// digital.js: what the 200 ms redraw tick may clear, and what it may write.
//
// Two defects in redrawDigital, both invisible in the DOM stub until they are driven:
//
//  - markDigitalDirty set _sizedirty on every lane, called redrawDigital, then cleared the
//    flag on every lane - including the lanes redrawDigital had skipped because the panel was
//    hidden (clientWidth 0). A time-base change made from the CAN view was therefore dropped,
//    and the lane kept the waveform it drew in the old time base once the panel came back.
//    The analog charts are not affected: redrawPlots continues before touching chart.dirty.
//
//  - the pendingVal write sat ABOVE the dirty check, so it ran on every tick for every visible
//    lane and overwrote the value setDigitalCursorAt had put in the gutter. redrawTick
//    re-applies the cursor only when something moved, so on an idle tick the live value is
//    what stayed on screen, beside a cursor line drawn at an earlier time.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { state } = await import(webuiUrl("state.js"));
const dg = await import(webuiUrl("digital.js"));

// Count real repaints by spying on the 2d context (drawDigitalLane's first act).
function countDraws(lane) {
  const real = lane.canvas.getContext.bind(lane.canvas);
  const n = { draws: 0 };
  lane.canvas.getContext = (k) => { n.draws++; return real(k); };
  return n;
}

test("a repaint requested while the panel is hidden survives until the panel is shown", () => {
  const ch = { kind: "bits", name: "gpio", labels: null };
  dg.digitalIngest("0", [["led", 0, ch]], { host: 1000, tick: 10 });
  dg.digitalIngest("0", [["led", 1, ch]], { host: 1001, tick: 20 });
  const lane = dg.digitalLanes.get("led");
  const n = countDraws(lane);

  lane.canvas.clientWidth = 200;     // panel visible
  dg.redrawDigital();                // baseline paint on the host time base
  assert.equal(n.draws, 1, "the baseline paint did not happen; the rest proves nothing");

  // The sidebar switches to the CAN view (the plots section is display:none), and the shared
  // #timeSeg control changes the time base while the panel is hidden.
  lane.canvas.clientWidth = 0;
  state.timeMode = "tick";
  dg.markDigitalDirty();             // terminal.js setTimeMode calls exactly this
  assert.equal(n.draws, 1, "a lane with no width cannot paint");
  assert.equal(lane._sizedirty, true,
    "the repaint request must outlive the hidden panel, or the lane keeps the old time base");

  // Back to the Plots view: same sidebar width, so nothing else marks the lane.
  lane.canvas.clientWidth = 200;
  dg.redrawDigital();
  assert.equal(n.draws, 2, "the lane must repaint on the new time base when it is shown again");
  assert.equal(lane._sizedirty, false, "and the request is spent by the paint that served it");
  state.timeMode = "host";
});

test("an idle tick does not clobber the cursor readout with the live value", () => {
  const ch = { kind: "enum", name: "st", labels: [[0, "IDLE"], [1, "RUN"]] };
  dg.digitalIngest("1", [["st", 0, ch]], { host: 2000, tick: 10 });
  dg.digitalIngest("1", [["st", 1, ch]], { host: 2002, tick: 30 });
  const lane = dg.digitalLanes.get("st");
  lane.canvas.clientWidth = 200;
  dg.redrawDigital();
  assert.equal(lane.valEl.textContent, "RUN", "the live edge value before the pointer arrives");

  dg.setDigitalCursorAt(2000.5);     // pointer rests over an earlier time
  assert.equal(lane.valEl.textContent, "IDLE", "the readout must show the value at the cursor");

  dg.redrawDigital();                // the 200 ms tick, no new samples, pointer unmoved
  assert.equal(lane.valEl.textContent, "IDLE",
    "an idle tick must leave the cursor readout alone; redrawTick will not re-apply it");
});

const ENUM_CH = { kind: "enum", name: "st", labels: [[0, "IDLE"], [1, "RUN"]] };

test("a repainting lane still tracks the live value while a cursor is up", () => {
  const lane = dg.digitalLanes.get("st");
  dg.setDigitalCursorAt(2002.5);     // parked on RUN this time, so the two values differ
  assert.equal(lane.valEl.textContent, "RUN");
  dg.digitalIngest("1", [["st", 0, ENUM_CH]], { host: 2004, tick: 50 });   // sets lane.dirty
  dg.redrawDigital();
  assert.equal(lane.valEl.textContent, "IDLE",
    "a lane that actually repaints writes pendingVal; redrawTick re-pins the cursor after it");
});

test("leaving the panel returns every readout to the live edge", () => {
  const lane = dg.digitalLanes.get("st");
  dg.setDigitalCursorAt(2002.5);
  assert.equal(lane.valEl.textContent, "RUN", "the cursor readout is up again");
  dg.refreshDigitalReadouts();       // what clearHoverCursor calls on mouseleave
  assert.equal(lane.valEl.textContent, "IDLE", "mouseleave snaps back to the live edge");
  dg.digitalIngest("1", [["st", 1, ENUM_CH]], { host: 2006, tick: 70 });
  dg.redrawDigital();
  assert.equal(lane.valEl.textContent, "RUN",
    "with no cursor showing the tick must write the live value again");
});
