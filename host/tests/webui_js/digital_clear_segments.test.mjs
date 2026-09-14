// digital.js after clear-all: a lane drew its first level from the left edge of the window, so
// the first post-clear sample read as a level held for the whole 30 s. A lane now starts at its
// first sample, as an analog trace does (timewindow.js laneSegments).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";
import { laneSegments, timeWindow, windowFor } from "../../mcuscope/webui/timewindow.js";

installDom();

const { state } = await import(webuiUrl("state.js"));
const dg = await import(webuiUrl("digital.js"));

const BIT = { kind: "bits", name: "f.b0" };
const ENUM = { kind: "enum", name: "mode", labels: [[0, "IDLE"], [1, "RUN"]] };
const W = 300;   // px

// ---- the projection, DOM-free ----------------------------------------------------------

test("a single vertex inside the window starts its segment at its own time", () => {
  const win = timeWindow("host", 30, 1030, W);             // 1000 .. 1030
  assert.deepEqual(laneSegments([1020], win), [{ i: 0, x0: 200, x1: W }]);
});

test("a level that began before the window still reaches the left edge", () => {
  const win = timeWindow("host", 30, 1030, W);
  assert.deepEqual(laneSegments([900, 1015], win), [{ i: 0, x0: 0, x1: 150 }, { i: 1, x0: 150, x1: W }]);
});

test("a zoom entirely before the first sample draws nothing; one straddling it starts there", () => {
  assert.deepEqual(laneSegments([1020], windowFor({ mode: "host", min: 900, max: 1000 }, "host", 30, 1030, W)), []);
  const z = windowFor({ mode: "host", min: 1010, max: 1030 }, "host", 30, 1030, W);
  assert.deepEqual(laneSegments([1020], z), [{ i: 0, x0: 150, x1: W }]);
  assert.deepEqual(laneSegments([1020], timeWindow("host", 300, 1030, W)), [{ i: 0, x0: 290, x1: W }],
    "a wider window moves the start, but never to the left edge");
  assert.deepEqual(laneSegments([], timeWindow("host", 30, 1030, W)), []);
});

// ---- through the panel: clear-all, then the draw calls ------------------------------------

// Replace the stub context's path calls with recorders; returns the log.
function record(lane) {
  const g = lane.canvas.getContext("2d");
  const log = [];
  for (const k of ["moveTo", "lineTo", "fillRect"]) g[k] = (...a) => log.push([k, ...a]);
  lane.canvas.clientWidth = W;
  return log;
}

function ingest(host, bit, mode) {
  dg.digitalIngest("p1", [["f.b0", bit, BIT], ["mode", mode, ENUM]], { host, tick: host * 1000 });
}

test("after clear-all a lane with no post-clear sample is gone, and the first one starts the trace", () => {
  state.timeMode = "host";
  dg.clearAllDigital();
  for (let t = 900; t <= 1005; t += 5) ingest(t, 1, 1);   // pre-clear: held high / RUN
  dg.clearAllDigital();
  assert.equal(dg.digitalLanes.size, 0, "no pre-clear value survives to be drawn");

  ingest(1020, 1, 1);
  ingest(1030, 1, 1);                                     // edge 1030: window 1000 .. 1030
  const bits = dg.digitalLanes.get("p1|f.b0"), mode = dg.digitalLanes.get("p1|mode");
  const bitLog = record(bits), modeLog = record(mode);
  dg.markDigitalDirty();
  const trace = (log) => log.filter((c) => c[0] === "moveTo" && c[2] !== 0);   // y 0: a gridline
  const bitMove = trace(bitLog)[0];
  assert.equal(bitMove[1], 200, "the square wave starts at the first post-clear sample, not x=0");
  assert.ok(bitLog.filter((c) => c[0] === "fillRect").every((c) => c[1] >= 200),
    "and so does its high fill");
  const railStart = Math.min(...trace(modeLog).map((c) => c[1]));
  assert.equal(railStart, 200, "the enum envelope opens at the first post-clear sample too");
});

test("a paused panel cleared starts its frozen and resumed traces at the first post-clear sample", () => {
  dg.clearAllDigital();
  for (let t = 900; t <= 1005; t += 5) ingest(t, 0, 0);
  dg.setDigitalPaused(true);
  dg.clearAllDigital();
  ingest(1020, 1, 1);                                     // re-anchors the freeze here
  ingest(1030, 0, 0);
  const lane = dg.digitalLanes.get("p1|f.b0");
  const frozen = dg.laneDrawData(lane);
  assert.deepEqual(laneSegments(frozen.xs, timeWindow("host", 30, dg.digitalRightEdge(), W)), [],
    "frozen at the first sample: its edge, so nothing earlier is drawn flat across the window");
  dg.setDigitalPaused(false);
  const live = dg.laneDrawData(lane);
  const segs = laneSegments(live.xs, timeWindow("host", 30, dg.digitalRightEdge(), W));
  assert.equal(segs[0].x0, 200, "resumed: the trace starts at 1020");
  dg.clearAllDigital();
});

test("tick mode: the same start, in the tick units", () => {
  dg.clearAllDigital();
  state.timeMode = "tick";
  ingest(1020, 1, 1);
  ingest(1030, 1, 1);
  const lane = dg.digitalLanes.get("p1|mode");
  const segs = laneSegments(dg.laneDrawData(lane).xs, timeWindow("tick", 30, dg.digitalRightEdge(), W));
  assert.equal(segs[0].x0, 200);
  state.timeMode = "host";
  dg.clearAllDigital();
});
