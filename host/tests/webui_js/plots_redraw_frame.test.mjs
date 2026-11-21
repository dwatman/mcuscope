// plots.js: the 5 Hz redraw timer only requests an animation frame; the charts and lanes are
// drawn inside it. Chromium on Linux (ANGLE on GL) froze the page for ~3.5 s of every 4 while a
// canvas was drawn straight from a timer, stalling /ws delivery and the terminal with it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { sidebar } = await import(webuiUrl("state.js"));
const { charts, plotIngest, initPlots, clearAllCharts } = await import(webuiUrl("plots.js"));
initPlots();
const tickFn = env.intervals.find((i) => i.ms === 200).fn;

function adhocChart(id) {
  clearAllCharts();
  env.runFrames();   // whatever building the chart queued (a resize) is not the tick's frame
  document.hidden = false;
  sidebar.setAttribute("data-view", "both");
  sidebar.clientWidth = 340;
  plotIngest({ id, ts: 1000, port: "p1", chan: "event", raw: "!p 1 a=1" });
  const chart = charts.get("p1|adhoc");
  chart.canvasEl.clientWidth = 340;
  env.runFrames();
  return chart;
}

test("the redraw tick draws nothing itself; its animation frame draws", () => {
  const chart = adhocChart(1);
  tickFn();
  assert.equal(chart.uplot, null, "the timer drew the chart outside an animation frame");
  assert.equal(env.frames.length, 1, "the tick queued no frame");
  env.runFrames();
  assert.ok(chart.uplot, "the frame did not draw (positive control)");
});

test("ticks before the frame runs queue one frame, and the next tick after it queues again", () => {
  adhocChart(2);
  tickFn(); tickFn(); tickFn();
  assert.equal(env.frames.length, 1, "a slow frame let ticks stack up one draw each");
  env.runFrames();
  tickFn();
  assert.equal(env.frames.length, 1, "after a frame ran, the next tick queued nothing: redraws stopped");
  env.runFrames();
});

test("a tab hidden between the tick and its frame draws nothing", () => {
  const chart = adhocChart(3);
  tickFn();
  document.hidden = true;
  env.runFrames();
  assert.equal(chart.uplot, null, "a hidden tab drew the chart");
  document.hidden = false;
  tickFn();
  env.runFrames();
  assert.ok(chart.uplot, "visible again, the next tick did not draw (positive control)");
});
