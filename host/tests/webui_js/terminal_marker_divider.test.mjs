// terminal.js buildLine: a firmware marker's divider strips the `!m [@<tick>] ` wire prefix,
// split on spaces only (SPEC 2.1), with the tick word taken only when text follows it, as
// state.js lineTick and protocol.parse_marker read it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
await import(webuiUrl("state.js"));
const { render } = await import(webuiUrl("terminal.js"));

function divider(raw) {
  const pane = makePane();
  pane.rows = [makeRow(1, { chan: "marker", raw })];
  render(pane);
  return pane.vlist.children[0].children[1].textContent;
}

test("the wire prefix and the tick word are stripped from a marker divider", () => {
  assert.equal(divider("!m @12 boot done"), "marker: boot done");
  assert.equal(divider("!m   @12   boot"), "marker: boot", "runs of spaces separate as one");
  assert.equal(divider("!m plain text"), "marker: plain text");
  assert.equal(divider("!m @12"), "marker: @12", "with no text after it the word is the text");
  assert.equal(divider("!m @12\tx"), "marker: @12\tx", "a tab is part of the word");
});
