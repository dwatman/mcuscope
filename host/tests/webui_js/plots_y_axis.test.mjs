// plots.js y scales and the soloed y axis.
//  - Two legal finite values near the double limit (1.7e308 beside -1.7e308) overflow uPlot's
//    max - min to Infinity and the trace vanished. Such a series is drawn at a quarter scale
//    (exact, a power of two) and every number shown is divided back to the sample's own.
//  - A 200-character unit (SPEC 2.5 bounds none) became the soloed axis label and ran down the
//    whole chart; it is cut to what fits, and the chip keeps it whole.

import test from "node:test";
import assert from "node:assert/strict";
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
