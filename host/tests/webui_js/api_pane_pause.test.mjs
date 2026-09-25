// Where a pause freezes a pane fed by the live stream (api.js feedPanes, terminal.js drawnTop):
// at the newest row its filter saw. Frozen at the newest row it drew, a pane with a sparse filter
// lost every row it had filtered out before the pause from a filter widened while paused; they
// counted as "N new" instead. Rows the high-rate shed never fed it still count as new.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();
let snap = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  const body = u.startsWith("/lines?order=desc") ? { lines: [...snap].reverse() } : { lines: [], channels: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes, rebuild, setAutoscroll } = await import(webuiUrl("terminal.js"));
const { pauseAll } = await import(webuiUrl("freeze.js"));
const before = env.intervals.length;
const { connectWs } = await import(webuiUrl("api.js"));
const rateTick = env.intervals.slice(before)[0].fn;   // api.js's one module interval: the rate window

let sock = null;
let pane = null;
const ids = () => pane.rows.map((r) => r.id);
const send = (rows) => sock.onmessage({ data: JSON.stringify(rows) });
const markerOnly = () => { pane.channels = new Set(["marker"]); rebuild(pane); };
const widen = () => { pane.channels = new Set(["marker", "debug"]); rebuild(pane); };
const flushed = () => tick(60);

// A fresh connection on an empty capture, one live pane, the backfill settled and no shed.
async function stream() {
  buffer.length = 0;
  state.maxId = 0;
  snap = [];
  panes.length = 0;
  pane = makePane();
  panes.push(pane);
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  for (let i = 0; i < 4; i++) await tick(0);
  rateTick(); rateTick();   // close any window an earlier test left open; a quiet one releases a shed
}

function arrive(from, to) {
  const rows = [];
  for (let id = from; id <= to; id++) rows.push(makeRow(id));
  send(rows);
}

test("a sparse filter paused after rows it skipped keeps them: widened, it shows them", async () => {
  await stream();
  markerOnly();
  send([makeRow(1, { chan: "marker", raw: "!m x" })]);
  arrive(2, 10);
  await flushed();
  assert.deepEqual(ids(), [1], "setup: the filter matched only the marker");
  setAutoscroll(pane, false);
  widen();
  assert.deepEqual(ids(), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "rows fed and filtered before the pause dropped out of the frozen view");
  assert.equal(pane.pending, 0);
});

test("rows the high-rate shed never fed to the pane still count as new", async () => {
  await stream();
  markerOnly();
  send([makeRow(1, { chan: "marker", raw: "!m x" })]);
  await flushed();
  arrive(2, 2600);
  rateTick();   // HIGH_RATE_ON: from here the panes are not fed
  arrive(2601, 2700);
  setAutoscroll(pane, false);
  assert.equal(pane.frozenId, 2600, "the pause froze past rows the pane's filter never saw");
  widen();
  assert.equal(pane.rows.at(-1).id, 2600);
  assert.equal(pane.pending, 100);
});

test("a pause between a capture reset and its re-seed counts the new capture as new", async () => {
  await stream();
  send([{ capture: "A" }]);
  arrive(1, 50);
  await flushed();
  assert.equal(pane.rows.length, 50, "setup: the first capture did not load");
  snap = [1, 2, 3].map((id) => makeRow(id, { raw: `new ${id}` }));
  send([{ capture: "B" }]);
  setAutoscroll(pane, false);   // the re-seed is still out
  for (let i = 0; i < 6; i++) await tick(0);
  assert.deepEqual(ids(), [], "the new capture folded into a paused pane");
  assert.equal(pane.pending, 3, "the old capture's watermark hid the new capture's rows from the count");
});

test("pause-all leaves a pane already paused at its own freeze point and count", async () => {
  await stream();
  const other = makePane();
  panes.push(other);
  arrive(1, 10);
  await flushed();
  setAutoscroll(pane, false);
  arrive(11, 15);
  await flushed();
  assert.equal(pane.pending, 5, "setup: the paused pane did not count the rows after its pause");
  pauseAll(true);   // the other pane was live, so this pauses everything
  assert.equal(other.frozenId, 15, "setup: the live pane froze at its newest row");
  assert.equal(pane.frozenId, 10, "pause-all moved an already paused pane's freeze point");
  assert.equal(pane.pending, 5, "pause-all zeroed an already paused pane's backlog");
  rebuild(pane);
  assert.equal(pane.rows.at(-1).id, 10, "the rows after its own pause folded into its frozen view");
  pauseAll(false);
});
