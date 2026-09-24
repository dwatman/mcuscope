// plots.js xForRow (registry class 77): the terminal-line hover under the tick base.
//
// A line with no tick of its own is placed at its anchor's tick plus the host gap. The reset
// offset was then taken at the LINE's host time rather than the anchor's:
// - a boot line read in the same chunk as the first sample after a reset shares that sample's
//   host time, so it took the new offset on top of an estimate that already continues from the
//   old tick, and landed a whole uptime to the right;
// - an estimate crossing the 2^32 wrap was wrapped back to a small tick, left of everything.
// Both read on a lane whose values sit far apart, so the cursor's snap cannot hide a wrong x.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

const { state, tickAnchors } = await import(webuiUrl("state.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));

let nextId = 0;
const hex = (n) => n.toString(16).padStart(8, "0");
// A plot row, noted as a tick anchor the way the app's buffer path notes it.
function ps(tick, value, ts) {
  const r = { id: ++nextId, ts, port: "p1", chan: "event", raw: `!ps 0 ${hex(tick)} ${value}` };
  state.maxId = r.id;
  P.plotIngest(r);
  TW.noteTickAnchor(tickAnchors, "p1", r.id, ts, tick);
  return r;
}
const debugLine = (ts) => ({ id: ++nextId, ts, port: "p1", chan: "debug", raw: "boot ok" });

function hoverReadout(r) {
  const lane = D.digitalLanes.get("p1|m");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  env.document.elementFromPoint = () => ({ closest: () => ({ __row: r }) });
  P.paneMouseMove({ clientX: Math.random(), clientY: 5 });
  env.frames.splice(0).forEach((f) => f());
  return lane.valEl.textContent;
}

function fresh() {
  P.paneMouseLeave();
  P.clearAllCharts();
  D.clearAllDigital();
  tickAnchors.clear();
  state.timeMode = "tick";
  const r = { id: ++nextId, ts: 1, port: "p1", chan: "event", raw: "!pd 0 m:u1:=0=IDLE,1=RUN,2=ERR" };
  P.plotIngest(r);
}

test("a line read with the first sample after a reset takes the offset once", () => {
  fresh();
  ps(1000000, "01", 100);         // RUN, then the board resets
  const boot = debugLine(120);    // same read as the first sample after the reset
  ps(0, "02", 120);               // ERR at x 1020000
  ps(60000, "00", 180);           // IDLE at x 1080000
  try {
    assert.equal(hoverReadout(boot), "ERR", "the boot line took the reset offset twice");
  } finally { P.paneMouseLeave(); state.timeMode = "host"; }
});

test("a line estimated across the 2^32 wrap stays on the continued axis", () => {
  fresh();
  ps(0xFFFFFF00, "01", 100);      // RUN at x 4294967040
  const line = debugLine(101);    // no tick; its estimate crosses the wrap
  ps(944, "02", 101.2);           // ERR at x 4294968240
  ps(60944, "00", 161.2);         // IDLE
  try {
    assert.equal(hoverReadout(line), "ERR", "the estimate was wrapped to a small tick");
  } finally { P.paneMouseLeave(); state.timeMode = "host"; }
});
