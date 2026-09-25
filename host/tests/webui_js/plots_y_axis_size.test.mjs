// plots.js: a soloed chart's y axis is as wide as its widest label. It was fixed at 46 px, so
// `10000` and `8e+307`, drawn right-aligned into the axis, lost their first characters off the
// chart's left edge. The size hook is uPlot's; this drives it with a measuring context.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { charts, plotIngest, redrawPlots, clearAllCharts } = await import(webuiUrl("plots.js"));

const PX = 7;   // per character, in CSS px
function yAxis() {
  clearAllCharts();
  let id = 1;
  for (let i = 0; i < 4; i++) plotIngest({ id: id++, ts: 1000 + i, port: "p1", chan: "event", raw: `!p ${i} v=${i}` });
  const chart = charts.get("p1|adhoc");
  chart.canvasEl.clientWidth = 300;
  redrawPlots();
  const axes = chart.uplot.opts.axes;
  assert.equal(axes.length, 2, "setup: one shown channel draws a y axis");
  return axes[1];
}
// uPlot's own view of the axis at layout: a font, the tick length and gap, a device-pixel ctx.
function sizeFor(axis, labels, dpr = 2, cycle = 0) {
  globalThis.devicePixelRatio = dpr;
  const u = { axes: [{}, { font: ["20px mono", 20], ticks: { size: 10 }, gap: 5, _size: 99 }],
              ctx: { font: "", measureText: (s) => ({ width: s.length * PX * dpr }) } };
  return axis.size(u, labels, 1, cycle);
}

test("the axis grows to its widest label", () => {
  const axis = yAxis();
  assert.equal(sizeFor(axis, ["0", "10000"]), 10 + 5 + 5 * PX);
  assert.equal(sizeFor(axis, ["0", null, "1.60e+308"]), 10 + 5 + 9 * PX, "a skipped label is no width");
  assert.equal(sizeFor(axis, ["0", "10000"], 1), 10 + 5 + 5 * PX, "measured in CSS px at any ratio");
});

test("short labels keep the axis at its old width, and a late layout cycle keeps its size", () => {
  const axis = yAxis();
  assert.equal(sizeFor(axis, ["0", "1"]), 46);
  assert.equal(sizeFor(axis, ["0", "10000"], 2, 2), 99, "uPlot's third cycle: converge, do not flip");
});
