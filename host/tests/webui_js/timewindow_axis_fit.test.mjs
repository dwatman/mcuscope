// Time-axis labels past 10 digits (MCU uptime past about 11.6 days, or a clock jump): the lane
// ruler printed two labels on top of each other ("21474800002147490000") and the chart axis cut
// its last label off. fitAxisTicks takes fewer ticks when the widest label needs more room than
// one per AXIS_PX_PER_TICK; the ruler leaves out a label that would still overlap the one before
// it (its end clamp pushes the last one left), and the chart leaves out one that would run off
// either end of the canvas.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { state } = await import(webuiUrl("state.js"));
const { axisTicks, fitAxisTicks, fmtAxisTick, AXIS_PX_PER_TICK } = await import(webuiUrl("timewindow.js"));
const { charts, plotIngest, redrawPlots, clearAllCharts } = await import(webuiUrl("plots.js"));
const dg = await import(webuiUrl("digital.js"));

const tick = { timeMode: "tick", anchorTs: null, anchorTick: null };
const BIG = { xmin: 2147450000, xmax: 2147480000 };   // a 30 s window at 2^31 ms of uptime

// The px each label spans at `charPx` a character, and where each is centred on `w` px.
function overlaps(out, win, w, charPx) {
  const at = out.ticks.map((t) => ((t - win.xmin) / (win.xmax - win.xmin)) * w);
  const half = out.ticks.map((t) => (fmtAxisTick(tick, t, out.step).length * charPx) / 2);
  for (let i = 1; i < at.length; i++) if (at[i] - half[i] < at[i - 1] + half[i - 1]) return true;
  return false;
}

test("10-digit ticks at one per 70 px overlap; fitted, they do not", () => {
  // A 5 s window on 350 px: 1 s steps 70 px apart, for 11-digit labels 77 px wide (a tick
  // axis continued past a 2^32 wrap).
  const w = 350, win = { xmin: 12345675000, xmax: 12345680000 };
  assert.ok(overlaps(axisTicks(tick, win, Math.floor(w / AXIS_PX_PER_TICK)), win, w, 7),
    "setup: the plain tick count must overlap at this width, or the case proves nothing");
  const fit = fitAxisTicks(tick, win, w, (s) => s.length * 7);
  assert.ok(fit.ticks.length >= 2, "still labelled");
  assert.equal(overlaps(fit, win, w, 7), false);
});

test("short labels keep the plain tick count", () => {
  const win = { xmin: 0, xmax: 30000 };
  assert.deepEqual(fitAxisTicks(tick, win, 400, (s) => s.length * 7), axisTicks(tick, win, Math.floor(400 / AXIS_PX_PER_TICK)));
});

test("the lane ruler never draws a label over the one before it", () => {
  dg.clearAllDigital();
  state.timeMode = "tick";
  const ch = { kind: "bits", name: "io", labels: null };
  dg.digitalIngest("p1", [["led", 0, ch]], { host: 1000, tick: BIG.xmin }, "p1|s0");
  dg.digitalIngest("p1", [["led", 1, ch]], { host: 1030, tick: BIG.xmax }, "p1|s0");
  const drawn = [];
  const ruler = env.byId("dRuler");
  ruler.clientWidth = 230;
  ruler.getContext = () => new Proxy({ measureText: (t) => ({ width: t.length * 6.1 }),
                                       fillText: (t, x) => drawn.push([x, x + t.length * 6.1]) },
                                     { get: (o, k) => (k in o ? o[k] : () => {}), set: () => true });
  dg.digitalLanes.get("p1|led").canvas.clientWidth = 230;
  try {
    dg.redrawDigital();
    assert.ok(drawn.length >= 1, "no label drawn at all");
    for (let i = 1; i < drawn.length; i++) {
      assert.ok(drawn[i][0] >= drawn[i - 1][1], `labels ${i - 1} and ${i} overlap: ${JSON.stringify(drawn)}`);
    }
  } finally { state.timeMode = "host"; }
});

test("the ruler takes as many ticks as its labels fit, and labels every one", () => {
  dg.clearAllDigital();
  state.timeMode = "tick";
  const ch = { kind: "bits", name: "io", labels: null };
  // The default 30 s window of 11-digit ticks on 210 px: at one tick per 70 px (10 s steps)
  // each 67 px label would crowd its neighbour and every other one would be left out.
  dg.digitalIngest("p1", [["led", 0, ch]], { host: 1000, tick: 12345650000 }, "p1|s0");
  dg.digitalIngest("p1", [["led", 1, ch]], { host: 1030, tick: 12345680000 }, "p1|s0");
  const labels = [], marks = [];
  const ruler = env.byId("dRuler");
  ruler.clientWidth = 210;
  ruler.getContext = () => new Proxy({ measureText: (t) => ({ width: t.length * 6.1 }),
                                       fillText: (t) => labels.push(t),
                                       moveTo: (x, y) => { if (y === 0) marks.push(x); } },
                                     { get: (o, k) => (k in o ? o[k] : () => {}), set: () => true });
  dg.digitalLanes.get("p1|led").canvas.clientWidth = 210;
  try {
    dg.redrawDigital();
    assert.ok(marks.length >= 2, "setup: no tick marks");
    assert.ok(labels.length >= marks.length - 1,
      `${marks.length} ticks but ${labels.length} labels: the tick count ignored the label width`);
  } finally { state.timeMode = "host"; }
});

test("the chart axis leaves out a label that would run off the canvas", () => {
  clearAllCharts();
  plotIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!p 1 a=1" });
  const chart = charts.get("p1|adhoc");
  chart.canvasEl.clientWidth = 340;
  redrawPlots();
  state.timeMode = "tick";
  try {
    const { splits, values } = chart.uplot.opts.axes[0];
    const win = { xmin: 12345675000, xmax: 12345680000 };
    const u = { width: 350, bbox: { left: 0, width: 350 } };
    const ticks = splits(u, 0, win.xmin, win.xmax);
    assert.ok(ticks.length < axisTicks(tick, win, 5).ticks.length,
      `${ticks.length} ticks: the chart axis did not fit its tick count to the label width`);
    // A 30 s window whose first and last ticks sit on the canvas edges.
    const edge = splits({ width: 340, bbox: { left: 0, width: 340 } }, 0, BIG.xmin, BIG.xmax);
    assert.equal(edge[0], BIG.xmin, "setup: a tick must sit on the left edge");
    const out = values(u, edge);
    assert.equal(out[0], null, "a label centred on the left edge is half off the canvas");
    assert.equal(out.at(-1), edge.at(-1) === BIG.xmax ? null : out.at(-1));
    assert.ok(out.slice(1, -1).every((s) => typeof s === "string" && s.length), `middle labels: ${out}`);
  } finally { state.timeMode = "host"; }
});
