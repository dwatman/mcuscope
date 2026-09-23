// plots.js y scales and the soloed y axis.
//  - Two legal finite values near the double limit (1.7e308 beside -1.7e308) overflow uPlot's
//    max - min to Infinity and the trace vanished. Such a series is drawn at a quarter scale
//    (exact, a power of two) and every number shown is divided back to the sample's own.
//  - A 200-character unit (SPEC 2.5 bounds none) became the soloed axis label and ran down the
//    whole chart; it is cut to what fits, and the chip keeps it whole.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { charts, plotIngest, currentData, redrawPlots, clearAllCharts } = await import(webuiUrl("plots.js"));

let id = 1;
const ingest = (raw) => plotIngest({ id: id++, ts: 1000 + id / 10, port: "p1", chan: "event", raw });

function shown(key) {
  const chart = charts.get(key);
  chart.canvasEl.clientWidth = 300;
  redrawPlots();
  return chart;
}

test("a series whose span overflows is drawn scaled, and read back at its own values", () => {
  clearAllCharts();
  for (let i = 0; i < 6; i++) ingest(`!p ${i} big=${i % 2 ? "-" : ""}1.7e308 small=${i}`);
  const chart = shown("p1|adhoc");
  const [, big, small] = currentData(chart, 300);
  assert.ok(big.every((v) => Number.isFinite(v)), "setup");
  assert.ok(Number.isFinite(Math.max(...big) - Math.min(...big)), "the drawn span still overflows");
  assert.deepEqual(small, [0, 1, 2, 3, 4, 5], "a series with an ordinary span must be drawn as is");
  assert.equal(Number(chart.valEls.get("big").textContent), -1.7e308, "the chip must read the sample");
  assert.equal(chart.valEls.get("small").textContent, "5");

  chart.show.set("small", false);    // solo big: its y axis is drawn
  chart.uplot = null;
  redrawPlots();
  const axis = chart.uplot.opts.axes[1];
  assert.equal(Number(axis.values(chart.uplot, [0.25 * 1.7e308])[0]), 1.7e308, "the axis read the scaled value");
  // A tick whose value read back is past the double limit (the padded range's outer ticks) is
  // left unlabelled rather than called Infinity; a real neighbour on the same axis still is.
  assert.deepEqual(axis.values(chart.uplot, [-7.5e307, 2.5e307]), [null, "1e+308"]);
});

// uPlot's own numeric steps stop at 5e32 and it draws no y tick past them: in Chromium the soloed
// axis of a 1e50 or a scaled series had no ticks, so nothing ever read back through drawnValue.
// Its steps must reach every range the axis can be asked to split into a few ticks.
test("the soloed y axis has tick steps for every magnitude a series can reach", () => {
  clearAllCharts();
  for (let i = 0; i < 6; i++) ingest(`!p ${i} v=${i % 2 ? "1e50" : "0"}`);
  const chart = shown("p1|adhoc");
  const { incrs } = chart.uplot.opts.axes[1];
  assert.ok(Array.isArray(incrs), "the axis falls back to uPlot's steps");
  for (const range of [1e-30, 1, 1e32, 1e50, 1e200, 4.25e307, 1.02e308]) {
    assert.ok(incrs.some((s) => s >= range / 10 && s <= range / 2), `no step splits a ${range} range`);
  }
  assert.ok(incrs.every(Number.isFinite), "a step overflowed");
});

// The vendored uPlot's own range function, loaded outside the DOM stub: `{auto: true}` y scales
// are ranged by rangeNum(min, max, 0.1, true), and that padding is what overflowed.
function realRangeNum() {
  const code = readFileSync(new URL("../../mcuscope/webui/vendor/uPlot.iife.min.js", import.meta.url), "utf8");
  const win = { devicePixelRatio: 1, addEventListener() {}, dispatchEvent() {},
                matchMedia: () => ({ addEventListener() {}, addListener() {} }) };
  const ctx = vm.createContext({ window: win, self: win, devicePixelRatio: 1, matchMedia: win.matchMedia,
    document: { createElement: () => ({ getContext: () => ({}), style: {}, classList: { add() {} } }),
                addEventListener() {} },
    navigator: { userAgent: "" }, CustomEvent: class {}, Intl, Math, Number, Array, Object });
  vm.runInContext(code + "\n;this.U = uPlot;", ctx);
  return ctx.U.rangeNum;
}

test("every series near the double limit gets a finite padded y range, and reads back its own values", () => {
  const rangeNum = realRangeNum();
  const M = Number.MAX_VALUE;
  const cases = {
    zeroToLimit: [0, 1.7e308], constant: [1.7e308, 1.7e308], negConstant: [-1.7e308, -1.7e308],
    opposite: [-1.7e308, 1.7e308], max: [0, M], maxConstant: [M, M], quarter: [0, M / 4],
    quarterOpposite: [-M / 4, M / 4], halfConstant: [M / 2, M / 2],
  };
  for (const [name, [a, b]] of Object.entries(cases)) {
    clearAllCharts();
    for (let i = 0; i < 6; i++) ingest(`!p ${i} v=${i % 2 ? b : a}`);
    const chart = shown("p1|adhoc");
    const drawn = currentData(chart, 300)[1];
    const [lo, hi] = rangeNum(Math.min(...drawn), Math.max(...drawn), 0.1, true);
    assert.ok(Number.isFinite(lo) && Number.isFinite(hi) && hi > lo, `${name}: y range [${lo}, ${hi}]`);
    assert.equal(Number(chart.valEls.get("v").textContent), b, `${name}: the chip must read the sample`);
  }
});

test("an ordinary chart is not rescaled", () => {
  clearAllCharts();
  for (let i = 0; i < 6; i++) ingest(`!p ${i} v=${i * 1000}`);
  const chart = shown("p1|adhoc");
  assert.deepEqual(currentData(chart, 300)[1], [0, 1000, 2000, 3000, 4000, 5000]);
  assert.equal(chart.valEls.get("v").textContent, "5000");
});

test("a long unit is cut on the soloed y axis and kept whole on the chip", () => {
  clearAllCharts();
  const unit = "u".repeat(200);
  ingest(`!pd 0 v:u2:${unit}`);
  ingest("!ps 0 1 0001");
  const chart = shown("p1|s0");
  const label = chart.uplot.opts.axes[1].label;
  assert.ok([...label].length <= 22, `axis label is ${[...label].length} characters`);
  assert.ok(label.endsWith("…"), "a cut label must say it is cut");
  assert.ok(chart.chansEl.textContent.includes(unit), "the chip must keep the whole unit");

  clearAllCharts();
  ingest("!pd 1 w:u2:degC");
  ingest("!ps 1 1 0001");
  assert.equal(shown("p1|s1").uplot.opts.axes[1].label, "degC", "a short unit is left alone");
});
