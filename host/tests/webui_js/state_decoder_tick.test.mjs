// state.js lineTick against the real !can and !p decoders (can.js, plots.js).
//
// A line the decoder rejects must carry no tick: lineTick sets the sticky state.anchorTick,
// which every terminal tick and the tick-base chart zero read against until clear-all.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makeRow } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state, lineTick, pushBuffer } = await import(webuiUrl("state.js"));
await import(webuiUrl("can.js"));
await import(webuiUrl("plots.js"));

let nextId = 1;
const evt = (raw) => makeRow(nextId++, { chan: "event", port: "p1", raw });

test("a line either decoder rejects carries no tick and sets no anchor", () => {
  for (const raw of ["!can 4000000000 zz 100 -", "!can 5 - 800 DE", "!can 5 - 100 D",
                     "!p 7 a=1\u001fb=2", "!p 7", "!p 7 a=1 a=2"]) {
    state.anchorTick = null;
    pushBuffer(evt(raw));
    assert.equal(state.anchorTick, null, `${JSON.stringify(raw)} became the sticky anchor`);
  }
});

test("a line the decoder accepts carries its tick", () => {
  assert.equal(lineTick(evt("!can 100 - 100 DE")), 100);
  assert.equal(lineTick(evt("!can2 101 x 1FFFFFFF -")), 101);
  assert.equal(lineTick(evt("!p 9 a=1")), 9);
});
