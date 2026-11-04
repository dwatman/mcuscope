// terminal.js buildLine and pane.js gapTitle: a gap divider's tooltip says where the lines it
// names went and how to read them. It used to repeat the divider's own text, so "gap: 4000
// lines not loaded" left the reader to guess whether those lines were lost.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
await import(webuiUrl("state.js"));
const { render } = await import(webuiUrl("terminal.js"));
const { gapRow, gapTitle } = await import(webuiUrl("pane.js"));

function dividerTitle(row) {
  const pane = makePane();
  pane.rows = [row];
  render(pane);
  return pane.vlist.children[0].children[1].title;
}

test("a reconnect gap says its lines are in the capture and how to load them", () => {
  const g = gapRow(makeRow(5001), 4000);
  const t = gapTitle(g);
  assert.match(t, /^4000 lines /, "the count leads, as on the divider");
  assert.match(t, /in the capture/);
  assert.match(t, /scroll(ing)? to the top/i, "the recovery the pane offers");
  assert.match(t, /export/i);
  assert.equal(dividerTitle(g), t, "the rendered divider carries it");
});

test("a shed gap says the page fell behind, and that the capture still holds the lines", () => {
  const g = gapRow(makeRow(900), 12, "shed by the live stream");
  const t = gapTitle(g);
  assert.match(t, /^12 lines /);
  assert.match(t, /fell behind/);
  assert.match(t, /in the capture/);
  // Scrolling up does not fill a hole in the middle of the pane, so it must not promise that.
  assert.doesNotMatch(t, /scroll/i);
  assert.equal(dividerTitle(g), t);
});

test("one line is singular, and a marker divider keeps its own text as its title", () => {
  assert.match(gapTitle(gapRow(makeRow(10), 1)), /^1 line /);
  assert.equal(gapRow(makeRow(10), 1).raw, "gap: 1 line not loaded", "not \"1 lines\"");
  assert.equal(gapRow(makeRow(10), 2).raw, "gap: 2 lines not loaded");
  const m = makeRow(3, { chan: "marker", raw: "!m boot done" });
  assert.equal(dividerTitle(m), "marker: boot done");
  // A row gapRow did not build has no hole to count: its own text stands.
  assert.equal(gapTitle({ id: 4, chan: "gap", raw: "gap: 3 lines not loaded" }),
    "gap: 3 lines not loaded");
});
