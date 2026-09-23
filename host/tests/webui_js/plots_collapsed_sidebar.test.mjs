// plots.js and can.js: a hidden sidebar draws nothing. Collapsed, each chart's canvas still reads
// a few px wide, so the 5 Hz redraw kept building and filling charts nobody could see, and the
// CAN table kept re-rendering into a zero-width column: collapsing the sidebar to save CPU saved
// none. Reopening draws on the next tick.
// Hidden is the sidebar's laid-out width, not #workspace.collapsed: below 860 px the layout is one
// column that ignores the class, so a hidden sidebar saved at a wide window is on screen there,
// and a class gate left its charts and CAN table undrawn with no control to undo it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { sidebar } = await import(webuiUrl("state.js"));
const { charts, plotIngest, clearAllCharts } = await import(webuiUrl("plots.js"));
const { canIngest, clearAllCan } = await import(webuiUrl("can.js"));

await import(webuiUrl("app.js"));   // runs initPlots and initCan, and adds its own visibilitychange handler
const plotTick = env.intervals.find((i) => i.ms === 200).fn;
const canTick = env.intervals.find((i) => i.ms === 1000 && String(i.fn).includes("canVisible")).fn;
const workspace = env.byId("workspace");

// The three layouts, as the browser lays them out (style.css .workspace.collapsed and the
// max-width: 860px block).
const shown = () => { workspace.classList.remove("collapsed"); sidebar.clientWidth = 340; };
const collapsedWide = () => { workspace.classList.add("collapsed"); sidebar.clientWidth = 0; };
const collapsedNarrow = () => { workspace.classList.add("collapsed"); sidebar.clientWidth = 800; };

function adhocChart(id) {
  clearAllCharts();
  sidebar.setAttribute("data-view", "both");
  plotIngest({ id, ts: 1000, port: "p1", chan: "event", raw: "!p 1 a=1" });
  return charts.get("p1|adhoc");
}

test("a collapsed sidebar builds and redraws no chart; reopening draws on the next tick", () => {
  const chart = adhocChart(1);
  chart.canvasEl.clientWidth = 4;   // what a collapsed sidebar's chart reads
  collapsedWide();
  plotTick();
  assert.equal(chart.uplot, null, "a chart was built into the hidden sidebar");
  shown();
  chart.canvasEl.clientWidth = 340;
  plotTick();
  assert.ok(chart.uplot, "reopened, the next tick must draw (positive control)");
});

test("below 860 px a saved hidden sidebar is on screen, and its charts draw", () => {
  const chart = adhocChart(2);
  chart.canvasEl.clientWidth = 780;
  collapsedNarrow();
  plotTick();
  assert.ok(chart.uplot, "the narrow layout shows the sidebar, yet no chart was built");
  shown();
});

test("a tab returning to visible repaints the charts only when the sidebar is on screen", () => {
  const chart = adhocChart(3);
  chart.canvasEl.clientWidth = 4;
  collapsedWide();
  env.document.emit("visibilitychange");
  assert.equal(chart.uplot, null, "the tab refocus drew into the hidden sidebar");
  shown();
  chart.canvasEl.clientWidth = 340;
  env.document.emit("visibilitychange");
  assert.ok(chart.uplot, "shown, the refocus must draw (positive control)");
});

function canTable() {
  clearAllCan();
  sidebar.setAttribute("data-view", "both");
  shown();
  const wrap = env.byId("canWrap");
  canIngest({ id: 10, ts: 1000, port: "p1", chan: "event", raw: "!can 1 - 100 01" });
  canTick();
  const built = wrap.children[0];
  assert.equal(built.tagName, "TABLE", "setup: the table did not render while shown");
  canIngest({ id: 11, ts: 1001, port: "p1", chan: "event", raw: "!can 2 - 101 01" });   // a new row
  return { wrap, built };
}

test("a collapsed sidebar does not re-render the CAN table", () => {
  const { wrap, built } = canTable();
  collapsedWide();
  canTick();
  assert.equal(wrap.children[0], built, "the table re-rendered into the hidden sidebar");
  shown();
  canTick();
  assert.notEqual(wrap.children[0], built, "reopened, the next tick must render the new row");
});

test("below 860 px a saved hidden sidebar still re-renders the CAN table", () => {
  const { wrap, built } = canTable();
  collapsedNarrow();
  canTick();
  assert.notEqual(wrap.children[0], built, "the narrow layout shows the table, yet it did not render");
  shown();
});

test("a tab returning to visible repaints the CAN table only when the sidebar is on screen", () => {
  const { wrap, built } = canTable();
  collapsedWide();
  env.document.emit("visibilitychange");
  assert.equal(wrap.children[0], built, "the tab refocus rendered the CAN table into the hidden sidebar");
  shown();
  env.document.emit("visibilitychange");
  assert.notEqual(wrap.children[0], built, "shown, the refocus must render the new row (positive control)");
});
