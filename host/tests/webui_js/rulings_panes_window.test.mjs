// Owner ruling 2026-09-15: the shift-clicked window span is a group state (REVIEW class 25), so a
// chart created later takes it; a plain click still sets one panel only.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { PLOT_WINDOW_DEFAULT } = await import(webuiUrl("chrome.js"));

let nextId = 0;
function sample(sid, ts) {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw: `!pd ${sid} v${sid}:u2` });
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw: `!ps ${sid} 00000001 0001` });
  return P.charts.get(`p1|s${sid}`);
}
const SPAN = { 5: 0, 30: 1, 300: 2 };   // window button index per span
const click = (chart, secs, shiftKey) => chart.winEl.children[SPAN[secs]].emit("click", { shiftKey });
const lit = (chart) => chart.winEl.children.slice(0, 3).map((b) => b.getAttribute("aria-checked"));

test("before any shift-click a new chart takes the default span", () => {
  P.clearAllCharts();
  assert.equal(sample(0, 1).window, PLOT_WINDOW_DEFAULT);
});

test("a plain click sets its own chart and is not inherited", () => {
  P.clearAllCharts();
  click(sample(0, 1), 300, false);
  assert.equal(P.charts.get("p1|s0").window, 300);
  assert.equal(sample(1, 2).window, PLOT_WINDOW_DEFAULT, "a plain click became the group span");
});

test("a chart for a new stream takes the last shift-clicked span, lit on its own selector", () => {
  P.clearAllCharts();
  const a = sample(0, 1);
  click(a, 300, true);
  click(a, 5, false);   // then a plain click on that chart: only it moves
  const b = sample(1, 2);
  assert.equal(b.window, 300);
  assert.deepEqual(lit(b), ["false", "false", "true"], "the head lies about its own window");
  assert.equal(a.window, 5);
});

test("shift-click, clear-all, new stream: the span survives the clear, paused or not", () => {
  P.clearAllCharts();
  click(sample(0, 1), 5, true);
  F.pauseAll(true);
  P.clearAllCharts();
  D.clearAllDigital();
  const c = sample(2, 3);
  assert.equal(c.window, 5);
  assert.equal(c.paused, true, "the pause-all latch is kept alongside it");
  F.pauseAll(false);
});

test("a shift-click on the lanes' selector is the same group state", () => {
  P.clearAllCharts();
  click(sample(9, 0), 300, true);   // a group span other than the one the lanes set below
  D.buildDigitalHead();
  const head = env.byId("digitalHead");
  const win = head.children.find((el) => el.className === "plot-ctl").children[0];
  win.children[SPAN[5]].emit("click", { shiftKey: true });
  assert.equal(sample(0, 1).window, 5);
  win.children[SPAN[300]].emit("click", { shiftKey: false });
  assert.equal(sample(1, 2).window, 5, "a plain click on the lanes moved the group span");
});
