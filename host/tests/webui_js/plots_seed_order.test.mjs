// plots.js plotSeed: a seeded stream's chips, lanes and colour slots follow its `!pd` field
// order.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { colorFor } = await import(webuiUrl("chrome.js"));

let nextId = 0;
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
  return nextId;
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
}

// ---- lead 2: seeded chip and lane order ---------------------------------------------------

const PALETTE = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b", "#ef7a5e", "#6fb2ff", "#d888c0", "#c7d05b"];
const seedPts = [{ line_id: 7001, ts: 700, tick_ms: 1, value: 1 }, { line_id: 7002, ts: 700.1, tick_ms: 2, value: 0 }];
const meta = (name, sid, extra = {}) => ({ name, port: "p1", sid, type: "u1", kind: "analog", ...extra });

test("a seeded stream's chips, lanes and colour slots follow its `!pd` order, unknown names last", () => {
  resetAll();
  row("!pd 4 zt:s2 zr:u2 zf:u2 zg:u1:/zl,zi", 1);
  // As /plot/channels lists them: by name.
  P.plotSeed([
    { channel: meta("za", "4"), points: seedPts },
    { channel: meta("zf", "4"), points: seedPts },
    { channel: meta("zi", "4", { kind: "bit", group: "zg" }), points: seedPts },
    { channel: meta("zl", "4", { kind: "bit", group: "zg" }), points: seedPts },
    { channel: meta("zr", "4"), points: seedPts },
    { channel: meta("zt", "4"), points: seedPts },
  ]);
  assert.deepEqual(P.charts.get("p1|s4").names, ["zt", "zr", "zf", "za"]);
  assert.deepEqual([...D.digitalLanes.values()].map((l) => l.name), ["zl", "zi"]);
  const k = PALETTE.indexOf(colorFor("zt"));
  assert.deepEqual(["zt", "zr", "zf", "za", "zl", "zi"].map(colorFor),
    [0, 1, 2, 3, 4, 5].map((j) => PALETTE[(k + j) % PALETTE.length]));
});

test("a seeded stream with no primed definition keeps the listed order", () => {
  resetAll();
  P.plotSeed([{ channel: meta("ya", "5"), points: seedPts }, { channel: meta("yb", "5"), points: seedPts }]);
  assert.deepEqual(P.charts.get("p1|s5").names, ["ya", "yb"]);
});
