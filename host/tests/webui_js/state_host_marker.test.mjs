// A host marker (`mcu mark`, POST /marker) is stored with dir "-", and the daemon parses `!m`
// only on what the target sent (dir "rx"). state.js lineTick and terminal.js's divider read the
// same: a host marker whose text starts `!m @N` has no tick and keeps its text whole.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
const { state, lineTick, pushBuffer, buffer } = await import(webuiUrl("state.js"));
const { render } = await import(webuiUrl("terminal.js"));

let id = 1;
const marker = (raw, dir) => ({ id: id++, ts: 100, port: "p1", dir, chan: "marker", raw });

function divider(row) {
  const pane = makePane();
  pane.rows = [row];
  render(pane);
  return pane.vlist.children[0].children[1].textContent;
}

test("a host marker reading like a firmware one carries no tick", () => {
  assert.equal(lineTick(marker("!m @4000000000 hello", "-")), null);
  assert.equal(lineTick(marker("!m @5 x", "rx")), 5, "positive control: the target's marker");
});

test("a host marker's divider keeps the text as typed", () => {
  assert.equal(divider(marker("!m @4000000000 hello", "-")), "marker: !m @4000000000 hello");
  assert.equal(divider(marker("!m @5 x", "rx")), "marker: x", "positive control: the target's prefix goes");
});

test("a host marker does not set the tick zero every tick stamp reads", () => {
  buffer.length = 0;
  state.anchorTick = null;
  pushBuffer(makeRow(id++, { dir: "-", chan: "marker", raw: "!m @4000000000 hello" }));
  assert.equal(state.anchorTick, null);
  pushBuffer(makeRow(id++, { dir: "rx", chan: "marker", raw: "!m @7 boot" }));
  assert.equal(state.anchorTick, 7, "positive control: the target's marker sets it");
});
