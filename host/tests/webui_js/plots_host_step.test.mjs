// A backward step of the host wall clock (an NTP step, a manual change; the daemon stamps rows
// unclamped). The member nudge read it as a repeat: every later sample drew at the old
// high-water plus 1e-4 s and the lanes' live edge stood still for the length of the step. A step
// back over 1 s now opens a host epoch keyed by line id (timewindow.continueHost): charts and
// lanes break there and continue from the pre-step edge by each sample's own gap, and a hovered
// terminal line maps through the same epoch.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makeRow, tick as settle } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);
const { state, tickAnchors, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const TW = await import(webuiUrl("timewindow.js"));
const F = await import(webuiUrl("freeze.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

let id = 0;
const row = (raw, ts) => { const r = makeRow(++id, { ts, chan: "event", raw }); state.maxId = id; P.plotIngest(r); return r; };
const hex = (n, w) => n.toString(16).toUpperCase().padStart(w, "0");
// One stream: an analog `v` and an enum `e` (0 OFF, 1 ON, 2 FAULT), one sample per call.
const sample = (tick, ts, v, e) => row(`!ps 0 ${hex(tick, 8)} ${hex(v, 4)},${hex(e, 2)}`, ts);

function stepped() {
  P.clearAllCharts();
  D.clearAllDigital();
  state.timeMode = "host";
  row("!pd 0 v:u2 e:u1:=0=OFF,1=ON,2=FAULT", 9999);
  for (let i = 0; i <= 10; i++) sample(1000 * i, 10000 + i, i, i % 2);   // ts 10000..10010
  for (let k = 0; k < 10; k++) sample(11000 + 1000 * k, 6411 + k, 100 + k, k < 5 ? 2 : 1);   // 3599 s back
  return { chart: P.charts.get("p1|s0"), lane: D.digitalLanes.get("p1|e") };
}

test("a step back over 1 s breaks the chart and continues it by the samples' own gaps", () => {
  const { chart } = stepped();
  const xs = chart.xsHost, ys = chart.ys.get("v");
  for (let i = 1; i < xs.length; i++) assert.ok(xs[i] > xs[i - 1], `x must climb at ${i}`);
  const first = ys.indexOf(100);
  assert.equal(ys[first - 1], null, "a break before the first sample after the step");
  assert.ok(Math.abs(xs[first] - 10010) < 0.01, `the step continues from the pre-step edge, got ${xs[first]}`);
  assert.ok(Math.abs(xs.at(-1) - 10019) < 1e-6, `later samples keep their own 1 s gaps, got ${xs.at(-1)}`);
});

test("the lanes break there too, and their live edge follows", () => {
  const { lane } = stepped();
  assert.ok(lane.vs.includes(null), "the lane holds its pre-step level across the step");
  assert.ok(Math.abs(lane.xsHost.at(-1) - 10015) < 1e-6, `the newest vertex (k = 5) at ${lane.xsHost.at(-1)}`);
  for (let i = 1; i < lane.xsHost.length; i++) assert.ok(lane.xsHost[i] > lane.xsHost[i - 1]);
});

test("a hovered terminal line after the step puts the cursor at its continued time", () => {
  const { lane } = stepped();
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  const debug = makeRow(++id, { ts: 6413.5, raw: "after the step" });   // continued: 10012.5, FAULT
  try {
    env.document.elementFromPoint = () => ({ closest: () => ({ __row: debug }) });
    P.paneMouseMove({ clientX: 3, clientY: 3 });
    env.frames.splice(0).forEach((f) => f());
    assert.equal(lane.valEl.textContent, "FAULT");
  } finally {
    P.paneMouseLeave();
    env.document.elementFromPoint = () => null;
  }
});

test("a step back under 1 s is a reordered burst: nudged, no break", () => {
  P.clearAllCharts();
  D.clearAllDigital();
  row("!pd 0 v:u2 e:u1:=0=OFF,1=ON,2=FAULT", 99);
  [100, 101, 100.5, 102].forEach((ts, i) => sample(1000 * i, ts, i + 1, 0));
  const chart = P.charts.get("p1|s0");
  assert.deepEqual(chart.ys.get("v"), [1, 2, 3, 4]);
  assert.ok(chart.xsHost[2] > 101 && chart.xsHost[2] < 101.001, "nudged just past its predecessor");
  assert.equal(D.hostClock.epochs.length, 0);
});

test("clear-all drops the steps with the samples they describe", () => {
  stepped();
  assert.equal(D.hostClock.epochs.length, 1);
  D.clearAllDigital();
  assert.equal(D.hostClock.epochs.length, 0);
  assert.equal(D.hostClock.top, null);
});

test("a lanes export bounded by time sends the stepped samples' own ts", async () => {
  P.clearAllCharts();
  D.clearAllDigital();
  TW.continueHost(D.hostClock, 1, 10000);
  TW.continueHost(D.hostClock, 2, 6400);   // 3600 s back: rows from id 2 draw 3600 s later
  assert.equal(D.hostClock.epochs.length, 1, "setup");
  row("!pd 1 f:u1:/b0", 6400);
  const BIT = { kind: "bits", bit: 0 };
  const total = PLOT_CAP + PLOT_SLACK + 10;   // trims the id index: the lower bound goes by time
  for (let i = 0; i < total; i++) {
    const ts = 6400 + i * 0.001;
    D.digitalIngest("p1", [["b0", i % 2, BIT]], { host: TW.hostX(D.hostClock, 101 + i, ts), tick: i, id: 101 + i }, "p1|s1");
  }
  state.maxId = 100 + total;
  state.timeMode = "tick";
  try {
    F.pauseAll(true);
    TW.setZoom({ mode: "tick", min: -1, max: total + 1 });
    seen.lastUrl = null;
    D.exportDigital();
    env.byId("expModeShown").emit("change");
    env.byId("expGo").emit("click");
    await settle();
    const q = new URLSearchParams(seen.lastUrl.split("?")[1]);
    const oldest = total - (PLOT_CAP + 9);
    assert.ok(Math.abs(Number(q.get("since_ts")) - (6400 + oldest * 0.001)) < 1e-5,
      `since_ts ${q.get("since_ts")} is the drawn time, 3600 s past the samples'`);
  } finally {
    TW.setZoom(null);
    F.pauseAll(false);
    state.timeMode = "host";
  }
});

test("tick base: a hovered line after both a host step and a tick reset maps through both", () => {
  P.clearAllCharts();
  D.clearAllDigital();
  row("!pd 0 v:u2 e:u1:=0=OFF,1=ON,2=FAULT", 9999);
  for (let i = 0; i <= 10; i++) sample(1000 * i, 10000 + i, i, 0);   // OFF throughout, ticks 0..10000
  const after = [];
  for (let k = 0; k < 10; k++) {   // 3599 s back; from k = 5 the board also resets its tick to 0
    after.push(sample(k < 5 ? 11000 + 1000 * k : 100 * (k - 5), 6411 + k, 100 + k, k < 5 ? 2 : 1));
  }
  const lane = D.digitalLanes.get("p1|e");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  const hover = (r) => {
    env.document.elementFromPoint = () => ({ closest: () => ({ __row: r }) });
    P.paneMouseMove({ clientX: Math.random(), clientY: 3 });
    env.frames.splice(0).forEach((f) => f());
    return lane.valEl.textContent;
  };
  state.timeMode = "tick";
  try {
    assert.equal(hover(after[7]), "ON", "a ticked line after the reset: its tick plus the reset's offset");
    TW.noteTickAnchor(tickAnchors, "p1", after[6].id, after[6].ts, 100);
    const debug = makeRow(after[9].id + 1, { ts: 6417.5, raw: "no tick" });
    assert.equal(hover(debug), "ON", "a line with no tick: its estimate, past the reset");
  } finally {
    P.paneMouseLeave();
    env.document.elementFromPoint = () => null;
    state.timeMode = "host";
  }
});

test("only the newest row can open a step: a late older row and a NaN stamp open none", () => {
  const clock = TW.newHostClock();
  TW.continueHost(clock, 10, 100);
  TW.continueHost(clock, 5, 50);        // a history row arriving after the live one
  assert.equal(clock.epochs.length, 0, "an older row read as a step back");
  TW.continueHost(clock, 11, 98);       // 2 s behind row 10, the newest: a step
  assert.deepEqual(clock.epochs.map((e) => e.id), [11], "the late row became the edge to step from");
  TW.continueHost(clock, 12, NaN);      // a malformed stamp must not become the newest x
  TW.continueHost(clock, 13, 40);       // a second step, still seen
  assert.deepEqual(clock.epochs.map((e) => e.id), [11, 13]);
  assert.equal(TW.hostX(clock, 13, 40), 100);
  assert.equal(TW.hostX(clock, 11, 98), 100);
  assert.equal(TW.hostX(clock, 10, 100), 100, "rows before a step keep their own time");
});

test("a clock stepping back again and again keeps a bounded epoch list", () => {
  const clock = TW.newHostClock();
  for (let i = 0; i <= 2400; i++) TW.continueHost(clock, i, i % 2 ? 0 : 100);   // 100 s back every other row
  assert.equal(clock.epochs.length, 1000);
  assert.equal(clock.epochs.at(-1).id, 2399, "the newest steps are the ones kept");
});
