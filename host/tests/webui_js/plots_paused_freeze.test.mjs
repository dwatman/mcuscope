// A paused analog chart must stay frozen, including after its ring rotates past the freeze.
//
// The pause recorded an INDEX into the live arrays (frozenLen), and the block trim slid that
// index down by every dropped sample. The arrays keep filling while paused (deliberately, for
// the resume catch-up), so at PLOT_CAP the index walked to 0 and the paused chart drew nothing
// at all - about 100 s of a 1 kHz stream, unrecoverable without resuming. digital.js takes the
// snapshot treatment (anchorDigitalFreeze / laneDrawData) and pane.js too (frozenRows); this is
// the analog sibling (REVIEW class 26). Per the class sweep the WHOLE ring rotates out: a
// partial rotation leaves pre-freeze samples in place and passes on the bug.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const { charts, plotIngest, setChartPaused, redrawPlots, chartDrawData, clearAllCharts } =
  await import(webuiUrl("plots.js"));

let nextId = 1;
let ts = 1000;
let n = 0;
function feed(count, name = "a") {
  for (let i = 0; i < count; i++) {
    ts += 0.001;
    state.maxId = nextId;
    plotIngest({ id: nextId++, ts, port: "p1", chan: "event",
                 raw: `!p ${1000 + n} ${name}=${n % 7}` });
    n += 1;
  }
}

// What a redraw hands uPlot, which is the drawn view itself (a stubbed canvas has no pixels).
function drawn(chart) {
  chart.canvasEl.clientWidth = 400;
  chart.dirty = true;
  redrawPlots();
  return chart.uplot.data.map((arr) => [...arr]);
}

let frozenDraw = null;   // the drawn arrays captured at pause
let frozenEdge = 0;      // host time of the newest frozen sample

test("pausing snapshots the samples the freeze covers", () => {
  clearAllCharts();
  feed(10);
  const chart = charts.get("adhoc");
  assert.ok(chart, "the fixture built no chart; everything below would prove nothing");
  setChartPaused(chart, true);

  frozenDraw = drawn(chart);
  frozenEdge = chart.xsHost[chart.xsHost.length - 1];   // nothing newer may ever be drawn
  assert.equal(frozenDraw[0].length, 10, "the freeze must cover every held sample");
});

test("the frozen view survives the whole ring rotating past the freeze", () => {
  const chart = charts.get("adhoc");
  feed(PLOT_CAP + PLOT_SLACK + 64);
  assert.ok(chart.xsHost.length <= PLOT_CAP + PLOT_SLACK, "the ring must have trimmed");
  assert.ok(chart.xsHost[0] > frozenEdge,
    "precondition: the whole ring must sit past the freeze, or this test passes on the bug");

  // A paused redraw: what a window change, a resize or a theme toggle triggers.
  assert.deepEqual(drawn(chart), frozenDraw,
    "the drawn view moved: it was re-derived from a ring that rotated past the freeze");
  assert.equal(chart.paused, true, "the chart un-paused itself");
});

test("a channel first seen while paused draws nothing into the frozen view", () => {
  const chart = charts.get("adhoc");
  feed(1, "late");
  assert.ok(chart.names.includes("late"), "the channel must still be created (it fills for resume)");
  const data = drawn(chart);
  const late = data[chart.names.indexOf("late") + 1];
  assert.equal(late.length, data[0].length, "uPlot needs every series the length of x");
  assert.ok(late.every((v) => v === null), "the freeze predates this channel; it must draw as a gap");
  assert.deepEqual(data[0], frozenDraw[0], "the frozen x window must not have moved");
});

test("resuming drops the snapshot and returns to the live arrays", () => {
  const chart = charts.get("adhoc");
  setChartPaused(chart, false);
  assert.equal(chart.frozen, null, "a live chart must not keep a stale snapshot around");
  assert.equal(chartDrawData(chart), chart, "live draws must read the arrays themselves");
  const data = drawn(chart);
  assert.ok(data[0][0] > frozenEdge, "everything buffered while frozen is now the view");
});
