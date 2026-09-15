// A paused terminal pane must stay frozen, including across the paths that rebuild it.
//
// rebuild() re-derives a pane's rows from the shared buffer, and two sibling callers run it on
// EVERY pane unconditionally: the end of every runBackfill (so every WS open and reconnect)
// and the high-rate release. Both therefore folded in everything that had arrived since the
// pause, while the pill still read "paused" - a pane that silently un-paused itself.
// plots.js:298 took this care for a paused chart (frozenLen); this is the terminal sibling.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

let served = [];
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ lines: served }) });

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes, rebuild, setAutoscroll } = await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const pane = makePane();
panes.push(pane);
const rows = (ids) => ids.map((i) => makeRow(i));

// A live, unfiltered pane over a buffer holding exactly `ids`.
function livePane(ids) {
  setAutoscroll(pane, true);
  pane.regex = null;
  buffer.length = 0;
  buffer.push(...rows(ids));
  state.maxId = ids.at(-1);
  rebuild(pane);
}

// Paused at row 3, then rows 4..6 arrived (counted, not folded in).
function frozenAt3WithNewer() {
  livePane([1, 2, 3]);
  setAutoscroll(pane, false);
  buffer.push(...rows([4, 5, 6]));
  state.maxId = 6;
  pane.pending = 3;
}

test("pausing records the row the pane is frozen at", async () => {
  livePane([1, 2, 3]);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3]);

  setAutoscroll(pane, false);
  assert.equal(pane.frozenId, 3, "a paused pane must remember where it froze");
  assert.equal(pane.pill.textContent, "paused");
});

test("a WS reconnect's backfill does not un-freeze the pane", async () => {
  livePane([1, 2, 3]);
  setAutoscroll(pane, false);
  served = [6, 5, 4].map((i) => makeRow(i));   // newest first, as /lines?order=desc serves
  connectWs();
  env.sockets.at(-1).onopen();
  await tick(20);

  assert.deepEqual(buffer.map((r) => r.id), [1, 2, 3, 4, 5, 6],
    "the backfill must still fill the shared buffer");
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3],
    "the frozen pane grew: it un-paused itself while the pill still read paused");
  assert.equal(pane.pill.textContent, "paused");
});

test("the frozen pane still re-filters when its filter changes", () => {
  frozenAt3WithNewer();
  pane.regex = /line (2|5)/;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [2],
    "a filter change must apply, but only within the frozen window");
  pane.regex = null;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3]);
});

test("rebuild leaves a frozen pane's 'N new' count alone", () => {
  frozenAt3WithNewer();
  pane.pending = 3;
  rebuild(pane);
  assert.equal(pane.pending, 3, "nothing was folded in, so the backlog counter still stands");
  assert.equal(pane.jumpBtn.textContent, "↓ 3 new");
});

test("resuming folds in everything that arrived while frozen", () => {
  frozenAt3WithNewer();
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3], "the pane must start out frozen");
  setAutoscroll(pane, true);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5, 6]);
  assert.equal(pane.pending, 0);
  assert.equal(pane.pill.textContent, "live");
});

test("the frozen pane survives the shared buffer rotating past its freeze", () => {
  // The buffer is a ring holding the newest BUFFER_MAX rows, so at any capture rate the rows
  // behind the freeze eventually fall out of it. rebuild() re-derived from that buffer, so
  // once every row left in it sat past frozenId, editing the filter emptied the pane - and
  // clearing the filter could not bring it back, because the rows were simply gone.
  livePane([1, 2, 3, 4, 5, 6]);
  setAutoscroll(pane, false);                        // frozen at id 6, holding rows 1..6
  buffer.length = 0;
  buffer.push(...[7, 8, 9].map((i) => makeRow(i)));  // the whole frozen window has rotated out
  state.maxId = 9;

  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5, 6],
    "the frozen pane went blank: rebuild read a buffer that no longer holds its rows");

  pane.regex = /line 2/;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [2], "filtering still applies within the freeze");
  pane.regex = null;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5, 6],
    "clearing the filter must bring the frozen rows back");
});

test("resuming drops the snapshot and returns the pane to the live buffer", () => {
  livePane([1, 2, 3, 4, 5, 6]);
  setAutoscroll(pane, false);
  buffer.length = 0;
  buffer.push(...rows([7, 8, 9]));   // the frozen window has rotated out
  state.maxId = 9;
  assert.notEqual(pane.frozenRows, null, "the paused pane must hold a snapshot to drop");
  setAutoscroll(pane, true);
  assert.equal(pane.frozenRows, null, "a live pane must not keep filtering a stale snapshot");
  assert.deepEqual(pane.rows.map((r) => r.id), [7, 8, 9]);
});
