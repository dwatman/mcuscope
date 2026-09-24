// plots.js: a paused chart's shown window in tick mode is the samples it draws (FW-2).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const F = await import(webuiUrl("freeze.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

let nextId = 0;
function ingest(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  const r = { id: nextId, ts, port, chan: "event", raw };
  P.plotIngest(r);
  return r;
}

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

// ---- FW-2: tick mode's shown window is the samples drawn -----------------------------------

// The daemon's selection over `rows`: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to,
// each bound only when given.
function selected(rows, q) {
  const bound = (k, dflt) => (q.has(k) ? Number(q.get(k)) : dflt);
  const s = bound("since_ts", -Infinity), u = bound("until_ts", Infinity);
  const sinceId = bound("since_id", -Infinity), idTo = bound("id_to", Infinity);
  return rows.filter((r) => r.ts > s && r.ts <= u && r.id > sinceId && r.id <= idTo).map((r) => r.id);
}

// 100 Hz by the MCU clock, which runs 1 percent slow against host time (an STM32 on HSI), plus
// any latency `lag(i)` adds.
function hsiStream(sid, n, value, lag = () => 0) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const tick = 10000 + i * 10;
    rows.push({ ...ingest(`!ps ${sid} ${tick.toString(16)} ${value(i)}`, 500 + i * 0.0101 + lag(i)), tick });
  }
  return rows;
}

test("FW-2: a paused chart in tick mode exports exactly the samples it draws", async () => {
  F.pauseAll(false);
  P.clearAllCharts();
  ingest("!pd 1 v:u2", 1);
  const rows = hsiStream(1, 4000, () => "0001");
  const chart = P.charts.get("p1|s1");
  chart.window = 30;
  P.setChartPaused(chart, true);
  state.timeMode = "tick";
  const q = await exportShown(() => P.exportChart(chart));
  state.timeMode = "host";
  const lastTick = rows.at(-1).tick;
  const drawn = rows.filter((r) => r.tick >= lastTick - 30000).map((r) => r.id);
  assert.equal(drawn.length, 3001);
  assert.deepEqual(selected(rows, q), drawn);
  P.setChartPaused(chart, false);
});
