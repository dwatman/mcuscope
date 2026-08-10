// A paused digital panel must stay frozen, including after its rings rotate past the freeze.
//
// The pause pinned the shared right edge as a TIME only (digitalFrozen), while the per-lane
// vertex rings kept filling for the resume catch-up. Once a fast-toggling lane's ring rotated
// fully past that edge, any redraw while still paused (window change, resize, lane toggle)
// re-derived the "frozen" view from post-freeze data: visibleRange fell back to its edge
// clamps, drawBits painted a post-freeze level flat across the frozen window, and the cursor
// readouts went blank. pane.js took this care for a paused terminal pane (frozenRows) and
// plots.js for a paused chart (frozenLen); this is the digital sibling (REVIEW class 26).
// Per the class sweep the WHOLE ring rotates out: a partial rotation leaves pre-freeze
// vertices in place and passes on the bug. Assertions read laneDrawData, the seam every
// draw/cursor/readout path consumes, since a stubbed canvas has no pixels to inspect.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const { state, PLOT_CAP, PLOT_SLACK } = await import(webuiUrl("state.js"));
const { digitalIngest, digitalLanes, setDigitalPaused, laneDrawData, redrawDigital,
        refreshDigitalReadouts, setDigitalCursorAt } = await import(webuiUrl("digital.js"));

const BIT = { kind: "bits", name: "f" };
const ENUM = { kind: "enum", name: "mode", labels: [[0, "IDLE"], [1, "RUN"], [2, "ERR"]] };

// Every sample changes both values, so transition reduction stores every one: the fastest
// lane there is, which is exactly the lane whose ring rotates past a freeze first.
let t = 1000;
let n = 0;
function feed(count, lanes = null) {
  for (let i = 0; i < count; i++) {
    t += 0.001;
    const v = n++;
    state.maxId = n;
    digitalIngest(1, lanes || [["f.b0", v & 1, BIT], ["mode", v % 3, ENUM]],
                  { host: t, tick: t * 1000 });
  }
}

let frozenB0 = null;    // laneDrawData(b0) captured at pause
let frozenEdge = 0;     // host time of the newest frozen vertex

test("pausing snapshots the vertices the freeze covers", () => {
  feed(10);
  assert.equal(digitalLanes.size, 2, "the two lanes must have been built");
  setDigitalPaused(true);

  const b0 = digitalLanes.get("f.b0");
  const d = laneDrawData(b0);
  frozenB0 = { xs: [...d.xs], vs: [...d.vs] };
  frozenEdge = frozenB0.xs[frozenB0.xs.length - 1];
  assert.equal(frozenB0.xs.length, 10, "the freeze must cover every held vertex");
});

test("the frozen view survives the whole ring rotating past the freeze", () => {
  // Drive the live rings until nothing from before the pause is left in them.
  feed(PLOT_CAP + PLOT_SLACK + 64);
  const b0 = digitalLanes.get("f.b0");
  assert.ok(b0.xsHost.length <= PLOT_CAP + PLOT_SLACK, "the ring must have trimmed");
  assert.ok(b0.xsHost[0] > frozenEdge,
    "precondition: the whole ring must sit past the freeze, or this test passes on the bug");

  // A paused redraw (what a resize / window change / lane toggle triggers). The stub's
  // canvas has no layout, so give it a width for the draw path to run end to end.
  for (const l of digitalLanes.values()) { l.canvas.clientWidth = 400; l.dirty = true; }
  assert.equal(redrawDigital(), true, "the paused redraw must still repaint");

  const d = laneDrawData(b0);
  assert.deepEqual([...d.xs], frozenB0.xs,
    "the drawn view moved: it was re-derived from a ring that rotated past the freeze");
  assert.deepEqual([...d.vs], frozenB0.vs, "the drawn levels must be the frozen ones");
});

test("readouts and cursor scrub read the frozen data, not the rotated ring", () => {
  const b0 = digitalLanes.get("f.b0");
  const mode = digitalLanes.get("mode");

  // Held value at the frozen edge (feed's v ran 0..9 before the pause, so 9 and 9 % 3).
  refreshDigitalReadouts();
  assert.equal(b0.valEl.textContent, "1",
    "the readout went blank: the frozen edge predates everything left in the live ring");
  assert.equal(mode.valEl.textContent, "IDLE");

  // Scrub to a mid-window frozen transition; the snap and the held value must both come
  // from the snapshot (v was 4 there: bit 0, enum RUN via 4 % 3 = 1).
  setDigitalCursorAt(frozenB0.xs[4]);
  assert.equal(b0.valEl.textContent, "0");
  assert.equal(mode.valEl.textContent, "RUN");
});

test("a lane born while paused draws nothing into the frozen view", () => {
  feed(3, [["g.b1", 1, { kind: "bits", name: "g" }]]);
  const late = digitalLanes.get("g.b1");
  assert.ok(late, "the lane itself must still be created (it fills for the resume)");
  assert.equal(late.xsHost.length, 1, "transition-reduced: one held vertex in the live ring");
  assert.equal(laneDrawData(late).xs.length, 0,
    "the freeze predates this lane, so the paused view must hold nothing for it");
});

test("resuming drops the snapshots and returns to the live rings", () => {
  setDigitalPaused(false);
  const b0 = digitalLanes.get("f.b0");
  assert.equal(b0.frozen, null, "a live lane must not keep a stale snapshot around");
  const d = laneDrawData(b0);
  assert.equal(d.xs, b0.xsHost, "live draws must read the ring itself, not a copy");
  assert.equal(d.vs, b0.vs);
  assert.ok(d.xs[0] > frozenEdge, "everything buffered while frozen is now the view");
});
