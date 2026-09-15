// Owner rulings 2026-09-15: D-2, a "shown window" export while a drag zoom stands exports the
// zoom range; D-9, a digital panel paused before its first lane stays empty and keeps its
// pause-time watermark.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
D.initDigitalCursorSync();

let nextId = 0;
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
}
const hex = (n) => n.toString(16).padStart(8, "0");

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  assert.equal(env.byId("expModeShown").disabled, false, "the shown window was not offered");
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function shownOffered(open) {
  open();
  const ok = !env.byId("expModeShown").disabled;
  env.byId("expCancel").emit("click");
  return ok;
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
}

// 50 samples, 100 ms apart on the host clock from 100 s, while the MCU clock runs at half that
// rate (50 ms a sample from tick 1000), so a tick edge mapped as if it were host time is wrong.
// `v` is analog (a chart), `b0` toggles every sample (a lane with a vertex per sample).
function stream() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  for (let i = 0; i < 50; i++) row(`!ps 0 ${hex(1000 + i * 50)} 0001,0${i % 2}`, 100 + i * 0.1);
}

// The real drag: onSelect with a uPlot whose pixels are x values.
function drag(min, max) {
  P.onSelect(P.charts.get("p1|s0"), { select: { left: min, width: max - min }, posToVal: (px) => px,
                                      setSelect() {} });
  assert.ok(TW.getZoom(), "the drag must leave a zoom standing");
}

// ---- D-2 ---------------------------------------------------------------------------

test("host base: a zoomed chart and the zoomed lanes export the zoom range", async () => {
  resetAll();
  stream();
  drag(101.25, 102.5);
  const chart = P.charts.get("p1|s0");
  let q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("since_ts"), String(101.25 - 1e-6));
  assert.equal(q.get("until_ts"), "102.5", "the selector's 30 s ending at the last sample was exported");
  assert.equal(q.get("id_to"), String(chart.frozenMaxId));
  q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_ts"), String(101.25 - 1e-6));
  assert.equal(q.get("until_ts"), "102.5");
  assert.deepEqual(seen.refusals, []);
});

test("tick base: the zoom's edges map to the host times of the first and last samples inside it", async () => {
  resetAll();
  stream();
  state.timeMode = "tick";
  drag(1210, 1550);   // i = 5 (tick 1250) to i = 11 (tick 1550, exactly on the edge)
  const chart = P.charts.get("p1|s0");
  const q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("since_ts"), String(chart.xsHost[5] - 1e-6));
  assert.equal(q.get("until_ts"), String(chart.xsHost[11]), "a sample on the right edge is drawn");
  state.timeMode = "host";
});

test("tick base: the lanes interpolate each zoom edge between the vertices either side", async () => {
  resetAll();
  stream();
  state.timeMode = "tick";
  drag(1210, 1590);
  const q = await exportShown(D.exportDigital);
  // 1210 sits a fifth of the way from tick 1200 (host 100.4) to 1250 (100.5); 1590 four fifths
  // from 1550 (101.1) to 1600 (101.2).
  assert.ok(Math.abs(Number(q.get("since_ts")) - (100.42 - 1e-6)) < 1e-9, q.get("since_ts"));
  assert.ok(Math.abs(Number(q.get("until_ts")) - 101.18) < 1e-9, q.get("until_ts"));
  state.timeMode = "host";
});

test("tick base: a lanes zoom dragged past the frozen edge ends at the edge's host time", async () => {
  resetAll();
  stream();   // edge: tick 3450 at host 104.9
  state.timeMode = "tick";
  drag(3300, 3600);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("until_ts"), String(100 + 49 * 0.1), "interpolating at the edge divides by zero");
  state.timeMode = "host";
});

test("tick base: a zoom holding no sample offers no shown window for the chart", () => {
  resetAll();
  stream();
  state.timeMode = "tick";
  drag(1260, 1290);   // between ticks 1250 and 1300
  assert.equal(shownOffered(() => P.exportChart(P.charts.get("p1|s0"))), false,
    "an inverted range (first sample after the last) would export nothing, silently");
  state.timeMode = "host";
});

test("tick base after a reset: a zoom on the continued axis exports post-reset host times", async () => {
  resetAll();
  stream();                                                 // last sample tick 3450 at host 104.9
  for (let i = 0; i < 10; i++) row(`!ps 0 ${hex(i * 50)} 0002,01`, 130 + i * 0.1);   // reset at 130 s
  state.timeMode = "tick";
  const first = 3450 + (130 - 104.9) * 1000;
  drag(first + 40, first + 160);   // ticks 50..150 after the reset: hosts 130.1..130.3
  const chart = P.charts.get("p1|s0");
  const q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("since_ts"), String(chart.xsHost.at(-9) - 1e-6));
  assert.equal(q.get("until_ts"), String(chart.xsHost.at(-7)));
  assert.ok(Number(q.get("since_ts")) > 130, "a raw-tick lookup would land before the reset");
  state.timeMode = "host";
});

test("a zoom left by a window button falls back to the span; one ended by double-click is gone", async () => {
  resetAll();
  stream();
  drag(101.25, 102.5);
  const chart = P.charts.get("p1|s0");
  chart.winEl.children[1].emit("click", {});   // 30 s: leaves the zoom, keeps the freeze
  assert.equal(TW.getZoom(), null);
  assert.equal(chart.paused, true);
  let q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("until_ts"), String(chart.xsHost[49]));
  assert.equal(q.get("since_ts"), String(chart.xsHost[49] - 30 - 1e-6));

  drag(101.25, 102.5);
  env.byId("digitalWrap").emit("dblclick");
  assert.equal(chart.paused, false, "the double-click resumes");
  assert.equal(shownOffered(() => P.exportChart(chart)), false, "a live chart has no shown window");
  row(`!ps 0 ${hex(4000)} 0001,01`, 106);
  F.pauseAll(true);
  q = await exportShown(D.exportDigital);
  assert.equal(q.get("until_ts"), "106", "the old zoom must not come back with the next pause");
  assert.equal(q.get("since_ts"), String(106 - 30 - 1e-6));
});

// ---- D-9 ---------------------------------------------------------------------------

test("a panel paused before its first lane stays empty and keeps its pause-time watermark", () => {
  resetAll();
  state.maxId = nextId = 100;
  F.pauseAll(true);
  assert.equal(F.watermarks().digital, 100);
  nextId = 499;
  row("!pd 3 e:u1:=0=OFF,1=ON", 200);
  row("!ps 3 00000001 01", 201);
  row("!ps 3 00000002 00", 202);
  const lane = D.digitalLanes.get("p1|e");
  assert.equal(F.watermarks().digital, 100, "the watermark jumped to a line after the pause");
  assert.deepEqual(D.laneDrawData(lane).vs, [], "a vertex from after the pause is on the frozen view");
  assert.equal(D.digitalRightEdge(), null, "the ruler would move under a paused panel");
  assert.equal(D.isDigitalPaused(), true);
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  D.redrawDigital();
  D.refreshDigitalReadouts();
  assert.equal(lane.valEl.textContent, "", "the readout shows a post-pause value");
  assert.equal(shownOffered(D.exportDigital), false, "an empty panel has no shown window");
  F.pauseAll(false);
  assert.deepEqual(D.laneDrawData(lane).vs, [1, 0], "resume shows what arrived meanwhile");
  assert.equal(D.digitalRightEdge(), 202);
});

test("clear-all while paused, then a sample: still empty, still paused, watermark at the clear", () => {
  resetAll();
  stream();
  F.pauseAll(true);
  state.maxId = nextId = 900;
  D.clearAllDigital();
  row(`!ps 0 ${hex(9000)} 0001,01`, 300);
  assert.equal(F.watermarks().digital, 900);
  assert.deepEqual(D.laneDrawData(D.digitalLanes.get("p1|b0")).vs, []);
  assert.equal(D.digitalRightEdge(), null);
  F.pauseAll(false);
});
