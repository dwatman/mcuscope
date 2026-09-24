// plots.js plotSeed: a seed takes field order from the newest `!pd` among the backfill's own
// rows (M1).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
}

// ---- M1: seed order from the backfill's own `!pd` ---------------------------------------------

const seedPts = [{ line_id: 7001, ts: 700, tick_ms: 1, value: 1 }];
const meta = (name, sid) => ({ name, port: "p1", sid, type: "u2", kind: "analog" });
const pd = (id, raw, port = "p1", chan = "event") => ({ id, ts: 1, port, chan, raw });

test("the seed takes field order from the newest `!pd` among the backfill rows", () => {
  resetAll();
  P.plotIngest(pd(1, "!pd 6 wa:u2 wt:u2"));   // primed, and older than the backfill's
  P.plotSeed([{ channel: meta("wa", "6"), points: seedPts }, { channel: meta("wt", "6"), points: seedPts }],
    [pd(2, "!pd 6 wa:u2 wt:u2"), pd(3, "!pd 6 wt:u2 wa:u2"), pd(4, "!pd 6 bad"), pd(5, "!pd 6 wa:u2 wt:u2", "p2"),
     pd(6, "!pd 6 wa:u2 wt:u2", "p1", "cmd")]);
  assert.deepEqual(P.charts.get("p1|s6").names, ["wt", "wa"]);
});
