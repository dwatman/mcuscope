// api.js routeLiveRow: what a pane is allowed to retain from the live stream.
//
// The other half of 4d7b4ef. A browser throttles a background tab's timers to about once a
// minute while rows keep arriving, so both pane queues had to be bounded:
//   - a PAUSED pane counts rows into `pending` and retains nothing (queueing them only to
//     length-count them later was unbounded retention that a VIEW_MAX trim could not touch,
//     because trimming would have corrupted the count);
//   - a LIVE pane keeps at most VIEW_MAX, since flush() renders no more than that anyway.
// Both are driven here through the real WS handlers rather than by calling the (private)
// routeLiveRow directly.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

// A backfill that succeeds and returns nothing, so staging drains and the live path is clear.
globalThis.fetch = async () => ({
  ok: true, status: 200,
  json: async () => ({ lines: [] }),
});

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes, VIEW_MAX } = await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const live = makePane({ autoscroll: true });
const paused = makePane({ autoscroll: false });
const filtered = makePane({ autoscroll: true, port: "other" });
panes.push(live, paused, filtered);

let sock = null;
let nextId = 1;

// Deliver rows the way the daemon does: one frame carrying an array (SPEC 3.4).
function feed(count) {
  const rows = [];
  for (let i = 0; i < count; i++) rows.push(makeRow(nextId++));
  sock.onmessage({ data: JSON.stringify(rows) });
  return rows;
}

test("open the stream and let the backfill settle", async () => {
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  assert.equal(state.maxId, 0);
});

test("a live pane keeps at most VIEW_MAX queued rows, newest first out", () => {
  const over = 120;
  feed(VIEW_MAX + over);
  assert.equal(live.queue.length, VIEW_MAX,
    "an unbounded queue keeps buffer-evicted rows alive in a throttled tab");
  assert.equal(live.queue[0].id, over + 1, "the trim must drop the OLDEST rows");
  assert.equal(live.queue.at(-1).id, VIEW_MAX + over);
  assert.equal(live.rows.length, 0, "nothing is rendered until the flush timer runs");
});

test("a paused pane counts rows without retaining any", () => {
  assert.equal(paused.queue.length, 0,
    "a paused pane must not retain rows: `pending` is all flush() reads");
  assert.equal(paused.pending, VIEW_MAX + 120, "every matching row must still be counted");
  assert.equal(paused.pendingDirty, true);
});

test("a pane whose filter excludes the rows gets neither", () => {
  assert.equal(filtered.queue.length, 0);
  assert.equal(filtered.pending, 0);
});

test("the flush trims the rendered set to VIEW_MAX and refreshes the jump button", async () => {
  await tick(60);
  assert.equal(live.queue.length, 0);
  assert.equal(live.rows.length, VIEW_MAX);
  assert.equal(live.rows.at(-1).id, nextId - 1);
  assert.equal(paused.rows.length, 0, "a paused pane does no DOM work at all");
  assert.equal(paused.jumpBtn.textContent, `↓ ${VIEW_MAX + 120} new`);
  assert.equal(paused.pendingDirty, false);
});

test("a further burst still cannot grow a paused pane's retention", () => {
  const before = paused.pending;
  feed(2000);
  assert.equal(paused.queue.length, 0);
  assert.equal(paused.pending, before + 2000);
  assert.ok(live.queue.length <= VIEW_MAX);
  // The shared buffer is separately capped (BUFFER_MAX + BUFFER_SLACK in state.js).
  assert.ok(buffer.length <= 5000 + 512, `shared buffer grew to ${buffer.length}`);
});
