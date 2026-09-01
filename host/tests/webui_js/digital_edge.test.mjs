// digital.js: the live right edge is the newest sample, not the newest transition.
//
// Lanes are transition-reduced (a held level stores no vertex), and digitalRightEdge() used to
// take the edge from each lane's last vertex. A stream whose digital fields never changed (a
// charger sitting in IDLE emitting !ps at 10 Hz) therefore drew one sample and never scrolled:
// every redraw covered the same window, ending at the first sample's time. The freeze anchor
// took the same max and pinned a pause at that stale time.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const { state } = await import(webuiUrl("state.js"));
const { fmtTime } = await import(webuiUrl("timewindow.js"));
const dg = await import(webuiUrl("digital.js"));

const ch = { kind: "enum", name: "npb", labels: [[0, "IDLE"], [4, "CHARGING"]] };

test("a constant signal still advances the right edge with every sample", () => {
  dg.clearAllDigital();
  for (let i = 0; i < 5; i++) dg.digitalIngest("0", [["npb", 0, ch]], { host: 1000 + i, tick: 10 + i });
  const lane = dg.digitalLanes.get("npb");
  assert.equal(lane.vs.length, 1, "transition reduction: one vertex for a held level");
  assert.equal(dg.digitalRightEdge(), 1004, "the window must end at the newest sample, not the vertex");
  state.timeMode = "tick";
  assert.equal(dg.digitalRightEdge(), 14);
  state.timeMode = "host";
  assert.equal(lane.dirty, true, "and the lane repaints, so the window scrolls");
});

test("the seed and the live stream interleaving out of order cannot move the edge backwards", () => {
  dg.digitalIngest("0", [["npb", 0, ch]], { host: 900, tick: 5 });
  assert.equal(dg.digitalRightEdge(), 1004);
});

test("pause pins the edge at the newest sample; clear-all forgets it", () => {
  dg.setDigitalPaused(true);
  dg.digitalIngest("0", [["npb", 0, ch]], { host: 2000, tick: 20 });
  assert.equal(dg.digitalRightEdge(), 1004, "paused: the edge does not follow new samples");
  dg.setDigitalPaused(false);
  assert.equal(dg.digitalRightEdge(), 2000, "resume: catches up to the newest sample");
  dg.clearAllDigital();
  assert.equal(dg.digitalRightEdge(), null);
});

test("the cursor carries the time under it, formatted as the analog legend does", () => {
  dg.clearAllDigital();
  dg.digitalIngest("0", [["npb", 0, ch]], { host: 1000, tick: 10 });
  dg.digitalIngest("0", [["npb", 4, ch]], { host: 1020, tick: 30 });
  const lane = dg.digitalLanes.get("npb");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 430;   // 130 px gutter
  state.anchorTs = 1000;
  state.timeMode = "rel";
  const cur = env.byId("dCursor");
  dg.setDigitalCursorAt(1020);                 // snaps to the transition at 1020
  assert.equal(cur.hidden, false);
  assert.equal(cur.dataset.t, "20.000 s");
  assert.equal(cur.dataset.t, fmtTime(state, 1020), "one formatter for both cursors");
  assert.equal(cur.classList.contains("flip"), true, "right half: the tag sits left of the line");
  dg.setDigitalCursorAt(1000);
  assert.equal(cur.dataset.t, "0.000 s");
  assert.equal(cur.classList.contains("flip"), false);
  state.timeMode = "host";
  state.anchorTs = null;
});
