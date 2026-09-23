// The chart and lane chrome that replaced uPlot's legend and fills the Plots head: value
// readouts in the channel chips, the solo axis unit, the collapsed head's channel names,
// per-browser titles, the empty state and hint, the below-the-fold cue, and the lane ruler.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, webuiDir } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { charts, plotIngest, plotSeed, redrawPlots, renameChart, paintChanValues, clearAllCharts, initPlots } =
  await import(webuiUrl("plots.js"));
const { digitalLanes, clearAllDigital, redrawDigital } = await import(webuiUrl("digital.js"));
const { TITLES_KEY } = await import(webuiUrl("layout.js"));
initPlots();

let nextId = 0;
function ingest(raw, port = "p1") {
  const row = { id: ++nextId, ts: 1000 + nextId, port, chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
}

function built(chart) {
  chart.canvasEl.clientWidth = 300;
  redrawPlots();
  assert.ok(chart.uplot, "the chart did not build");
  return chart.uplot;
}

const chip = (chart, name) => chart.chansEl.children.find((c) => c.children[1].textContent === name);
const val = (chart, name) => chip(chart, name).children.find((c) => c.className === "val").textContent;

test("uPlot's own legend is off and each chip carries the newest value", () => {
  clearAllCharts();
  ingest("!pd 0 v:s2*0.5:V n:u2");
  ingest("!ps 0 1 0004,0007");
  ingest("!ps 0 2 0006,0008");
  const chart = charts.get("p1|s0");
  const u = built(chart);
  assert.equal(u.opts.legend.show, false, "the legend repeated every name below the canvas");
  assert.equal(val(chart, "v"), "3.000", "a scaled channel reads as a float");
  assert.equal(val(chart, "n"), "8", "an integer channel reads as an integer");
});

test("a chip reads the value under the cursor, a gap as --, and the newest value again once it leaves", () => {
  clearAllCharts();
  ingest("!pd 0 a:u2 b:u2");
  ingest("!ps 0 1 0001,0002");
  ingest("!p 5 other=1");
  const chart = charts.get("p1|s0");
  const u = built(chart);
  u.data = [[1, 2, 3], [10, null, 30], [7, 8, null]];
  u.cursor = { left: 55 };
  u.posToIdx = () => 1;
  paintChanValues(chart);
  assert.equal(val(chart, "a"), "--", "a gap under the cursor is not the previous sample");
  assert.equal(val(chart, "b"), "8");
  u.cursor = { left: -10 };
  paintChanValues(chart);
  assert.equal(val(chart, "a"), "30");
  assert.equal(val(chart, "b"), "8", "off the chart, a channel absent from the last sample shows its last value");
  u.posToIdx = () => 99;
  u.cursor = { left: 5 };
  paintChanValues(chart);
  assert.equal(val(chart, "a"), "30", "an index past the data is no cursor at all");
});

test("a soloed channel's axis names its unit, and a unitless one gets no label", () => {
  clearAllCharts();
  plotSeed([
    { channel: { name: "volts", port: "p1", sid: "3", type: "u2", kind: "analog", unit: "V" },
      points: [{ line_id: 1, ts: 1, tick_ms: 1, value: 3 }] },
    { channel: { name: "blank", port: "p1", sid: "4", type: "u2", kind: "analog", unit: "   " },
      points: [{ line_id: 2, ts: 2, tick_ms: 2, value: 4 }] },
  ]);
  const withUnit = built(charts.get("p1|s3"));
  assert.equal(withUnit.opts.axes.length, 2, "one shown trace gets its y axis");
  assert.equal(withUnit.opts.axes[1].label, "V");
  const without = built(charts.get("p1|s4"));
  assert.equal(without.opts.axes.length, 2);
  assert.equal("label" in without.opts.axes[1], false, "a whitespace unit must not reserve an empty label band");
});

test("a collapsed chart's head lists its shown channels, and forgets them on expand", () => {
  clearAllCharts();
  ingest("!pd 0 tri:u2 ramp:u2 ftest:u2");
  ingest("!ps 0 1 0001,0002,0003");
  const chart = charts.get("p1|s0");
  assert.equal(chart.namesEl.hidden, true, "an expanded chart shows its chips instead");
  const collapse = chart.el.children[0].children[0];
  collapse.emit("click");
  assert.equal(chart.namesEl.hidden, false);
  assert.equal(chart.namesEl.textContent, "tri, ramp, ftest");
  chip(chart, "ramp").emit("click", {});
  assert.equal(chart.namesEl.textContent, "tri, ftest", "a channel hidden while collapsed leaves the list");
  ingest("!pd 0 tri:u2 ramp:u2 ftest:u2 extra:u1");
  ingest("!ps 0 2 0001,0002,0003,04");
  assert.equal(chart.namesEl.textContent, "tri, ftest, extra", "a channel arriving while collapsed joins it");
  collapse.emit("click");
  assert.equal(chart.namesEl.hidden, true);
});

test("a chart title is renamed per browser; empty or whitespace restores the default", () => {
  clearAllCharts();
  ingest("!pd 0 v:u2");
  ingest("!ps 0 1 0001");
  const chart = charts.get("p1|s0");
  assert.equal(chart.titleEl.textContent, "stream 0");
  renameChart(chart, "  battery pack  ");
  assert.equal(chart.titleEl.textContent, "battery pack");
  assert.deepEqual(JSON.parse(env.store.get(TITLES_KEY)), { "p1|s0": "battery pack" });
  renameChart(chart, "   ");
  assert.equal(chart.titleEl.textContent, "stream 0", "whitespace is not a title");
  assert.deepEqual(JSON.parse(env.store.get(TITLES_KEY)), {}, "and leaves nothing stored");
  renameChart(chart, "x".repeat(50));
  assert.equal(chart.titleEl.textContent.length, 32, "a title is bounded");
  renameChart(chart, "");
  assert.equal(chart.titleEl.textContent, "stream 0");
  renameChart(chart, "stream 0");
  assert.deepEqual(JSON.parse(env.store.get(TITLES_KEY)), {}, "typing the default stores nothing");
});

test("a rename belongs to one board's chart, not to every stream 0", () => {
  clearAllCharts();
  ingest("!pd 0 v:u2", "a");
  ingest("!ps 0 1 0001", "a");
  ingest("!pd 0 v:u2", "b");
  ingest("!ps 0 1 0001", "b");
  renameChart(charts.get("a|s0"), "charger");
  assert.equal(charts.get("b|s0").titleEl.textContent, "stream 0");
  clearAllCharts();
  ingest("!ps 0 2 0002", "a");
  assert.equal(charts.get("a|s0").titleEl.textContent, "charger", "a rebuilt chart takes its stored title");
  renameChart(charts.get("a|s0"), "");
});

test("the empty state and the gesture hint follow the widgets, lanes included", () => {
  clearAllCharts();
  clearAllDigital();
  assert.equal(env.byId("plotEmpty").hidden, false);
  assert.equal(env.byId("plotHint").hidden, true, "no chart, nothing to drag");
  ingest("!pd 7 st:u1:=0=A,1=B");
  ingest("!ps 7 1 01");
  assert.equal(charts.size, 0, "a digital-only stream builds no chart");
  assert.equal(env.byId("plotEmpty").hidden, true, "'No plot data yet' beside live lanes is false");
  assert.equal(env.byId("plotHint").hidden, false);
  assert.equal(env.byId("digitalHead").hidden, false);
  clearAllCharts();
  assert.equal(env.byId("plotEmpty").hidden, true, "the lanes are still there");
  clearAllDigital();
  assert.equal(env.byId("plotEmpty").hidden, false);
  assert.equal(env.byId("digitalHead").hidden, true, "no lanes, no Digital / Enum head");
});

test("[hidden] beats every display rule: one global rule, no per-selector patches", () => {
  // `.plot-head { display: flex }` beat the UA [hidden] rule, so the empty panel showed a live
  // pause. A patch per selector covers only the elements someone already noticed.
  const css = readFileSync(webuiDir() + "style.css", "utf8");
  assert.match(css, /^\[hidden\] \{ display: none !important; \}$/m);
  assert.deepEqual(css.match(/\S\[hidden\]/g), null, "a per-selector [hidden] patch is back");
  const loud = [...css.matchAll(/display:\s*([\w-]+)\s*!important/g)].map((m) => m[1]);
  assert.deepEqual(loud.filter((v) => v !== "none"), [], "an !important display outranks [hidden]");
});

test("the plots empty state is one line, with the grammar and docs in its tooltip", () => {
  const html = readFileSync(webuiDir() + "index.html", "utf8");
  const [, title, line] = html.match(/id="plotEmpty" title="([^"]*)">([^<]*)</);
  assert.equal(line, "No plot data yet: the board prints !p or !pd / !ps lines");
  for (const want of ["!p &lt;tick&gt; &lt;name&gt;=&lt;value&gt;", "!p 1234 temp=21.5", "!pd 0",
                      "firmware/monitor/INTEGRATION.md", "docs/SPEC.md section 2.5"]) {
    assert.ok(title.includes(want), `the tooltip must name ${want}`);
    assert.ok(!line.includes(want), `the visible line must not carry ${want}`);
  }
});

test("an empty state cannot grow past one line, whatever its copy", () => {
  const css = readFileSync(webuiDir() + "style.css", "utf8");
  const rule = css.match(/\n\.empty-state \{([^}]*)\}/)[1];
  for (const want of ["white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"]) {
    assert.ok(rule.includes(want), `.empty-state needs ${want}`);
  }
});

test("the below-the-fold cue counts widgets under the visible part and names them", () => {
  clearAllCharts();
  clearAllDigital();
  ingest("!pd 0 v:u2");
  ingest("!ps 0 1 0001");
  ingest("!pd 1 v2:u2");
  ingest("!ps 1 1 0001");
  ingest("!pd 2 st:u1:=0=A,1=B");
  ingest("!ps 2 1 01");
  const rect = (top, height = 30) => () => ({ top, bottom: top + height, height, left: 0, right: 0, width: 0 });
  env.byId("plotsScroll").getBoundingClientRect = rect(100, 400);   // visible 100..500
  charts.get("p1|s0").el.getBoundingClientRect = rect(110);
  charts.get("p1|s1").el.getBoundingClientRect = rect(490);          // 10 px of its head peeks in
  env.byId("digitalHead").getBoundingClientRect = rect(700);
  // The cue runs when the fold can move (a scroll, a resize), not on the redraw tick.
  const tickFn = () => env.byId("plotsScroll").emit("scroll");
  tickFn();
  const btn = env.byId("plotFold");
  assert.equal(btn.hidden, false);
  assert.equal(btn.textContent, "↓ 2 below", "a head peeking 10 px is as good as unseen");
  assert.ok(btn.title.includes("stream 1, Digital / Enum"), btn.title);
  charts.get("p1|s1").el.getBoundingClientRect = rect(300);
  env.byId("digitalHead").getBoundingClientRect = rect(460);
  tickFn();
  assert.equal(btn.hidden, true, "everything in view: no cue");
});

test("the lane ruler labels the shared window's ticks and names the time base", () => {
  clearAllCharts();
  clearAllDigital();
  for (let i = 0; i < 4; i++) {
    const row = { id: ++nextId, ts: 2000 + i * 10, port: "p1", chan: "event", raw: i ? `!ps 2 ${i} 0${i % 2}` : "!pd 2 st:u1:=0=A,1=B" };
    state.maxId = row.id;
    plotIngest(row);
  }
  const ruler = env.byId("dRuler");
  const texts = [];
  ruler.clientWidth = 400;
  ruler.getContext = () => new Proxy({ measureText: () => ({ width: 20 }), fillText: (t) => texts.push(t) },
                                     { get: (o, k) => (k in o ? o[k] : () => {}), set: () => true });
  digitalLanes.get("p1|st").canvas.clientWidth = 400;
  state.timeMode = "rel";
  state.anchorTs = 2000;
  try {
    redrawDigital();
    assert.equal(env.byId("dRulerLabel").textContent, "x: rel (s)");
    // 30 s window ending at the newest sample (2030), 400 px at 70 px a label: 10 s steps.
    assert.deepEqual(texts, ["0", "10", "20", "30"]);
  } finally {
    state.timeMode = "host";
    state.anchorTs = null;
  }
});
