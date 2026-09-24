// timewindow.js estimateTick (registry class 77): the terminal column reads a tick estimate
// crossing the 2^32 wrap as the board's clock would, wrapped.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const { tickAnchors } = await import(webuiUrl("state.js"));
const TW = await import(webuiUrl("timewindow.js"));

test("the terminal column still reads the wrapped estimate, as the board's clock would", () => {
  tickAnchors.clear();
  TW.noteTickAnchor(tickAnchors, "p1", 1, 100, 0xFFFFFF00);
  assert.equal(TW.estimateTick(tickAnchors, { id: 2, ts: 101, port: "p1" }), 744);
});
