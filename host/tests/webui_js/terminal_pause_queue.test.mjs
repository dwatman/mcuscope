// terminal.js setAutoscroll: a pane paused between an arrival and the flush that would draw it
// freezes at the newest row its filter saw, drawn or not. Freezing at state.maxId took in the
// rows still queued (or, above the high-rate threshold, never fed), and the next rebuild drew
// them: the paused pane grew while its pill read "paused". Freezing at the newest row drawn
// dropped the rows a sparse filter had skipped from a filter widened while paused.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
const { state, buffer } = await import(webuiUrl("state.js"));
const { rebuild, setAutoscroll } = await import(webuiUrl("terminal.js"));

function drawnThenArrived(queue) {
  buffer.length = 0;
  for (let id = 1; id <= 10; id++) buffer.push(makeRow(id));
  state.maxId = 10;
  const pane = makePane();
  rebuild(pane);   // live: rows 1-10 drawn
  for (let id = 11; id <= 13; id++) {
    const row = makeRow(id);
    buffer.push(row);
    if (queue) pane.queue.push(row);   // api.js feedPanes, before the next flush
  }
  state.maxId = 13;
  return pane;
}

test("rows queued for the next flush count as new, and a rebuild does not draw them", () => {
  const pane = drawnThenArrived(true);
  setAutoscroll(pane, false);
  rebuild(pane);   // a filter change, a backfill's end, the high-rate release
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(pane.pending, 3);
});

test("rows the high-rate guard never fed count as new too", () => {
  const pane = drawnThenArrived(false);
  setAutoscroll(pane, false);
  rebuild(pane);
  assert.equal(pane.rows.at(-1).id, 10);
  assert.equal(pane.pending, 3);
});

test("resuming draws them", () => {
  const pane = drawnThenArrived(true);
  setAutoscroll(pane, false);
  setAutoscroll(pane, true);
  assert.equal(pane.rows.at(-1).id, 13);
});

test("a pane that drew nothing freezes below its queue, or at the newest row it filtered", () => {
  const queued = drawnThenArrived(true);
  queued.rows = [];   // its filter matched nothing before rows 11-13
  setAutoscroll(queued, false);
  assert.equal(queued.frozenId, 10);
  const idle = drawnThenArrived(false);
  idle.rows = [];
  setAutoscroll(idle, false);
  assert.equal(idle.frozenId, 10, "rows 11-13 were never fed to it: they are new, not frozen in");
});

test("a pane rebuilt over rows its filter skips freezes past them; widened, it shows them", () => {
  buffer.length = 0;
  buffer.push(makeRow(1, { chan: "marker", raw: "!m x" }));
  for (let id = 2; id <= 10; id++) buffer.push(makeRow(id));
  state.maxId = 10;
  const pane = makePane();
  pane.channels = new Set(["marker"]);
  rebuild(pane);   // live: only row 1 matches, rows 2-10 were read and filtered out
  assert.deepEqual(pane.rows.map((r) => r.id), [1], "setup");
  setAutoscroll(pane, false);
  pane.channels = new Set(["marker", "debug"]);
  rebuild(pane);   // the filter widened while paused
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "the pause froze at the last match, dropping rows that arrived before it");
  assert.equal(pane.pending, 0);
});
