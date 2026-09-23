// plots.js syncFoldCue: the "↓ N below" cue forces a layout (a rect per chart) and wrote its
// hidden and title every time, and a same-value write still invalidates style. It ran from the
// 5 Hz redraw tick, so an idle page with charts recalculated style 5 times a second. It now runs
// when the fold can move (a scroll, or a size change of the scroller or its content, through a
// ResizeObserver) and writes only what changed.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

// A ResizeObserver whose callback the test fires, as the browser does after a layout.
const observers = [];
globalThis.ResizeObserver = class {
  constructor(cb) { this.cb = cb; this.els = []; observers.push(this); }
  observe(el) { this.els.push(el); }
  fire() { this.cb([]); }
};

const { sidebar } = await import(webuiUrl("state.js"));
const { charts, plotIngest, initPlots, clearAllCharts, renameChart } = await import(webuiUrl("plots.js"));
initPlots();
const tickFn = env.intervals.find((i) => i.ms === 200).fn;

const rect = (top, height = 30) => () => ({ top, bottom: top + height, height, left: 0, right: 0, width: 0 });
const scroller = env.byId("plotsScroll");
const btn = env.byId("plotFold");

// Count reads of the scroller's rect (the forced layout) and writes to the button.
let layouts = 0;
const writes = { hidden: 0, title: 0 };
function watch() {
  scroller.getBoundingClientRect = () => { layouts += 1; return rect(100, 400)(); };
  for (const k of ["hidden", "title"]) {
    let v = btn[k];
    Object.defineProperty(btn, k, { configurable: true, get: () => v, set: (x) => { writes[k] += 1; v = x; } });
  }
}

function twoCharts() {
  clearAllCharts();
  sidebar.setAttribute("data-view", "both");
  sidebar.clientWidth = 300;   // shown, or the tick returns before anything the tests watch
  plotIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!p 1 a=1" });
  plotIngest({ id: 2, ts: 1000, port: "p1", chan: "event", raw: "!pd 0 v:u2" });
  plotIngest({ id: 3, ts: 1000, port: "p1", chan: "event", raw: "!ps 0 1 0001" });
  charts.get("p1|adhoc").el.getBoundingClientRect = rect(110);
  charts.get("p1|s0").el.getBoundingClientRect = rect(700);   // below the fold
}

test("the redraw tick does not run the cue", () => {
  twoCharts();
  watch();
  layouts = 0;
  for (let i = 0; i < 5; i++) tickFn();
  assert.equal(layouts, 0, "the 5 Hz tick forced a layout for the cue");
});

test("a size change reported by the ResizeObserver updates the cue", () => {
  twoCharts();
  watch();
  const ro = observers.at(-1);
  assert.ok(ro, "no ResizeObserver was set up");
  assert.deepEqual(ro.els.map((e) => e.id).sort(), ["digitalHead", "digitalWrap", "plotCharts", "plotsScroll"]);
  ro.fire();
  assert.equal(btn.textContent, "↓ 1 below");
  assert.equal(btn.hidden, false);
  charts.get("p1|s0").el.getBoundingClientRect = rect(200);
  ro.fire();
  assert.equal(btn.hidden, true, "back in view: the cue must go");
});

test("an unchanged cue writes nothing", () => {
  twoCharts();
  watch();
  scroller.emit("scroll");
  writes.hidden = writes.title = 0;
  for (let i = 0; i < 3; i++) scroller.emit("scroll");
  assert.deepEqual(writes, { hidden: 0, title: 0 }, "a same-value write still invalidates style");
});

test("renaming a chart below the fold renames it in the cue", () => {
  twoCharts();
  watch();
  scroller.emit("scroll");
  renameChart(charts.get("p1|s0"), "motor");
  assert.ok(btn.title.includes("motor"), btn.title);
});
