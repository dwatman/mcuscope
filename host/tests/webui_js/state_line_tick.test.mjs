// state.js lineTick: which decoder is asked for a line's tick, and the marker tick rule.
//
// Tokens split on runs of U+0020 only (protocol.split_tokens, SPEC 2.1), so a tag followed by a
// tab is not that tag at all. The !can, !p and !ps ticks come from the decoders' own hooks, so a
// line a decoder rejects cannot set the sticky state.anchorTick. Driven with stand-in hooks that
// record what they were asked; state_decoder_tick.test.mjs drives the real decoders.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state, hooks, lineTick, pushBuffer } = await import(webuiUrl("state.js"));

let asked = [];
let answer = 500;
hooks.canTick = (raw) => { asked.push(["can", raw]); return answer; };
hooks.adhocTick = (raw) => { asked.push(["p", raw]); return answer; };
hooks.plotSampleTick = (port, raw) => { asked.push(["ps", raw]); return answer; };

let nextId = 1;
const evt = (raw) => ({ id: nextId++, ts: 100, port: "p1", chan: "event", raw });
const mark = (raw) => ({ id: nextId++, ts: 100, port: "p1", chan: "marker", raw });

function tickOf(row) {
  asked = [];
  return lineTick(row);
}

test("each event tag is asked of its own decoder, and only that one", () => {
  answer = 500;
  for (const [raw, hook] of [["!can 1 - 100 -", "can"], ["!can2 1 - 100 -", "can"],
                             ["!p 1 a=1", "p"], ["!ps 0 1 0001", "ps"],
                             ["!can  1 - 100 -", "can"]]) {
    assert.equal(tickOf(evt(raw)), 500, raw);
    assert.deepEqual(asked, [[hook, raw]], `${raw} went to the wrong decoder`);
  }
  for (const raw of ["!can0 1 - 100 -", "!canx 1 - 100 -", "!pd 0 a:u1", "!other 5", "!pp 1 a=1"]) {
    assert.equal(tickOf(evt(raw)), null, raw);
    assert.deepEqual(asked, [], `${raw} is no decoder's line`);
  }
});

test("a tag followed by a tab or another whitespace byte is not that tag", () => {
  answer = 500;
  for (const raw of ["!can\t100 - 100 DE", "!p\t9 a=1", "!ps\t0 3E8 0064", "!can\u001f100 - 100 DE",
                     "!p\u00a09 a=1"]) {
    assert.equal(tickOf(evt(raw)), null, JSON.stringify(raw));
    assert.deepEqual(asked, [], `${JSON.stringify(raw)} reached a decoder`);
  }
});

test("a decoder's refusal sets no anchor, and an out-of-range answer is refused here too", () => {
  answer = null;
  state.anchorTick = null;
  pushBuffer(evt("!can 4000000000 zz 100 -"));
  assert.equal(state.anchorTick, null, "a line the decoder rejects became the sticky anchor");
  answer = 0x100000000;
  assert.equal(tickOf(evt("!p 1 a=1")), null, "past the SPEC 2.5 32-bit range");
  answer = 42;
  pushBuffer(evt("!p 42 a=1"));
  assert.equal(state.anchorTick, 42, "positive control: an accepted line does anchor");
});

test("a marker's tick is the first space-separated word when all of it is @<digits>", () => {
  const cases = [
    ["!m @5 hello", 5],
    ["!m   @5   hello  ", 5, "runs of spaces collapse"],
    ["!m @5\thello", null, "the daemon reads `@5\\thello` as marker text with no tick"],
    ["!m \u001f@5 hi", null, "a unit separator is a token byte, not a separator"],
    ["!m\t@5 hi", null, "`!m\\t@5` is not the marker tag"],
    ["!m @5", null, "a tick with no text is not a marker"],
    ["!m @5x hi", null, "the whole word must be @<digits>"],
    ["!m hi @5", null, "only the first word"],
    ["!m @4294967295 late", 0xFFFFFFFF],
    ["!m @4294967296 late", null],
    ["!m @" + "0".repeat(21) + " x", null, "past the decimal digit cap"],
  ];
  for (const [raw, want, why] of cases) assert.equal(tickOf(mark(raw)), want, why || raw);
  assert.deepEqual(asked, [], "a marker is not asked of any event decoder");
});

test("an undecodable !ps line is asked again later, other misses are not", () => {
  answer = null;
  const ps = evt("!ps 0 3E8 0064");
  const can = evt("!can 1 zz 100 -");
  tickOf(ps); tickOf(can);
  answer = 1000;
  assert.equal(tickOf(ps), 1000, "its !pd may arrive after it");
  assert.equal(tickOf(can), null, "a rejected !can is rejected for good");
  assert.deepEqual(asked, [], "and is not decoded a second time");
});
