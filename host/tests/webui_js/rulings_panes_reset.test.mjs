// Owner ruling D-3 (2026-09-15) through the charts, the lanes and the hover: after an MCU reset
// the tick axis continues by the host-time gap and the line breaks at the reset.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const { state, tickAnchors } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));

let nextId = 0;
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  const r = { id: nextId, ts, port, chan: "event", raw };
  P.plotIngest(r);
  return r;
}
const hex = (n) => n.toString(16).padStart(8, "0");

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
}

// Stream 0: an analog `v` and an enum `m` (0 IDLE, 1 RUN, 2 ERR). 10 samples at 100 Hz on
// ticks 1000000.., the board resets, 10 more on ticks 0.. starting 20 s later.
const PRE_LAST_TICK = 1000090, PRE_LAST_HOST = 100.09, POST_HOST = 120;
function streamAcrossReset() {
  row("!pd 0 v:u2 m:u1:=0=IDLE,1=RUN,2=ERR", 99);
  const rows = [];
  for (let i = 0; i < 10; i++) rows.push(row(`!ps 0 ${hex(1000000 + i * 10)} ${hex(i).slice(4)},${i < 5 ? "00" : "01"}`, 100 + i * 0.01));
  for (let i = 0; i < 10; i++) rows.push(row(`!ps 0 ${hex(i * 10)} ${hex(100 + i).slice(4)},02`, POST_HOST + i * 0.01));
  return rows;
}
const CONTINUED = PRE_LAST_TICK + (POST_HOST - PRE_LAST_HOST) * 1000;   // first post-reset x

test("a chart continues its tick axis by the host gap and breaks its line at the reset", () => {
  resetAll();
  streamAcrossReset();
  const c = P.charts.get("p1|s0");
  assert.equal(c.xsTick.length, 21, "20 samples and one gap point");
  assert.equal(c.ys.get("v")[10], null, "the gap point must be null, or uPlot joins across the reset");
  assert.ok(Math.abs(c.xsTick[11] - CONTINUED) < 1e-6, `first post-reset x ${c.xsTick[11]}`);
  assert.ok(Math.abs(c.xsTick[20] - c.xsTick[11] - 90) < 1e-6, "the post-reset 90 ms must stay 90 ms");
  state.timeMode = "tick";
  const [xs, v] = P.currentData(c);
  assert.ok(v.includes(null), "the drawn slice must carry the break");
  assert.equal(xs.at(-1), c.xsTick[20]);
  state.timeMode = "host";
});

test("a repeated tick is nudged, not taken for a reset: no gap point", () => {
  resetAll();
  row("!pd 0 v:u2", 1);
  row("!ps 0 00001000 0001", 2);
  row("!ps 0 00001000 0002", 2);
  row("!ps 0 00000FC0 0003", 2.1);   // 64 ms back: inside the slack
  const c = P.charts.get("p1|s0");
  assert.equal(c.xsTick.length, 3);
  assert.deepEqual(c.ys.get("v"), [1, 2, 3]);
  assert.ok(c.xsTick[2] > c.xsTick[1] && c.xsTick[2] - 0x1000 < 1e-3, "the old nudge still applies");
});

test("the lanes continue too: a null vertex at the reset and a right edge that keeps moving", () => {
  resetAll();
  streamAcrossReset();
  const lane = D.digitalLanes.get("p1|m");
  // IDLE@1000000, RUN@1000050, null at the last pre-reset sample, ERR at the first post-reset one.
  assert.deepEqual(lane.vs, [0, 1, null, 2]);
  assert.equal(lane.xsTick[2], PRE_LAST_TICK);
  assert.ok(Math.abs(lane.xsTick[3] - CONTINUED) < 1e-6);
  state.timeMode = "tick";
  assert.ok(Math.abs(D.digitalRightEdge() - (CONTINUED + 90)) < 1e-6,
    `the lanes' live edge stopped at ${D.digitalRightEdge()}`);
  state.timeMode = "host";
});

// A recording 2d context: the stub's own one draws nothing it can report.
function recorder() {
  const calls = [];
  return { calls, ctx: new Proxy({}, {
    get: (t, k) => (k in t ? t[k] : (...a) => calls.push([k, ...a])),
    set: (t, k, v) => { t[k] = v; return true; },
  }) };
}

test("a bits lane lifts the pen at the reset, and an enum lane draws no bus for it", () => {
  resetAll();
  row("!pd 1 f:u1:/b0 e:u1:=0=OFF,1=ON", 1);
  // Wide enough at 10 px a second for a label either side and a 100 px gap between.
  row(`!ps 1 ${hex(0x100000 - 3000)} 01,01`, 7);
  row("!ps 1 00100000 01,01", 10);
  row("!ps 1 00000005 01,01", 20);     // reset; same values on both sides
  row(`!ps 1 ${hex(4005)} 01,01`, 24);
  state.timeMode = "tick";
  env.byId("digitalWrap").clientWidth = 340;
  const bit = D.digitalLanes.get("p1|b0"), en = D.digitalLanes.get("p1|e");
  const rb = recorder(), re = recorder();
  bit.canvas.getContext = () => rb.ctx; bit.canvas.clientWidth = 300;
  en.canvas.getContext = () => re.ctx; en.canvas.clientWidth = 300;
  D.setDigitalCursorAt(0);   // any cursor; the draw is what is read
  D.markDigitalDirty();
  state.timeMode = "host";
  const moves = rb.calls.filter((c) => c[0] === "moveTo" && c[2] !== 0).length;   // gridlines start at y 0
  assert.equal(moves, 2, "one stroke either side of the reset, not one joined across it");
  const drops = rb.calls.filter((c) => c[0] === "lineTo" && c[2] === 26);        // y of a low level
  assert.deepEqual(drops, [], "a high lane drew an edge down into the gap");
  const labels = re.calls.filter((c) => c[0] === "fillText").map((c) => c[1]);
  assert.deepEqual(labels, ["ON", "ON"], "an enum bus was drawn across the gap");
});

// A hover over a terminal line: the pane's hit test resolves to its row.
function hoverRow(r) {
  env.document.elementFromPoint = () => ({ closest: () => ({ __row: r }) });
  P.paneMouseMove({ clientX: Math.random(), clientY: 5 });
  env.frames.splice(0).forEach((f) => f());
}

test("a hovered terminal line maps through the same offset, before and after the reset", () => {
  resetAll();
  const rows = streamAcrossReset();
  state.timeMode = "tick";
  const lane = D.digitalLanes.get("p1|m");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  try {
    hoverRow(rows[15]);   // raw tick 50, drawn past the reset
    assert.equal(lane.valEl.textContent, "ERR", "a raw post-reset tick sits before every vertex");
    hoverRow(rows[7]);    // pre-reset: no offset
    assert.equal(lane.valEl.textContent, "RUN");
    // A line with no tick of its own, after the reset: its estimate plus the offset.
    TW.noteTickAnchor(tickAnchors, "p1", rows[19].id, rows[19].ts, 90);
    hoverRow({ id: ++nextId, ts: POST_HOST + 1, port: "p1", chan: "debug", raw: "boot ok" });
    assert.equal(lane.valEl.textContent, "ERR");
  } finally {
    P.paneMouseLeave();
    state.timeMode = "host";
    env.document.elementFromPoint = () => null;
  }
});

test("a reset while paused leaves the frozen view alone, and resume shows the continued axis", () => {
  resetAll();
  row("!pd 0 v:u2 m:u1:=0=IDLE,1=RUN,2=ERR", 99);
  for (let i = 0; i < 10; i++) row(`!ps 0 ${hex(1000000 + i * 10)} 0001,01`, 100 + i * 0.01);
  F.pauseAll(true);
  const c = P.charts.get("p1|s0"), lane = D.digitalLanes.get("p1|m");
  const frozenTicks = [...c.frozen.xsTick];
  for (let i = 0; i < 10; i++) row(`!ps 0 ${hex(i * 10)} 0002,02`, POST_HOST + i * 0.01);
  assert.deepEqual(P.chartDrawData(c).xsTick, frozenTicks, "the reset reached the frozen chart");
  state.timeMode = "tick";
  assert.equal(D.digitalRightEdge(), PRE_LAST_TICK, "the frozen lanes' edge moved");
  assert.deepEqual(D.laneDrawData(lane).vs, [1]);
  F.pauseAll(false);
  assert.ok(Math.abs(D.digitalRightEdge() - (CONTINUED + 90)) < 1e-6);
  assert.deepEqual(D.laneDrawData(lane).vs, [1, null, 2]);
  const [xs, v] = P.currentData(c);
  assert.ok(Math.abs(xs.at(-1) - (CONTINUED + 90)) < 1e-6);
  assert.equal(v.filter((y) => y === null).length, 1);
  state.timeMode = "host";
});

test("a stream born after the reset joins the continued axis, beside the one that saw it", () => {
  resetAll();
  streamAcrossReset();
  row("!pd 1 w:u2 k:u1:=0=A,1=B", 121);
  row(`!ps 1 ${hex(1200)} 0005,01`, POST_HOST + 1.2);
  const born = P.charts.get("p1|s1");
  assert.ok(Math.abs(born.xsTick[0] - (CONTINUED + 1200)) < 1e-6,
    `a chart born after the reset drew at raw tick ${born.xsTick[0]}`);
  const k = D.digitalLanes.get("p1|k");
  assert.ok(Math.abs(k.xsTick[0] - (CONTINUED + 1200)) < 1e-6, "a lane born after it would sit off screen");
  assert.deepEqual(born.ys.get("w"), [5], "nothing to break on a member's first sample");
});

test("clear-all after a reset starts the axis over at raw ticks, for charts, lanes and the hover", () => {
  resetAll();
  streamAcrossReset();
  P.clearAllCharts();
  D.clearAllDigital();
  const r = row(`!ps 0 ${hex(300)} 0007,01`, POST_HOST + 5);
  assert.equal(P.charts.get("p1|s0").xsTick[0], 300);
  assert.equal(D.digitalLanes.get("p1|m").xsTick[0], 300);
  state.timeMode = "tick";
  const lane = D.digitalLanes.get("p1|m");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  try {
    hoverRow(r);
    assert.equal(lane.valEl.textContent, "RUN", "the hover kept the cleared offset");
  } finally {
    P.paneMouseLeave();
    state.timeMode = "host";
    env.document.elementFromPoint = () => null;
  }
});

test("a sample only for lanes past the lane cap still moves the tick edge, on the port's offset", () => {
  resetAll();
  const names = (p) => Array.from({ length: 32 }, (_, i) => p + i).join(",");
  row(`!pd 5 a:u4:/${names("l")} b:u4:/${names("k")}`, 9);
  row(`!ps 5 ${hex(1000000)} 00000000,00000000`, 10);
  assert.equal(D.digitalLanes.size, 64);
  row(`!ps 5 ${hex(5)} 00000001,00000000`, 20);   // reset
  row("!pd 6 c:u1:/n0", 20.5);
  row(`!ps 6 ${hex(1005)} 01`, 21);                 // capped: no lane takes it
  assert.equal(D.digitalLanes.size, 64);
  state.timeMode = "tick";
  assert.equal(D.digitalRightEdge(), 1000000 + 10000 + 1000, "the edge stalled on a capped sample");
  state.timeMode = "host";
});
