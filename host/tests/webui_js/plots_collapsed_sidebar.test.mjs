// plots.js and can.js: a hidden sidebar (#workspace.collapsed) draws nothing. Collapsed, each
// chart's canvas still reads a few px wide, so the 5 Hz redraw kept building and filling charts
// nobody could see, and the CAN table kept re-rendering into a zero-width column: collapsing the
// sidebar to save CPU saved none. Reopening draws on the next tick.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { sidebar } = await import(webuiUrl("state.js"));
const { charts, plotIngest, initPlots, clearAllCharts } = await import(webuiUrl("plots.js"));
const { canIngest, initCan } = await import(webuiUrl("can.js"));

initPlots();
initCan();
const plotTick = env.intervals.find((i) => i.ms === 200).fn;
const canTick = env.intervals.find((i) => i.ms === 1000 && String(i.fn).includes("canVisible")).fn;
const workspace = env.byId("workspace");

test("a collapsed sidebar builds and redraws no chart; reopening draws on the next tick", () => {
  clearAllCharts();
  sidebar.setAttribute("data-view", "both");
  plotIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!p 1 a=1" });
  const chart = charts.get("p1|adhoc");
  chart.canvasEl.clientWidth = 4;   // what a collapsed sidebar's chart reads
  workspace.classList.add("collapsed");
  plotTick();
  assert.equal(chart.uplot, null, "a chart was built into the hidden sidebar");
  workspace.classList.remove("collapsed");
  chart.canvasEl.clientWidth = 340;
  plotTick();
  assert.ok(chart.uplot, "reopened, the next tick must draw (positive control)");
});

test("a collapsed sidebar does not re-render the CAN table", () => {
  sidebar.setAttribute("data-view", "both");
  const wrap = env.byId("canWrap");
  canIngest({ id: 2, ts: 1000, port: "p1", chan: "event", raw: "!can 1 - 100 01" });
  canTick();
  const built = wrap.children[0];
  assert.equal(built.tagName, "TABLE", "setup: the table did not render while shown");
  canIngest({ id: 3, ts: 1001, port: "p1", chan: "event", raw: "!can 2 - 101 01" });   // a new row
  workspace.classList.add("collapsed");
  canTick();
  assert.equal(wrap.children[0], built, "the table re-rendered into the hidden sidebar");
  workspace.classList.remove("collapsed");
  canTick();
  assert.notEqual(wrap.children[0], built, "reopened, the next tick must render the new row");
});
