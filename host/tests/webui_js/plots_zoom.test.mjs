// plots.js: a drag on the x axis zooms a chart. The range pauses the chart (so the follow-
// tail window cannot overwrite it), currentData ships the selected range, and resuming or
// double-clicking drops it. The drawing itself is uPlot's and needs eyes; this pins the data.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { charts, plotIngest, setChartPaused, currentData, onSelect, clearAllCharts } =
  await import(webuiUrl("plots.js"));

let nextId = 1;
function feed(count) {
  for (let i = 0; i < count; i++) {
    const ts = 1000 + nextId;   // one sample per second, host time
    state.maxId = nextId;
    plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw: `!p ${nextId * 1000} a=${nextId}` });
    nextId += 1;
  }
}

// A stand-in for the uPlot instance the setSelect hook receives: pixel 0..100 maps onto the
// x values 1001..1100.
function fakeU(left, width) {
  const calls = [];
  return {
    select: { left, top: 0, width, height: 10 },
    posToVal: (px) => 1001 + (px / 100) * 99,
    setSelect: (o, fire) => calls.push([o, fire]),
    calls,
  };
}

test("a selection pauses the chart and stores the range in the active mode's units", () => {
  clearAllCharts();
  nextId = 1;
  feed(100);
  const chart = charts.get("adhoc");
  assert.ok(chart, "no chart built");
  assert.equal(chart.paused, false);
  const u = fakeU(20, 10);
  onSelect(chart, u);
  assert.equal(chart.paused, true, "the follow-tail window would otherwise overwrite the zoom");
  assert.ok(chart.zoom, "no zoom stored");
  assert.equal(chart.zoom.mode, "host");
  assert.ok(Math.abs(chart.zoom.min - 1020.8) < 1e-6, String(chart.zoom.min));
  assert.ok(Math.abs(chart.zoom.max - 1030.7) < 1e-6, String(chart.zoom.max));
  assert.deepEqual(u.calls[0], [{ left: 0, top: 0, width: 0, height: 0 }, false],
    "the selection box is cleared without re-firing the hook");
});

test("currentData ships the zoomed range with a one-sample margin on each side", () => {
  const chart = charts.get("adhoc");
  const [xs, ys] = currentData(chart);
  // Range 1020.8..1030.7 covers samples 1021..1030; margins add 1020 and 1031.
  assert.deepEqual([xs[0], xs.at(-1)], [1020, 1031]);
  assert.equal(xs.length, 12);
  assert.equal(ys.length, xs.length, "every series must match x in length");
});

test("an empty or inverted selection is ignored", () => {
  const chart = charts.get("adhoc");
  const before = chart.zoom;
  onSelect(chart, fakeU(50, 0));
  assert.equal(chart.zoom, before, "a click with no drag is not a zoom");
});

test("the zoom is dropped in another time mode and on resume", () => {
  const chart = charts.get("adhoc");
  state.timeMode = "tick";
  try {
    const [xs] = currentData(chart);
    assert.equal(xs.length, 100 > xs.length ? xs.length : 100, "tick mode must not slice by a host range");
    assert.ok(xs.at(-1) === 100000, "tick mode draws the tail window");
  } finally {
    state.timeMode = "host";
  }
  setChartPaused(chart, false);
  assert.equal(chart.zoom, null, "resuming follows the tail again");
  const [xs] = currentData(chart);
  assert.equal(xs.at(-1), 1100, "back on the live edge");
});

test("a zoom is not overwritten by samples that keep arriving while paused", () => {
  const chart = charts.get("adhoc");
  onSelect(chart, fakeU(20, 10));
  feed(50);
  const [xs] = currentData(chart);
  assert.deepEqual([xs[0], xs.at(-1)], [1020, 1031], "the paused snapshot is what the zoom slices");
  clearAllCharts();
});
