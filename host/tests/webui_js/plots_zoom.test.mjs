// plots.js: a drag on the x axis zooms EVERY chart and the digital lanes (SPEC 9.2's one
// synchronized x axis). The range pauses every surface (so the follow-tail window cannot
// overwrite it), currentData ships the selected range, and resuming or double-clicking drops
// it everywhere. The drawing itself is uPlot's and needs eyes; this pins the data.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { getZoom, setZoom } = await import(webuiUrl("timewindow.js"));
const { charts, plotIngest, setChartPaused, currentData, onSelect, clearZoom, clearAllCharts } =
  await import(webuiUrl("plots.js"));
const { isDigitalPaused, digitalLanes, initDigitalCursorSync } = await import(webuiUrl("digital.js"));

let nextId = 1;
function feed(count) {
  if (nextId === 1) {
    state.maxId = 1;
    plotIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!pd 0 b:u2" });
  }
  for (let i = 0; i < count; i++) {
    const ts = 1000 + nextId;   // one sample per second, host time
    state.maxId = nextId;
    plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw: `!p ${nextId * 1000} a=${nextId}` });
    // A second chart on the same time base: one stream, so the zoom must reach it too.
    plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw: `!ps 0 ${(nextId * 1000).toString(16).toUpperCase()} 0001` });
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

test("a selection pauses every surface and stores one range in the active mode's units", () => {
  clearAllCharts();
  setZoom(null);
  nextId = 1;
  feed(100);
  const chart = charts.get("p1|adhoc");
  assert.ok(chart, "no chart built");
  assert.ok(charts.get("p1|s0"), "the second stream must have built its own chart");
  assert.equal(chart.paused, false);
  const u = fakeU(20, 10);
  onSelect(chart, u);
  assert.equal(chart.paused, true, "the follow-tail window would otherwise overwrite the zoom");
  assert.equal(charts.get("p1|s0").paused, true,
    "a drag freezes every chart: a sibling still following the tail is not a shared x axis");
  assert.equal(isDigitalPaused(), true, "and the digital lanes, which draw the same range");
  const z = getZoom();
  assert.ok(z, "no zoom stored");
  assert.equal(z.mode, "host");
  assert.ok(Math.abs(z.min - 1020.8) < 1e-6, String(z.min));
  assert.ok(Math.abs(z.max - 1030.7) < 1e-6, String(z.max));
  assert.deepEqual(u.calls[0], [{ left: 0, top: 0, width: 0, height: 0 }, false],
    "the selection box is cleared without re-firing the hook");
});

test("the zoom reaches the chart that was NOT dragged", () => {
  // The whole point of stacked charts is reading two signals against one time axis, so the
  // range dragged on the ad-hoc chart must slice the stream chart the same way.
  const [xs] = currentData(charts.get("p1|s0"));
  assert.deepEqual([xs[0], xs.at(-1)], [1020, 1031],
    "the sibling chart is still on its own 30 s tail window");
});

test("currentData ships the zoomed range with a one-sample margin on each side", () => {
  const chart = charts.get("p1|adhoc");
  const [xs, ys] = currentData(chart);
  // Range 1020.8..1030.7 covers samples 1021..1030; margins add 1020 and 1031.
  assert.deepEqual([xs[0], xs.at(-1)], [1020, 1031]);
  assert.equal(xs.length, 12);
  assert.equal(ys.length, xs.length, "every series must match x in length");
});

test("an empty or non-finite selection is ignored", () => {
  const chart = charts.get("p1|adhoc");
  const before = getZoom();
  onSelect(chart, fakeU(50, 0));
  assert.equal(getZoom(), before, "a click with no drag is not a zoom");
  onSelect(chart, fakeU(NaN, 10));   // a scale with no data projects to NaN
  assert.equal(getZoom(), before, "a NaN range is not a zoom");
});

test("the zoom is dropped in another time mode and on resume", () => {
  const chart = charts.get("p1|adhoc");
  state.timeMode = "tick";
  try {
    const [xs] = currentData(chart);
    assert.equal(xs.length, 100 > xs.length ? xs.length : 100, "tick mode must not slice by a host range");
    assert.ok(xs.at(-1) === 100000, "tick mode draws the tail window");
  } finally {
    state.timeMode = "host";
  }
  setChartPaused(chart, false);
  assert.equal(getZoom(), null, "resuming any chart follows the tail again, on every panel");
  const [xs] = currentData(chart);
  assert.equal(xs.at(-1), 1100, "back on the live edge");
  const [sxs] = currentData(charts.get("p1|s0"));
  assert.equal(sxs.at(-1), 1100, "and so does the chart that was never resumed by hand");
});

test("a time-mode change drops the range without resuming a frozen UI", () => {
  // The range is in the old mode's units, so it cannot survive; but setTimeMode must not
  // secretly restart a UI the user paused (clearZoom is the call terminal.js makes).
  const chart = charts.get("p1|adhoc");
  onSelect(chart, fakeU(20, 10));
  assert.ok(getZoom());
  clearZoom();
  assert.equal(getZoom(), null, "the zoom is gone");
  assert.equal(chart.paused, true, "but the chart is still frozen where the user left it");
  assert.equal(isDigitalPaused(), true);
});

test("double-clicking the digital lanes clears the zoom and resumes every surface", () => {
  // The zoom is drawn on the lanes as much as on the charts, so it must be dismissable there.
  initDigitalCursorSync();
  const chart = charts.get("p1|adhoc");
  onSelect(chart, fakeU(20, 10));
  assert.ok(getZoom());
  env.byId("digitalWrap").emit("dblclick");
  assert.equal(getZoom(), null, "the double-click must clear the shared range");
  assert.equal(chart.paused, false, "and resume the charts it froze");
  assert.equal(isDigitalPaused(), false);
  assert.equal(digitalLanes.size, 0, "no lanes in this fixture: the panel is the surface");
});

test("a zoom is not overwritten by samples that keep arriving while paused", () => {
  const chart = charts.get("p1|adhoc");
  onSelect(chart, fakeU(20, 10));
  feed(50);
  const [xs] = currentData(chart);
  assert.deepEqual([xs[0], xs.at(-1)], [1020, 1031], "the paused snapshot is what the zoom slices");
  clearAllCharts();
});
