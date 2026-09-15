// Pre-release round 2026-09-15, charts and lanes: the shown-window export anchored on the
// surface's own edge (D-2), the pause-all label on create and clear (D-4), the single-trace
// y axis unit after a redefinition (D-10), and the branches nothing pinned (F-23, F-25, F-26).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state, tickAnchors } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const C = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

let label = null;
F.onFreezeChanged(() => { label = F.pauseAllLabel(); });

let nextId = 0;
function row(raw, ts, port = "p1") {
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
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  C.clearAllCan();
  TW.setZoom(null);
  state.timeMode = "host";
}

// ---- D-4 ---------------------------------------------------------------------------

test("a chart or a lane born live, and a clear, re-render the pause-all label", () => {
  resetAll();
  label = null;
  row("!pd 0 v:u2", 1);
  row("!ps 0 00000001 0001", 1.1);
  assert.equal(label, "pause all", "a chart born live must relabel the button");
  P.setChartPaused(P.charts.get("p1|s0"), true);
  assert.equal(label, "resume all");
  label = null;
  row("!pd 1 e:u1:=0=OFF,1=ON", 2);
  row("!ps 1 00000002 01", 2.1);
  assert.equal(label, "pause all", "a lane born live must relabel the button");
  label = null;
  D.clearAllDigital();
  assert.equal(label, "resume all", "clearing the only live lanes must relabel the button");
  P.setChartPaused(P.charts.get("p1|s0"), false);
  label = null;
  P.clearAllCharts();
  assert.equal(label, "resume all", "clearing the only live chart must relabel the button");
});

// ---- D-2 ---------------------------------------------------------------------------

test("a paused chart's shown window ends at its own last sample, not at a later line", async () => {
  resetAll();
  row("!pd 0 v:u2", 100);
  for (let i = 0; i < 50; i++) row(`!ps 0 ${(1000 + i * 100).toString(16)} 0001`, 100 + i * 0.1);
  const lastSampleId = state.maxId;
  row("!m @9000 board crashed", 160, "p1");   // a later line, far past the stream's end
  const chart = P.charts.get("p1|s0");
  P.setChartPaused(chart, true);
  assert.equal(chart.frozenMaxId, lastSampleId + 1, "positive control: the watermark is the later line");
  const q = await exportShown(() => P.exportChart(chart));
  assert.equal(q.get("id_to"), String(lastSampleId),
    "the window ends at the chart's last sample, not at the id_to row (ts 160)");
  assert.equal(q.get("since_id"), String(lastSampleId - 50), "all 50 samples are inside 30 s");
  assert.equal(q.has("last_ms") || q.has("since_ts"), false);
  assert.deepEqual(seen.refusals, []);
});

test("a paused lane panel's shown window ends at its frozen edge", async () => {
  resetAll();
  row("!pd 2 f:u1:/b0", 170);
  row("!ps 2 00000000 01", 170);   // outside the 30 s ending at 209
  const first = nextId + 1;
  for (let i = 0; i < 10; i++) row(`!ps 2 ${(i + 1).toString(16)} 0${i % 2}`, 200 + i);
  const last = nextId;
  row("!m later", 500);
  D.setDigitalPaused(true);
  row("!ps 2 0000000B 01", 600);   // after the freeze: must not move the edge
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("id_to"), String(last), "the last sample before the freeze, not the later line");
  assert.equal(q.get("since_id"), String(first - 1), "the panel's own window, 30 s by default");
  assert.equal(q.has("since_ts") || q.has("until_ts"), false);
  D.setDigitalPaused(false);
});

// ---- D-10 --------------------------------------------------------------------------

test("a single-trace chart's y axis label follows a unit redefinition", () => {
  resetAll();
  row("!pd 0 v:u2:mV", 1);
  row("!ps 0 00000001 0001", 1.1);
  const chart = P.charts.get("p1|s0");
  chart.canvasEl.clientWidth = 300;
  P.redrawPlots();
  const axisLabel = () => (chart.uplot.opts.axes[1] || {}).label;
  assert.equal(axisLabel(), "mV");
  row("!pd 0 v:u2:V", 2);
  row("!ps 0 00000002 0002", 2.1);
  chart.uplot.width = 300;
  P.redrawPlots();
  assert.equal(axisLabel(), "V", "the axis kept the old unit beside a chip reading the new one");
});

// ---- F-23 --------------------------------------------------------------------------

test("a chart born live while a drag zoom stands follows its own tail", () => {
  resetAll();
  C.canIngest({ id: ++nextId, ts: 1, port: "p1", chan: "event", raw: "!can 1 - 100 AA" });
  row("!pd 0 v:u2", 10);
  for (let i = 0; i < 20; i++) row(`!ps 0 ${(i + 1).toString(16)} 0001`, 10 + i * 0.1);
  const a = P.charts.get("p1|s0");
  // The real drag: the zoom, and pause-all with it.
  P.onSelect(a, { select: { left: 0, width: 10 }, posToVal: (px) => 10 + px / 100,
                  setSelect() {} });
  assert.ok(TW.getZoom(), "the drag must leave a zoom standing");
  C.setCanPaused(false);   // one surface resumed by hand ends the pause-all latch; the zoom stays
  assert.equal(F.bornPaused(), false);
  row("!pd 1 w:u2", 20);
  for (let i = 0; i < 20; i++) row(`!ps 1 ${(i + 1).toString(16)} 0002`, 20 + i * 0.1);
  const b = P.charts.get("p1|s1");
  assert.equal(b.paused, false);
  const xs = P.currentData(b)[0];
  assert.equal(xs.length, 20, "a live chart drew the zoom range, which holds none of its samples");
  assert.equal(xs.at(-1), b.xsHost.at(-1));
});

// ---- F-25 --------------------------------------------------------------------------

test("under the tick base a hovered line with no tick of its own drives the cursor at its estimate", () => {
  resetAll();
  state.timeMode = "tick";
  try {
    row("!pd 3 e:u1:=0=OFF,1=ON", 300);
    row("!ps 3 000003E8 00", 300);     // tick 1000: OFF
    row("!ps 3 00001388 01", 304);     // tick 5000: ON
    const lane = D.digitalLanes.get("p1|e");
    lane.canvas.clientWidth = 300;
    env.byId("digitalWrap").clientWidth = 340;
    D.redrawDigital();
    assert.equal(lane.valEl.textContent, "ON", "the readout starts at the live edge");
    const anchorId = ++nextId;
    TW.noteTickAnchor(tickAnchors, "p1", anchorId, 300, 1000);
    const debug = { id: ++nextId, ts: 300.2, port: "p1", chan: "debug", raw: "hello" };
    // A hover over that terminal line: the pane's hit test resolves to its row.
    const ln = { __row: debug };
    env.document.elementFromPoint = () => ({ closest: () => ln });
    P.paneMouseMove({ clientX: 5, clientY: 5 });
    env.frames.splice(0).forEach((f) => f());
    assert.equal(lane.valEl.textContent, "OFF",
      "the line's estimated tick (1200) must place the cursor, as its tick column reads");
    P.paneMouseLeave();
  } finally {
    state.timeMode = "host";
    env.document.elementFromPoint = () => null;
  }
});

// ---- F-26 --------------------------------------------------------------------------

test("a lane colour applies to every port's lane of that name, and to no other name", () => {
  resetAll();
  row("!pd 4 f:u1:/b0", 400, "a");
  row("!ps 4 00000001 01", 400, "a");
  row("!pd 4 f:u1:/b0,b1", 400, "b");
  row("!ps 4 00000001 01", 400, "b");
  const [a0, b0, b1] = ["a|b0", "b|b0", "b|b1"].map((k) => D.digitalLanes.get(k));
  a0.swEl.onclick({});
  const picker = env.body.children.filter((c) => c.type === "color").at(-1);
  assert.ok(picker, "the swatch must open a colour input");
  picker.value = "#123456";
  picker.onchange();
  assert.equal(a0.color, "#123456");
  assert.equal(b0.color, "#123456", "the other port's lane of the same name follows the colour");
  assert.notEqual(b1.color, "#123456", "a lane of another name does not");
});
