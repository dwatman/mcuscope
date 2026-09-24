// plots.js: a chart's shown window exports by line id, since the samples of one serial burst
// share a timestamp. Three tests drive the lanes' export too.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
D.initDigitalCursorSync();

let nextId = 0;
const rows = [];   // {id, ts, port} of every line, for the daemon model below
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  rows.push({ id: nextId, ts, port });
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
  return nextId;
}
const hex = (n) => n.toString(16).padStart(8, "0");

// The daemon's selection: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to, port.
function selected(q, port = "p1") {
  const b = (k, d) => (q.has(k) ? Number(q.get(k)) : d);
  return rows.filter((r) => r.port === port && r.ts > b("since_ts", -Infinity) && r.ts <= b("until_ts", Infinity)
    && r.id > b("since_id", -Infinity) && r.id <= b("id_to", Infinity)).map((r) => r.id);
}

const opt = (name) => [...env.byId("expOptions").querySelectorAll("select")].find((el) => el.id === "expOpt_" + name);

async function exportShown(open, port = null) {
  seen.lastUrl = null;
  open();
  assert.equal(env.byId("expModeShown").disabled, false, "the shown window was not offered");
  if (port) { opt("port").value = port; opt("port").emit("change"); }
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  assert.deepEqual(seen.refusals, []);
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
  rows.length = 0;
}

function zoom(mode, min, max) {
  state.timeMode = mode;
  F.pauseAll(true);
  TW.setZoom({ mode, min, max });
}

const chart = () => P.charts.get("p1|s0");

// 200 samples, one a millisecond by the MCU clock, arriving in bursts of 10 that share one ts.
// Sample i is on the chart (v) and a lane (b0). Returns sample i's id.
function bursts() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  const first = nextId + 1;
  for (let i = 0; i < 200; i++) row(`!ps 0 ${hex(1000 + i)} 0001,0${i % 2}`, 100 + Math.floor(i / 10) * 0.01);
  return (i) => first + i;
}

// ---- lead 1: the chart --------------------------------------------------------------------

test("tick base: a burst straddling each zoom edge exports exactly the samples drawn", async () => {
  resetAll();
  const id = bursts();
  zoom("tick", 1042.5, 1156.5);   // bursts 40..49 and 150..159 straddle the edges
  const drawn = [...Array(114).keys()].map((k) => id(43 + k));   // ticks 1043..1156
  let q = await exportShown(() => P.exportChart(chart()));
  assert.deepEqual(selected(q), drawn);
  q = await exportShown(D.exportDigital);
  assert.deepEqual(selected(q), drawn, "the lanes draw the same ticks");
});

test("host base: a zoom edge inside a burst's nudged spread exports exactly the samples drawn", async () => {
  resetAll();
  const id = bursts();
  // Burst 4 draws at 100.04 + j * 1e-4, burst 15 at 100.15 + j * 1e-4.
  zoom("host", 100.04 + 3.5e-4, 100.15 + 6.5e-4);
  const xs = chart().frozen.xsHost;
  assert.equal(xs.findIndex((x) => x >= 100.04 + 3.5e-4), 44, "the fixture's nudge moved");
  const drawn = [...Array(113).keys()].map((k) => id(44 + k));
  let q = await exportShown(() => P.exportChart(chart()));
  assert.deepEqual(selected(q), drawn);
  assert.equal(q.has("since_ts"), false, "stored ts 100.04 cannot split burst 4");
  q = await exportShown(D.exportDigital);
  assert.deepEqual(selected(q), drawn, "the lanes place a burst as the chart does");
  assert.equal(q.has("since_ts"), false);
});

test("host base tail: a burst straddling the window's left edge exports its drawn half", async () => {
  resetAll();
  row("!pd 0 v:u2", 99);
  const burst = [...Array(10).keys()].map((i) => row(`!ps 0 ${hex(1000 + i)} 0001`, 100));
  const last = row(`!ps 0 ${hex(31000)} 0001`, 130.0005);   // window: 100.0005 .. 130.0005
  F.pauseAll(true);
  const q = await exportShown(() => P.exportChart(chart()));
  assert.deepEqual(selected(q), [...burst.slice(5), last]);
});

test("a ring trim before the pause moves the first id the export starts from", async () => {
  resetAll();
  row("!pd 0 v:u2", 1);
  const first = nextId + 1, total = PLOT_CAP + PLOT_SLACK + 50;
  for (let i = 0; i < total; i++) row(`!ps 0 ${hex(i)} 0001`, 10 + i * 0.001);
  const kept = PLOT_CAP + 49;
  assert.equal(chart().xsHost.length, kept, "the fixture must have trimmed once");
  chart().window = 300;   // longer than the ring holds
  F.pauseAll(true);
  const q = await exportShown(() => P.exportChart(chart()));
  assert.equal(q.get("since_id"), String(first + total - kept - 1));
  assert.equal(q.get("id_to"), String(nextId));
});

// 50 samples ticks 1000.. then a board reset at 130 s: 10 samples from tick 0. Sample ids back.
function withReset() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  const pre = [...Array(50).keys()].map((i) => row(`!ps 0 ${hex(1000 + i * 50)} 0001,0${i % 2}`, 100 + i * 0.1));
  const post = [...Array(10).keys()].map((i) => row(`!ps 0 ${hex(i * 50)} 0002,0${i % 2}`, 130 + i * 0.1));
  return { pre, post };
}

test("a reset's gap point on either zoom edge is skipped, not exported as id -1 or NaN", async () => {
  resetAll();
  const { pre, post } = withReset();
  const gapAt = chart().xsTick.length - 11;
  assert.equal(chart().ids[gapAt], null, "the fixture has no gap point");
  const gap = chart().xsTick[gapAt], after = chart().xsTick[gapAt + 1];
  zoom("tick", gap - 0.5e-4, after + 60);   // first index: the gap point
  let q = await exportShown(() => P.exportChart(chart()));
  assert.deepEqual(selected(q), post.slice(0, 2));
  F.pauseAll(false);
  zoom("tick", 3300, gap + 0.5e-4);         // last index: the gap point
  q = await exportShown(() => P.exportChart(chart()));
  assert.deepEqual(selected(q), pre.slice(46));
});

test("a zoom across a board reset exports both sides, on chart and lanes", async () => {
  resetAll();
  const { pre, post } = withReset();
  zoom("tick", 3300, chart().xsTick.at(-10) + 120);
  const drawn = [...pre.slice(46), ...post.slice(0, 3)];
  assert.deepEqual(selected(await exportShown(() => P.exportChart(chart()))), drawn);
  assert.deepEqual(selected(await exportShown(D.exportDigital)), drawn);
});
