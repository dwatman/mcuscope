// A drag zoom's visible state and its ways out (SPEC 9.2). The zoom froze every surface while
// every head still lit `30s`, and the only exits were an undocumented double-click or a
// chart's resume, which left the other panels frozen.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { getZoom } = await import(webuiUrl("timewindow.js"));
const { charts, plotIngest, setChartPaused, onSelect, clearAllCharts } = await import(webuiUrl("plots.js"));
const { isDigitalPaused, buildDigitalHead, setDigitalPaused, initDigitalCursorSync } =
  await import(webuiUrl("digital.js"));
const { anyLive, pauseAll } = await import(webuiUrl("freeze.js"));
buildDigitalHead();
initDigitalCursorSync();

let nextId = 0;
function feed(n) {
  for (let i = 0; i < n; i++) {
    const id = ++nextId, ts = 1000 + id;
    state.maxId = id;
    const hex = (id * 1000).toString(16).toUpperCase();
    plotIngest({ id, ts, port: "p1", chan: "event", raw: `!ps 0 ${hex} 0001` });
    plotIngest({ id, ts, port: "p1", chan: "event", raw: `!ps 1 ${hex} 0${id % 2}` });
    plotIngest({ id, ts, port: "p2", chan: "event", raw: `!ps 0 ${hex} 0002` });
  }
}

function fresh() {
  pauseAll(false);
  clearAllCharts();
  plotIngest({ id: ++nextId, ts: 1000, port: "p1", chan: "event", raw: "!pd 0 v:u2" });
  plotIngest({ id: ++nextId, ts: 1000, port: "p1", chan: "event", raw: "!pd 1 s:u1:=0=LO,1=HI" });
  plotIngest({ id: ++nextId, ts: 1000, port: "p2", chan: "event", raw: "!pd 0 v:u2" });
  feed(40);
}

// uPlot's selection over pixels 20..30 of a 0..100 px axis mapping onto the fed host times.
function drag(chart) {
  const x0 = 1000 + nextId - 30;
  onSelect(chart, {
    select: { left: 20, top: 0, width: 10, height: 10 },
    posToVal: (px) => x0 + px / 10,
    setSelect: () => {},
  });
}

const heads = () => [...[...charts.values()].map((c) => c.winEl), env.byId("digitalHead").querySelector(".plot-win")];
const chipOf = (win) => win.children.find((b) => b.className === "zoom");
const lit = (win) => win.children.filter((b) => b.className !== "zoom" && b.classList.contains("on")).map((b) => b.textContent);

test("a zoom names its span on every head and unlights every window button", () => {
  fresh();
  drag(charts.get("p1|s0"));
  assert.ok(getZoom(), "the drag did not zoom");
  const all = heads();
  assert.equal(all.length, 3, "two charts and the digital head");
  for (const win of all) {
    assert.equal(chipOf(win).hidden, false, "every head must show the zoom chip");
    assert.equal(chipOf(win).textContent, "1.00 s ×");
    assert.deepEqual(lit(win), [], "no head may still claim its 30s window");
  }
});

test("a window button clicked while zoomed leaves the zoom but keeps the freeze", () => {
  fresh();
  const chart = charts.get("p1|s0");
  drag(chart);
  chart.winEl.children[0].emit("click", {});
  assert.equal(getZoom(), null, "picking a span must end the zoom that hid it");
  assert.equal(chart.window, 5);
  assert.equal(chart.paused, true, "a span is not a resume");
  assert.equal(isDigitalPaused(), true);
  for (const win of heads()) assert.equal(chipOf(win).hidden, true, "the chip goes from every head");
  assert.deepEqual(lit(chart.winEl), ["5s"]);
  assert.deepEqual(lit(charts.get("p2|s0").winEl), ["30s"], "the other chart relights its own span");
});

test("the chip's x on one chart releases every surface", () => {
  fresh();
  drag(charts.get("p2|s0"));
  assert.equal(anyLive(), false, "the zoom froze everything");
  chipOf(charts.get("p1|s0").winEl).emit("click", {});
  assert.equal(getZoom(), null);
  assert.equal([...charts.values()].every((c) => !c.paused), true, "every chart resumes");
  assert.equal(isDigitalPaused(), false, "and the lanes");
  for (const win of heads()) {
    assert.equal(chipOf(win).hidden, true);
    assert.deepEqual(lit(win), ["30s"]);
  }
});

test("a zoom made while already paused goes when everything resumes", () => {
  fresh();
  pauseAll(true);
  drag(charts.get("p1|s0"));
  assert.ok(getZoom());
  pauseAll(false);
  assert.equal(getZoom(), null, "resume all follows the tail again");
  for (const win of heads()) assert.equal(chipOf(win).hidden, true, "and no head keeps a stale chip");
});

test("resuming the lanes alone drops the zoom, as resuming a chart does", () => {
  fresh();
  drag(charts.get("p1|s0"));
  setDigitalPaused(false);
  assert.equal(getZoom(), null);
  assert.equal(charts.get("p1|s0").paused, true, "the charts stay frozen where they were");
  for (const win of heads()) assert.equal(chipOf(win).hidden, true);
});

test("a chart resumed on its own clears the chips", () => {
  fresh();
  drag(charts.get("p1|s0"));
  setChartPaused(charts.get("p2|s0"), false);
  assert.equal(getZoom(), null);
  for (const win of heads()) assert.equal(chipOf(win).hidden, true);
});

test("double-clicking the lanes exits through the same door as the chip", () => {
  fresh();
  drag(charts.get("p1|s0"));
  env.byId("digitalWrap").emit("dblclick");
  assert.equal(getZoom(), null);
  assert.equal(anyLive(), true);
  for (const win of heads()) assert.equal(chipOf(win).hidden, true);
});
