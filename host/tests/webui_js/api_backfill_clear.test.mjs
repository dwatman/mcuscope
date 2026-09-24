// api.js: a clear clicked while the first-connect backfill is out is honoured. The backfill's
// rows were all captured before the click, so a cleared pane, the CAN table and
// the charts must not refill with them when it lands. Real createPane and real clear buttons.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl, tick } from "./dom_stub.mjs";
import { ALL_CHANS } from "../../mcuscope/webui/pane.js";

const env = installDom();
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

const BACKFILL = [
  makeRow(4),
  makeRow(3, { chan: "event", raw: "!can 1 - 321 00" }),
  makeRow(2, { chan: "event", raw: "!ps 0 3E8 0064" }),
  makeRow(1, { chan: "event", raw: "!pd 0 v:u2" }),
];
let release = null;
globalThis.fetch = async (url) => {
  const u = String(url);
  const ok = (b) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => b });
  if (u.startsWith("/lines?order=desc&limit=200")) {
    await new Promise((r) => { release = r; });
    return ok({ lines: BACKFILL, truncated: false });
  }
  return ok({ lines: [], channels: [], truncated: false });
};

env.localStorage.setItem("termState", JSON.stringify({ timeMode: "host", panes: [
  { port: "all", channels: ALL_CHANS, regex: "" },
  { port: "all", channels: ALL_CHANS, regex: "" },
] }));

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const { canRows, initCan } = await import(webuiUrl("can.js"));
const { charts, clearAllCharts } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));
T.initTerminal();
initCan();
const [a, b] = T.panes;

// A fresh page: nothing held anywhere, the backfill in flight.
async function backfillOut() {
  state.maxId = 0;
  buffer.length = 0;
  clearAllCharts();
  env.byId("canClear").emit("click", {});
  for (const p of T.panes) { p.clearId = 0; p.rows = []; }
  release = null;
  connectWs();
  env.sockets.at(-1).onopen();
  for (let i = 0; i < 5 && !release; i++) await tick(0);
  assert.ok(release, "the backfill never went out");
}
async function land() {
  release();
  await tick(30);
}
const ids = (p) => p.rows.map((r) => r.id);

test("control: a backfill nothing interfered with fills panes, CAN and charts", async () => {
  await backfillOut();
  await land();
  assert.deepEqual(ids(a), [1, 2, 3, 4]);
  assert.equal(canRows.size, 1);
  assert.equal(charts.size, 1);
});

test("clear-all during the backfill: panes and charts stay empty, CAN (not cleared) fills", async () => {
  await backfillOut();
  env.byId("clearAllBtn").emit("click");
  await land();
  assert.deepEqual(ids(a), [], "a cleared pane refilled with rows captured before the clear");
  assert.deepEqual(ids(b), []);
  assert.equal(charts.size, 0, "the charts refilled after clear-all");
  assert.equal(canRows.size, 1, "the CAN table was not cleared and must still fill");
  assert.equal(buffer.length, 4, "the rows still reach the shared buffer");
});

test("one pane cleared during the backfill: only that pane stays empty", async () => {
  await backfillOut();
  a.el.querySelector(".clear").emit("click");
  await land();
  assert.deepEqual(ids(a), [], "the cleared pane refilled");
  assert.deepEqual(ids(b), [1, 2, 3, 4], "a pane nobody cleared lost the backfill");
  assert.equal(charts.size, 1);
});

test("the CAN clear during the backfill: the table stays empty, panes and charts fill", async () => {
  await backfillOut();
  env.byId("canClear").emit("click", {});
  await land();
  assert.equal(canRows.size, 0, "the CAN table refilled after its clear");
  assert.deepEqual(ids(a), [1, 2, 3, 4]);
  assert.equal(charts.size, 1);
});
