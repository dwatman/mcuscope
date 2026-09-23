// plots.js currentData hands uPlot at most about DECIMATE_PER_PX samples per pixel: an 800 Hz
// stream in a 30 s window was 24,000 points per series on a 342 px chart, every one a lineTo,
// 5 times a second. Past that each pixel column keeps its first and last sample and each
// series' lowest, highest and first null (timewindow.decimateColumns). What must survive:
// every kept value is a real sample, a one-sample spike, a rare step and a break.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { state } = await import(webuiUrl("state.js"));
const { decimateColumns, DECIMATE_PER_PX } = await import(webuiUrl("timewindow.js"));
const { charts, plotIngest, currentData, clearAllCharts, redrawPlots, resizePlots } = await import(webuiUrl("plots.js"));

const W = 100;

test("up to the threshold nothing is dropped", () => {
  const xs = Array.from({ length: DECIMATE_PER_PX * W }, (_, i) => i);
  assert.equal(decimateColumns(xs, [xs], 0, xs.length, W), null);
  assert.equal(decimateColumns(xs, [xs], 0, xs.length, 0), null, "no width, no decimation");
});

test("a dense signal keeps its extremes per pixel column, in order, and only real samples", () => {
  const n = 24000;
  const xs = Array.from({ length: n }, (_, i) => i / 800);
  const ys = xs.map((x) => Math.sin(x * 7) * 100);
  const keep = decimateColumns(xs, [ys], 0, n, W);
  assert.ok(keep.length <= 4 * (W + 1), `kept ${keep.length} of ${n}`);
  assert.ok(keep.every((k, j) => j === 0 || k > keep[j - 1]), "indices must ascend strictly");
  assert.equal(keep[0], 0);
  assert.equal(keep.at(-1), n - 1, "the newest sample is the readout's live edge");
  const kept = keep.map((i) => ys[i]);
  assert.equal(Math.max(...kept), Math.max(...ys));
  assert.equal(Math.min(...kept), Math.min(...ys));
});

test("a sparse-step fast stream keeps the exact sample of each step and a one-sample spike", () => {
  const n = 24000;
  const xs = Array.from({ length: n }, (_, i) => i);
  const ys = xs.map((i) => (i < 9001 ? 0 : 5));
  ys[15000] = 50;                                     // one sample high, then back
  ys[20000] = -50;                                    // and one low
  const keep = decimateColumns(xs, [ys], 0, n, W);
  assert.ok(keep.includes(9001), "the step moved: its first sample was not kept");
  const before = keep.filter((k) => k < 9001).at(-1);
  assert.equal(ys[before], 0, "the held level drawn up to the step is not the one before it");
  assert.ok(keep.includes(15000), "a one-sample spike vanished");
  assert.ok(keep.includes(20000), "a one-sample dip vanished");
  assert.ok(keep.includes(15001) || keep.some((k) => k > 15000 && ys[k] === 5 && k < 15000 + n / W),
    "the level after the spike must come back in the same column");
});

test("a break (null) inside a column survives, per series, and the union keeps one x array", () => {
  const n = 24000;
  const xs = Array.from({ length: n }, (_, i) => i);
  const a = xs.map(() => 1);
  const b = xs.map(() => 2);
  b[12345] = null;
  const keep = decimateColumns(xs, [a, b, undefined], 0, n, W);
  assert.ok(keep.includes(12345), "the break was decimated away, so the trace joins across it");
});

test("the chart hands uPlot the reduction, and the chip reads the newest true value", () => {
  clearAllCharts();
  state.timeMode = "host";
  for (let i = 1; i <= 20000; i++) {
    plotIngest({ id: i, ts: 1000 + i / 1000, port: "p1", chan: "event", raw: `!p ${i} v=${i % 97}` });
  }
  const chart = charts.get("p1|adhoc");
  chart.window = 30;
  const [xs, v] = currentData(chart, W);
  assert.ok(xs.length <= 4 * (W + 1), `uPlot got ${xs.length} points for ${W} px`);
  assert.equal(xs.length, v.length);
  assert.equal(v.at(-1), 20000 % 97);
  assert.equal(currentData(chart)[0].length, 20000, "without a width nothing is reduced");

  chart.canvasEl.clientWidth = W;
  redrawPlots();
  assert.ok(chart.uplot.data[0].length <= 4 * (W + 1), "the drawn data was not reduced");
  assert.equal(chart.valEls.get("v").textContent, String(20000 % 97));

  // A wider chart is re-reduced for its new width, with no new sample to mark it dirty: through
  // the redraw's own width check, and through resizePlots (a divider drag).
  redrawPlots();
  assert.equal(chart.dirty, false, "setup: the chart must be clean before the resize");
  chart.uplot.width = W;
  chart.canvasEl.clientWidth = 50 * W;   // 4 samples a pixel: not reduced
  redrawPlots();
  assert.equal(chart.uplot.data[0].length, 20000, "the redraw kept the data reduced for the old width");
  chart.uplot.width = 50 * W;
  chart.canvasEl.clientWidth = W;
  resizePlots();
  chart.uplot.width = W;                 // resizePlots' setSize, which the stub does not apply
  redrawPlots();
  assert.ok(chart.uplot.data[0].length <= 4 * (W + 1), "resizePlots left the data at the old width");
});

test("a flat signal keeps each column's first and last sample, so the live edge is drawn", () => {
  const n = 24000;
  const xs = Array.from({ length: n }, (_, i) => i);
  const keep = decimateColumns(xs, [xs.map(() => 7)], 0, n, W);
  assert.equal(keep.at(-1), n - 1, "the newest sample was dropped");
  assert.equal(keep[0], 0);
});

test("the level held across empty columns is the burst's last sample, not an extreme", () => {
  const xs = [...Array.from({ length: 1000 }, (_, i) => i / 1000), 100];
  const ys = [...Array.from({ length: 1000 }, (_, i) => (i === 10 ? 0 : i === 20 ? 9 : 5)), 5];
  ys[999] = 4;
  const keep = decimateColumns(xs, [ys], 0, xs.length, W);
  const held = keep.filter((k) => k < 1000).at(-1);
  assert.equal(ys[held], 4, "the path would hold the wrong level across the silence");
});
