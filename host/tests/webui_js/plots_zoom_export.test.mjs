// plots.js, owner ruling D-2: a "shown window" export while a drag zoom stands exports the zoom
// range. The first test drives the lanes' export beside the chart's.

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
const chart = () => P.charts.get("p1|s0");
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
// Returns sample i's line id.
function stream() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  for (let i = 0; i < 50; i++) row(`!ps 0 ${hex(1000 + i * 50)} 0001,0${i % 2}`, 100 + i * 0.1);
  const last = nextId;
  return (i) => last - 49 + i;
}

// The real drag: onSelect with a uPlot whose pixels are x values.
function drag(min, max) {
  P.onSelect(P.charts.get("p1|s0"), { select: { left: min, width: max - min }, posToVal: (px) => px,
                                      setSelect() {} });
  assert.ok(TW.getZoom(), "the drag must leave a zoom standing");
}

// ---- D-2 ---------------------------------------------------------------------------

test("host base: a zoomed chart exports its samples in the zoom, the lanes the zoom range", async () => {
  resetAll();
  const id = stream();
  drag(101.25, 102.5);
  const chart = P.charts.get("p1|s0");
  let q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("since_id"), String(id(13) - 1), "i = 13 (101.3 s) is the first sample inside");
  assert.equal(q.get("id_to"), String(id(25)), "the selector's 30 s ending at the last sample was exported");
  assert.equal(q.has("since_ts"), false);
  q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(id(13) - 1), "the lanes by id too");
  assert.equal(q.get("id_to"), String(id(25)));
  assert.equal(q.has("since_ts") || q.has("until_ts"), false);
  assert.deepEqual(seen.refusals, []);
});

test("tick base: the zoom exports the ids of the first and last samples inside it", async () => {
  resetAll();
  const id = stream();
  state.timeMode = "tick";
  drag(1210, 1550);   // i = 5 (tick 1250) to i = 11 (tick 1550, exactly on the edge)
  const chart = P.charts.get("p1|s0");
  const q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("since_id"), String(id(5) - 1));
  assert.equal(q.get("id_to"), String(id(11)), "a sample on the right edge is drawn");
  assert.equal(q.has("since_ts") || q.has("until_ts"), false);
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

test("tick base after a reset: a zoom on the continued axis exports post-reset samples", async () => {
  resetAll();
  const id = stream();                                      // last sample tick 3450 at host 104.9
  for (let i = 0; i < 10; i++) row(`!ps 0 ${hex(i * 50)} 0002,01`, 130 + i * 0.1);   // reset at 130 s
  state.timeMode = "tick";
  const first = 3450 + (130 - 104.9) * 1000;
  drag(first + 40, first + 160);   // ticks 50..150 after the reset
  const q = await exportShown(() => P.exportChart(chart()));
  assert.equal(q.get("since_id"), String(nextId - 9), "post-reset i = 1 (tick 50) is the first inside");
  assert.equal(q.get("id_to"), String(nextId - 6));
  assert.ok(Number(q.get("since_id")) > id(49), "a raw-tick lookup would land before the reset");
  state.timeMode = "host";
});

test("a zoom left by a window button falls back to the span; one ended by double-click is gone", async () => {
  resetAll();
  const id = stream();
  drag(101.25, 102.5);
  const chart = P.charts.get("p1|s0");
  chart.winEl.children[1].emit("click", {});   // 30 s: leaves the zoom, keeps the freeze
  assert.equal(TW.getZoom(), null);
  assert.equal(chart.paused, true);
  let q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("id_to"), String(state.maxId), "the last sample is the newest line");
  assert.equal(q.get("since_id"), String(state.maxId - 50), "all 50 samples are inside 30 s");

  drag(101.25, 102.5);
  env.byId("digitalWrap").emit("dblclick");
  assert.equal(chart.paused, false, "the double-click resumes");
  assert.equal(shownOffered(() => P.exportChart(chart)), false, "a live chart has no shown window");
  row(`!ps 0 ${hex(4000)} 0001,01`, 106);
  F.pauseAll(true);
  q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(id(0) - 1), "the old zoom must not come back with the next pause");
  assert.equal(q.get("id_to"), String(state.maxId));
});
