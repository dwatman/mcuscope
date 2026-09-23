// api.js: live rows staged behind the first backfill. A staging overflow drops the oldest lines,
// and that hole is marked like any shed (divider, chart break) with the daemon's own staged
// notices kept. Also the shed notice's number handling and a divider under a clear-all.
// Driven through the real socket handlers, as in api_ws_gap.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

let backfill = [];
let hold = null;
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  let body = {};
  if (u.pathname === "/lines" && u.searchParams.get("match")) body = { lines: [] };
  else if (u.pathname === "/lines") {
    if (hold) await hold;
    body = { lines: [...backfill].reverse() };
  } else if (u.pathname === "/plot/channels") body = { channels: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { state, buffer, BUFFER_MAX, BUFFER_SLACK } = await import(webuiUrl("state.js"));
const { panes } = await import(webuiUrl("terminal.js"));
const { charts, clearAllCharts } = await import(webuiUrl("plots.js"));
const { clearAllDigital } = await import(webuiUrl("digital.js"));
const { connectWs } = await import(webuiUrl("api.js"));

let sock = null;
const p = (id) => makeRow(id, { chan: "event", raw: `!p ${id} a=${id}` });
const send = (...items) => sock.onmessage({ data: JSON.stringify(items) });
const shed = (n) => `gap: ${n} lines shed by the live stream`;
const dividers = () => buffer.filter((r) => r.chan === "gap");

async function open({ rows = [], parked = false } = {}) {
  buffer.length = 0; state.maxId = 0; state.anchorTs = null; state.anchorTick = null;
  clearAllCharts(); clearAllDigital();
  backfill = rows;
  let release = null;
  hold = parked ? new Promise((r) => { release = r; }) : null;
  panes.length = 0; panes.push(makePane({ autoscroll: true }));
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  if (!parked) for (let i = 0; i < 4; i++) await tick(0);
  return async () => { hold = null; release(); for (let i = 0; i < 6; i++) await tick(0); };
}

// Ids lo..hi as live rows, in frames of 500.
function stream(lo, hi) {
  for (let id = lo; id <= hi; id += 500) {
    send(...Array.from({ length: Math.min(500, hi - id + 1) }, (_, i) => p(id + i)));
  }
}

test("a staging overflow marks its hole and keeps a staged shed notice", async () => {
  const release = await open({ parked: true, rows: [p(1), p(2), p(3)] });
  send(p(4), p(5), p(6), { gap: 5 });   // ids 7-11 shed by the daemon
  const last = 12 + BUFFER_MAX + BUFFER_SLACK;
  stream(12, last);
  await release();

  const d = dividers();
  assert.equal(d.length, 1, `dividers: ${d.map((r) => r.raw)}`);
  const at = buffer.indexOf(d[0]);
  const next = buffer[at + 1].id;
  assert.ok(next > 12, "setup: the staging area did not overflow");
  assert.deepEqual(buffer.slice(0, at).map((r) => r.id), [1, 2, 3]);
  // Rows 4..next-1 are missing: 3 staged and dropped, 5 shed by the daemon, the rest dropped.
  assert.equal(d[0].raw, shed(next - 4), "the daemon's staged notice was lost with the rows");
  assert.equal(buffer.at(-1).id, last);
  const ys = charts.get("p1|adhoc").ys.get("a");
  assert.deepEqual(ys.slice(0, 5), [1, 2, 3, null, next], "the chart held its level across the hole");
});

test("a dropped run the backfill fetched counts only what the page still lacks", async () => {
  const fetched = Array.from({ length: 150 }, (_, i) => p(i + 1));
  const release = await open({ parked: true, rows: fetched });
  stream(4, 4 + BUFFER_MAX + BUFFER_SLACK);
  await release();
  const d = dividers();
  assert.equal(d.length, 1);
  const next = buffer[buffer.indexOf(d[0]) + 1].id;
  assert.ok(next > 151, "setup: the dropped run must reach past the backfill");
  assert.equal(d[0].raw, shed(next - 1 - 150), "rows the backfill holds were counted as missing");
});

test("a notice whose count is not a positive integer adds nothing", async () => {
  await open();
  send(p(1), p(2), p(3), p(4), p(5));
  // Ids 6-10 missing: 2 shed, 3 lost to failed commits (SPEC 3.2), so the notice sets the count.
  send({ gap: "5" }, { gap: -3 }, { gap: 0 }, { gap: 2 }, p(11));
  assert.deepEqual(dividers().map((r) => r.raw), [shed(2)]);
});

test("a divider staged under a clear-all does not become the relative-time zero", async () => {
  const release = await open({ parked: true, rows: [p(1), p(2), p(3)] });
  send({ gap: 8 }, p(12), p(13));
  // Clear all while the rows are staged: it re-zeroes relative time from the click (SPEC 9.1).
  state.anchorTs = null; state.anchorTick = null;
  clearAllCharts();
  await release();
  assert.equal(dividers().length, 1, "setup: the divider was not drawn");
  assert.equal(state.anchorTs, null, "a row the clear covers became the relative zero");
});
