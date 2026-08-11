// api.js: the reconnect backfill has to page against the server's limit clamp.
//
// The defect: /lines clamps `limit` to 1000 rows (store.query_lines) and says so with
// `truncated` in the envelope, but the reconnect fetch asked for BUFFER_MAX (5000) and read
// the answer as if it had been served whole. A gap wider than 1000 lines therefore came back
// as its newest 1000 only: the pane showed the pre-gap buffer, then the live edge, and the
// missing middle was invisible - no hole in the scrollback, no notice, nothing.
//
// So: page it (order=desc, same since_id, id_to walking down) until the gap closes or the
// client's own row budget is spent, and when it is spent say so with a divider row.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

// A fake capture: ids 1..dbMax, served with the real /lines semantics (since_id exclusive,
// id_to inclusive, desc, limit clamped, `truncated` when more rows matched than were served).
const SERVER_CLAMP = 1000;
let dbMax = 0;
let queries = [];          // every /lines?since_id= request, in order
let gate = null;           // when set, the next paged answer waits on this promise

function serveLines(url) {
  const q = new URL(url, "http://x");
  const sinceId = Number(q.searchParams.get("since_id") || 0);
  const idTo = q.searchParams.has("id_to") ? Number(q.searchParams.get("id_to")) : dbMax;
  const limit = Math.min(Number(q.searchParams.get("limit") || 100), SERVER_CLAMP);
  const hi = Math.min(idTo, dbMax);
  const ids = [];
  for (let id = hi; id > sinceId && ids.length < limit + 1; id--) ids.push(id);
  const truncated = ids.length > limit;
  return { lines: ids.slice(0, limit).map((id) => makeRow(id)), truncated };
}

globalThis.fetch = async (url) => {
  let body;
  if (url.includes("/lines?match=")) body = { lines: [] };          // !pd definition seed
  else if (url.includes("/plot/channels")) body = { channels: [] };  // plot history seed
  else if (url.includes("/lines")) {
    queries.push(url);
    if (gate) await gate;
    body = url.includes("since_id=")
      ? serveLines(url)
      : { lines: [], truncated: false };   // first-connect seed; per-test bodies override
  } else body = {};
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes, rebuild } = await import(webuiUrl("terminal.js"));
const { connectWs, gapRow, planBackfillStep, BACKFILL_MAX, LINES_LIMIT_MAX } =
  await import(webuiUrl("api.js"));

const FLUSH_WAIT_MS = 60;   // terminal.js flushes on a 33 ms timer

const pane = makePane();
panes.push(pane);

// Start a reconnect whose watermark is `maxId` against a capture holding `max` lines, and
// wait for the backfill (and its rebuild) to settle.
async function reconnectAt(maxId, max) {
  buffer.length = 0;
  pane.rows = []; pane.queue.length = 0; pane.pending = 0; pane.clearId = 0;
  state.maxId = maxId;
  state.anchorTs = null; state.anchorTick = null;
  queries = [];
  dbMax = max;
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  await tick(FLUSH_WAIT_MS);
  return sock;
}

const ids = (rows) => rows.map((r) => r.id);
const gaps = (rows) => rows.filter((r) => r.chan === "gap");

test("a gap inside one page is one fetch and no divider", async () => {
  await reconnectAt(100, 500);
  assert.equal(queries.length, 1, "a gap the server can serve whole must not be paged");
  assert.match(queries[0], /since_id=100/);
  assert.deepEqual(gaps(buffer), [], "nothing is missing, so nothing may claim it is");
  assert.equal(buffer.length, 400);
  assert.deepEqual(ids(buffer).slice(0, 3), [101, 102, 103], "rows must land oldest-first");
  assert.equal(state.maxId, 500);
});

test("a gap wider than the server's clamp is paged, and every row lands in order", async () => {
  await reconnectAt(100, 2600);   // 2500 rows: three pages of 1000/1000/500
  assert.equal(queries.length, 3, `expected three pages, got ${queries.length}`);
  // Every page is anchored to the same watermark, and walks down by id_to.
  assert.ok(queries.every((u) => u.includes("since_id=100")),
    "the watermark must stay put, or a page re-fetches what the last one already had");
  assert.ok(!queries[0].includes("id_to="), "the first page has no upper bound");
  assert.match(queries[1], /id_to=1600\b/);   // oldest of page 1 (id 1601) minus one
  assert.match(queries[2], /id_to=600\b/);
  assert.deepEqual(gaps(buffer), [], "a closed gap must not be marked");
  assert.equal(buffer.length, 2500);
  const got = ids(buffer);
  assert.deepEqual([got[0], got.at(-1)], [101, 2600]);
  assert.ok(got.every((id, i) => id === 101 + i), "the paged rows are out of order or holed");
  assert.equal(state.maxId, 2600);
  // ... and the pane shows them, not just the buffer.
  assert.deepEqual([pane.rows[0].id, pane.rows.at(-1).id], [101, 2600]);
});

test("a gap past the row budget stops there and says so with a divider", async () => {
  await reconnectAt(100, 10000);   // 9900 rows missing: far past what the client can hold
  assert.equal(buffer.length, BACKFILL_MAX + 1, "budget + one divider is the whole buffer");
  const marks = gaps(buffer);
  assert.equal(marks.length, 1, "a gap the backfill could not close must be visible");
  const oldestLine = buffer.find((r) => r.chan !== "gap");
  assert.equal(marks[0].id, oldestLine.id - 1,
    "the divider must sort immediately ahead of the rows it precedes");
  assert.equal(marks[0].raw, `gap: ${oldestLine.id - 101} lines not loaded`);
  assert.equal(buffer.indexOf(marks[0]), 0);
  assert.equal(state.maxId, 10000, "the watermark still belongs to the newest row");
  // The divider is the first row the pane holds - the trim must not have eaten it.
  assert.equal(pane.rows[0], marks[0], "the pane dropped the very notice the gap needs");
});

test("rows arriving over the socket while the paging runs stay staged, then drain", async () => {
  let release;
  gate = new Promise((r) => { release = r; });
  buffer.length = 0;
  pane.rows = []; pane.queue.length = 0; pane.pending = 0; pane.clearId = 0;
  state.maxId = 100; state.anchorTs = null; state.anchorTick = null;
  queries = [];
  dbMax = 2600;
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  // The paging is parked on its first page. Live rows arriving now are ahead of the whole
  // fetch and must be held: merged straight in they would advance the watermark past the
  // history still in flight, and every row of it would then read as a duplicate.
  sock.onmessage({ data: JSON.stringify([makeRow(2602), makeRow(2601)]) });
  assert.equal(buffer.length, 0, "a live row landed while the backfill was still paging");
  assert.equal(state.maxId, 100);
  gate = null;
  release();
  await tick(FLUSH_WAIT_MS);
  assert.ok(queries.length >= 3, "the paging did not resume after the live rows arrived");
  const got = ids(buffer);
  assert.equal(got.length, 2502, "the staged rows are missing, or the paged ones are");
  assert.deepEqual([got[0], got.at(-1)], [101, 2602]);
  assert.ok(got.every((id, i) => id === 101 + i), "the drain landed the staged rows out of order");
  assert.deepEqual(gaps(buffer), []);
});

test("a capture reset re-seeds through the unchanged first-connect fetch", async () => {
  const sock = await reconnectAt(100, 2600);
  // The capture this page has been reading all along; a fresh page holds nothing to throw
  // away, so the first token it ever sees is adopted rather than treated as a change.
  sock.onmessage({ data: JSON.stringify([{ capture: "cap-one" }]) });
  const before = queries.length;
  // The daemon says the id space was replaced (SPEC 3.4): maxId drops to 0 and the re-seed
  // takes the single bounded first-connect fetch, not the paged reconnect one.
  dbMax = 5;
  sock.onmessage({ data: JSON.stringify([{ capture: "cap-two" }, makeRow(1, { raw: "fresh" })]) });
  assert.equal(state.maxId, 0, "the stale watermark must be dropped");
  await tick(FLUSH_WAIT_MS);
  const reseed = queries.slice(before);
  assert.equal(reseed.length, 1, "the re-seed must be one fetch, not a paged walk");
  assert.match(reseed[0], /order=desc&limit=200$/);
  assert.ok(!reseed[0].includes("since_id="), "a first connect has no watermark to page from");
  assert.deepEqual(gaps(buffer), [], "a reset is not a gap");
  assert.ok(buffer.some((r) => r.raw === "fresh"), "the staged post-reset row never landed");
});

test("the gap row renders as a divider, and no filter can hide it", async () => {
  await reconnectAt(100, 120);           // a short window, so the whole pane is rendered
  const mark = gapRow(buffer[0], 42);
  buffer.unshift(mark);
  // The strictest filter a pane can carry: another port, one channel, and a pattern nothing
  // here matches. A gap is still a gap in this pane's view of the capture.
  pane.port = "somewhere-else";
  pane.channels = new Set(["cmd"]);
  rebuild(pane);
  assert.deepEqual(pane.rows, [mark], "the divider must survive every pane filter");
  const el = pane.vlist.children.find((c) => c.className.split(" ").includes("gap"));
  assert.ok(el, "the gap row rendered as an ordinary line, not as a divider");
  assert.ok(el.className.split(" ").includes("marker"),
    "the divider must keep the marker classes it takes its styling from");
  const div = el.querySelector(".divider");
  assert.equal(div.textContent, "gap: 42 lines not loaded");
});

// ---- the paging decision itself, driven directly ------------------------------------

test("planBackfillStep: a full answer ends the walk even with ids missing below it", () => {
  // Not truncated and short of the limit: the ids between the watermark and the oldest row
  // are simply not in the table (pruned, or another session's window), so nothing is missing.
  const step = planBackfillStep({ sinceId: 10, collected: 5, limit: 1000, pageLen: 5,
                                  truncated: false, oldest: 900 });
  assert.deepEqual(step, { idTo: null, gap: 0 });
});

test("planBackfillStep: a clamped page asks for the next one below it", () => {
  const step = planBackfillStep({ sinceId: 10, collected: 1000, limit: LINES_LIMIT_MAX,
                                  pageLen: 1000, truncated: true, oldest: 4000 });
  assert.deepEqual(step, { idTo: 3999, gap: 0 });
});

test("planBackfillStep: the budget ends the walk and reports what is left", () => {
  const step = planBackfillStep({ sinceId: 10, collected: BACKFILL_MAX, limit: LINES_LIMIT_MAX,
                                  pageLen: 1000, truncated: true, oldest: 9000 });
  assert.deepEqual(step, { idTo: null, gap: 8989 });
});

test("planBackfillStep: reaching the watermark ends the walk with nothing missing", () => {
  const step = planBackfillStep({ sinceId: 10, collected: 1000, limit: LINES_LIMIT_MAX,
                                  pageLen: 1000, truncated: true, oldest: 11 });
  assert.deepEqual(step, { idTo: null, gap: 0 });
});

test("planBackfillStep: an empty page ends the walk", () => {
  const step = planBackfillStep({ sinceId: 10, collected: 0, limit: 1000, pageLen: 0,
                                  truncated: false, oldest: null });
  assert.deepEqual(step, { idTo: null, gap: 0 });
});
