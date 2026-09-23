// can.js: each !can line is parsed once. pushBuffer asks for the line's tick (state.js lineTick,
// through hooks.canTick) and canIngest then parses the same row: two full parses per frame on a
// busy bus. The last parse is kept, keyed by the raw text (plots.js decodeOnce does the same).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { pushBuffer, lineTick } = await import(webuiUrl("state.js"));
const { canIngest, canRows, clearAllCan } = await import(webuiUrl("can.js"));

let nextId = 1;
const row = (raw) => ({ id: nextId++, ts: 1000 + nextId / 100, port: "p1", chan: "event", raw });

test("a live !can row is parsed once for its tick and its frame together", () => {
  clearAllCan();
  let parses = 0;
  const realInt = globalThis.parseInt;   // parseCanEvent reads the id through it
  globalThis.parseInt = (...a) => { parses += 1; return realInt(...a); };
  try {
    const r = row("!can 500 - 123 DEADBEEF");
    pushBuffer(r); canIngest(r);   // what api.js routeLiveRow does with a live row
    assert.equal(lineTick(r), 500, "setup: the tick was not read");
    assert.equal(canRows.get("p1|1|s291").hex, "DEADBEEF", "setup: the frame did not land");
    assert.equal(parses, 1, `parsed ${parses} times`);
    const r2 = row("!can 501 - 123 00");
    pushBuffer(r2); canIngest(r2);
    assert.equal(canRows.get("p1|1|s291").hex, "00", "a stale parse was reused for new text");
  } finally {
    globalThis.parseInt = realInt;
  }
});
