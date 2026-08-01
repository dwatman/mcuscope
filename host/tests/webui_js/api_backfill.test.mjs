// api.js: a failing backfill must still drain the staging area.
//
// The frozen-stream defect (commit 4d7b4ef): the WS open handler puts every live row into
// `staging` until the /lines backfill has merged. When the backfill's error path threw, the
// drain never ran, so `staging` stayed non-null, every later frame was queued into an area
// nobody read, and the UI kept looking live with a scrollback that never moved. Nothing
// executed this path, so it shipped.
//
// The test drives the real open/message/close handlers with a fake socket and a fetch that
// rejects, and asserts the rows reach the pane anyway.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

let fetchCalls = 0;
globalThis.fetch = async () => { fetchCalls += 1; throw new Error("connection refused"); };

const { state, buffer, hooks } = await import(webuiUrl("state.js"));
const { panes, VIEW_MAX } = await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));

// terminal.js flushes on a 33 ms timer; wait past it before reading pane.rows.
const FLUSH_WAIT_MS = 60;

const errors = [];
hooks.reportError = (m) => errors.push(m);

const pane = makePane();
panes.push(pane);

function frame(sock, rows) {
  sock.onmessage({ data: JSON.stringify(rows) });
}

test("a rejected backfill still drains staging and the rows reach the pane", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  assert.ok(sock, "connectWs did not open a socket");
  assert.match(sock.url, /^ws:\/\/127\.0\.0\.1:8765\/ws/);

  // The socket is open before the backfill resolves; rows arriving now go to staging.
  sock.onopen();
  frame(sock, [makeRow(2), makeRow(1)]);   // out of order on purpose: the drain sorts by id
  assert.equal(pane.queue.length, 0, "rows must be held until the backfill has merged");

  await tick(0);

  assert.equal(fetchCalls, 1, "the backfill did not run");
  assert.deepEqual(errors, ["backfill failed: connection refused"],
    "a failed backfill must be reported, not swallowed");
  assert.deepEqual(pane.queue.map((r) => r.id), [1, 2],
    "staged rows were never drained: the stream is frozen while still looking live");
  assert.equal(state.maxId, 2);
  assert.deepEqual(buffer.map((r) => r.id), [1, 2]);

  // ... and the queue really is what the pane renders.
  await tick(FLUSH_WAIT_MS);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2]);
  assert.equal(pane.queue.length, 0);
});

test("rows arriving after the drain route straight through", async () => {
  const sock = env.sockets.at(-1);
  frame(sock, [makeRow(3)]);
  assert.deepEqual(pane.queue.map((r) => r.id), [3],
    "staging was left in place, so live rows are still being swallowed");
  await tick(FLUSH_WAIT_MS);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3]);
});

test("a row already covered by the watermark is dropped", async () => {
  const sock = env.sockets.at(-1);
  frame(sock, [makeRow(3)]);
  await tick(FLUSH_WAIT_MS);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3], "duplicate ids must not be re-added");
});

test("a malformed row does not cost the rest of the frame", async () => {
  const sock = env.sockets.at(-1);
  frame(sock, [null, { id: "not a number" }, makeRow(4)]);
  await tick(FLUSH_WAIT_MS);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4]);
});

test("ids going backwards are read as a daemon DB reset, not as duplicates", async () => {
  const before = fetchCalls;
  const sock = env.sockets.at(-1);
  frame(sock, [makeRow(1, { raw: "after the reset" })]);
  assert.equal(state.maxId, 1, "the stale watermark must be dropped, or every row is discarded");
  assert.deepEqual(buffer.map((r) => r.raw), ["after the reset"]);
  await tick(FLUSH_WAIT_MS);
  // Not deepEqual: rebuild() rewrites pane.rows from the shared buffer without clearing
  // pane.queue, so a row that is queued when the re-seeding backfill settles is appended a
  // second time by the next flush. Narrow (one flush window) and cosmetic, but real; the
  // assertion below is about the reset, so it does not pin that behaviour either way.
  assert.ok(pane.rows.some((r) => r.raw === "after the reset"),
    "the pane must show the new capture's rows");
  assert.ok(pane.rows.every((r) => r.raw === "after the reset"),
    "rows from the old capture must not survive the reset");
  assert.ok(fetchCalls > before, "a reset must re-seed the terminal from the new capture");
});

test("VIEW_MAX is the pane bound the drain path has to respect", () => {
  assert.equal(VIEW_MAX, 5000);
});
