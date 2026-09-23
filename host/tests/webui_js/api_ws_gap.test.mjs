// api.js: rows the daemon shed for this page ({"gap": n} ahead of the next row, SPEC 3.4) are a
// hole, and every surface must show one: a divider in the panes, a null break in every chart
// and lane. Without it the terminal joined the rows either side of the hole and the charts drew
// the last level before it held across the whole span, a trace nobody measured.
//
// Driven through the real socket handlers, as the daemon sends it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

// The first-connect backfill and the seeds; `hold` parks the /lines fetch so rows stage.
let backfill = [];
let hold = null;
let pages = null;   // reconnect paging: (sinceId, idTo, limit) -> {lines, truncated}
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  let body = {};
  if (u.pathname === "/lines" && u.searchParams.get("match")) body = { lines: [] };
  else if (u.pathname === "/lines" && u.searchParams.has("since_id") && pages) {
    const q = u.searchParams;
    body = pages(+q.get("since_id"), q.has("id_to") ? +q.get("id_to") : null, +q.get("limit"));
  } else if (u.pathname === "/lines") {
    if (hold) await hold;
    body = { lines: [...backfill].reverse() };
  } else if (u.pathname === "/plot/channels") body = { channels: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes } = await import(webuiUrl("terminal.js"));
const { charts, clearAllCharts } = await import(webuiUrl("plots.js"));
const { digitalLanes, clearAllDigital } = await import(webuiUrl("digital.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const SHED = "gap: 5 lines shed by the live stream";
let sock = null;
let pane = null;

const ev = (id, raw) => makeRow(id, { chan: "event", raw });
const send = (...items) => sock.onmessage({ data: JSON.stringify(items) });

async function open({ rows = [], parked = false } = {}) {
  buffer.length = 0; state.maxId = 0;
  clearAllCharts(); clearAllDigital();
  backfill = rows; pages = null;
  let release = null;
  hold = parked ? new Promise((r) => { release = r; }) : null;
  pane = makePane({ autoscroll: true });
  panes.length = 0; panes.push(pane);
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  if (!parked) for (let i = 0; i < 4; i++) await tick(0);
  return async () => { hold = null; release(); for (let i = 0; i < 6; i++) await tick(0); };
}

function traceRows() {
  return [
    ev(1, "!pd 0 st:u1:=0=OFF,1=RUN"),
    ev(2, "!p 2 a=2"), ev(3, "!ps 0 3 00"),
    ev(4, "!p 4 a=4"), ev(5, "!ps 0 5 01"),
  ];
}

test("a shed notice puts a divider ahead of the next row and breaks every chart and lane", async () => {
  await open();
  send(...traceRows());
  send({ gap: 5 }, ev(11, "!p 11 a=11"), ev(12, "!ps 0 C 01"));

  const at = buffer.findIndex((r) => r.chan === "gap");
  assert.ok(at >= 0, "no divider in the shared buffer");
  assert.equal(buffer[at].raw, SHED);
  assert.equal(buffer[at].id, 10, "the divider sorts just below the row after the hole");
  assert.equal(buffer[at + 1].id, 11);
  const q = pane.queue.map((r) => r.raw);
  assert.equal(q[q.indexOf(SHED) + 1], "!p 11 a=11", "the pane must get the divider ahead of the row");

  const chart = charts.get("p1|adhoc");
  assert.deepEqual(chart.ys.get("a"), [2, 4, null, 11], "the chart held its level across the hole");
  assert.deepEqual(chart.ids, [2, 4, null, 11]);
  const lane = digitalLanes.get("p1|st");
  assert.deepEqual(lane.vs, [0, 1, null, 1], "the lane held its level across the hole");
});

test("a notice with nothing actually missing marks nothing", async () => {
  await open();
  send(...traceRows());
  send({ gap: 3 }, ev(6, "!p 6 a=6"));   // ids run on: nothing this page lacks
  assert.equal(buffer.some((r) => r.chan === "gap"), false);
  assert.deepEqual(charts.get("p1|adhoc").ys.get("a"), [2, 4, 6]);
});

test("a notice staged while the backfill loads stays ahead of the row after the hole", async () => {
  const release = await open({ parked: true });
  send(ev(1, "!p 1 a=1"), ev(2, "!p 2 a=2"), ev(3, "!p 3 a=3"));
  send({ gap: 5 }, ev(9, "!p 9 a=9"), ev(10, "!p 10 a=10"));
  await release();
  const ids = buffer.map((r) => r.id);
  const at = buffer.findIndex((r) => r.chan === "gap");
  assert.ok(at >= 0, "the staged notice was lost (a sort carried it ahead of the first row)");
  assert.equal(buffer[at].id, 8, `divider in the wrong place: ${ids}`);
  assert.deepEqual(charts.get("p1|adhoc").ys.get("a"), [1, 2, 3, null, 9, 10]);
});

test("rows the backfill fetched are no hole, whatever the stream shed meanwhile", async () => {
  const release = await open({ parked: true, rows: [] });
  backfill = Array.from({ length: 9 }, (_, i) => makeRow(i + 1));
  send(makeRow(5), makeRow(6), { gap: 3 }, makeRow(10));
  await release();
  assert.deepEqual(buffer.map((r) => r.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
});

test("a notice from a replaced socket is not applied to the next one", async () => {
  await open();
  send(...traceRows());
  send({ gap: 4 });   // the socket drops before the row after the hole arrives
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  pages = () => ({ lines: [], truncated: false });
  for (let i = 0; i < 6; i++) await tick(0);
  send(ev(9, "!p 9 a=9"));
  assert.equal(buffer.some((r) => r.chan === "gap"), false,
    "the old socket's notice marked a hole in the new socket's stream");
});

test("a reconnect backfill too long to fetch breaks the charts at its divider", async () => {
  await open();
  send(...traceRows());
  // The reconnect: 8000 rows since the watermark, served newest first 1000 at a time.
  const newest = state.maxId + 8000;
  pages = (sinceId, idTo, limit) => {
    const top = idTo === null ? newest : idTo;
    const lines = [];
    for (let id = top; id > sinceId && lines.length < limit; id--) lines.push(makeRow(id));
    return { lines, truncated: lines.length === limit && lines.at(-1).id > sinceId + 1 };
  };
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  for (let i = 0; i < 20; i++) await tick(0);
  assert.ok(buffer.some((r) => r.chan === "gap" && /lines not loaded$/.test(r.raw)),
    "setup: the backfill did not leave its divider");
  assert.deepEqual(charts.get("p1|adhoc").ys.get("a"), [2, 4, null]);
  assert.deepEqual(digitalLanes.get("p1|st").vs, [0, 1, null]);
});
