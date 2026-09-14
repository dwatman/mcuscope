// The digital lanes draw the shared drag zoom, not their own tail window.
//
// digital.js never saw the zoom at all: it projected with timeWindow(timeMode, windowSec,
// digitalRightEdge(), w) while the chart the drag happened on used its own zoom range, so the
// linked cursor landed at the right TIME on a lane and at a different PIXEL from the chart it
// was dragged on. The drawing itself needs a laid-out canvas and is manual; the cursor
// projection is the same arithmetic and reaches through setDigitalCursorAt, so it is what is
// pinned here (a canvas width is a plain property the stub will hold).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { setZoom, getZoom } = await import(webuiUrl("timewindow.js"));
const { plotIngest } = await import(webuiUrl("plots.js"));
const { digitalLanes, setDigitalPaused, setDigitalCursorAt } = await import(webuiUrl("digital.js"));

let nextId = 0;
let ts = 1000;
function ingest(raw) {
  const row = { id: ++nextId, ts: (ts += 0.1), port: "p1", chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
}

ingest("!pd 0 f:u1:/b0");
const vertices = [];
for (let i = 0; i < 20; i++) {
  ingest(`!ps 0 ${(i + 1).toString(16)} 0${i % 2}`);   // one transition per sample
  vertices.push(ts);
}

// A laid-out panel: 400 px of canvas behind a 40 px name/value gutter.
const lane = [...digitalLanes.values()][0];
lane.canvas.clientWidth = 400;
env.byId("digitalWrap").clientWidth = 440;

const cursorPx = (t) => {
  const snapped = setDigitalCursorAt(t);
  assert.equal(env.byId("dCursor").hidden, false, "the cursor must be on screen for " + t);
  return [parseFloat(env.byId("dCursor").style.left), snapped];
};

test("with no zoom a lane projects its right-anchored window", () => {
  const edge = vertices.at(-1);
  const [px, snapped] = cursorPx(edge);
  assert.equal(snapped, edge, "the edge is a vertex, so the snap is a no-op");
  assert.ok(Math.abs(px - 440) < 0.01, `the newest sample sits at the right edge, got ${px}`);
});

test("a zoom the charts are frozen on is the window the lanes draw too", () => {
  const v = vertices[10];
  setZoom({ mode: "host", min: v - 0.5, max: v + 0.5 });
  setDigitalPaused(true);
  const [px, snapped] = cursorPx(v);
  assert.equal(snapped, v);
  assert.ok(Math.abs(px - 240) < 0.5,
    `a vertex at the middle of the zoom must draw at the middle of the canvas, got ${px}`);
});

test("a zoom recorded in another time mode does not move the lanes", () => {
  const v = vertices[10];
  state.timeMode = "tick";
  try {
    const px = parseFloat(env.byId("dCursor").style.left);
    setDigitalCursorAt(v);
    assert.notEqual(parseFloat(env.byId("dCursor").style.left), px,
      "tick mode reads the tick array, so the host-second range cannot still be in force");
  } finally {
    state.timeMode = "host";
  }
});

test("a live panel ignores the zoom, so it cannot draw a frozen window while scrolling", () => {
  assert.ok(getZoom(), "the zoom must stand going in, or this passes without it");
  // Resuming leaves the zoom first (chrome.js leaveZoom), and only a drag sets one, which pauses
  // the panel: a live panel under a standing zoom is not reachable, so the draw guard in
  // laneWindow is belt and braces for this path.
  setDigitalPaused(false);
  assert.equal(getZoom(), null, "resuming the lanes leaves the zoom");
  const [px] = cursorPx(vertices.at(-1));
  assert.ok(Math.abs(px - 440) < 0.01, `back on the tail window, got ${px}`);
  setZoom(null);
});
