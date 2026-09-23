// plots.js: each !ps line is decoded once. pushBuffer asks for the line's tick (state.js lineTick,
// through hooks.plotSampleTick) and plotIngest then decodes the same row: two full decodes per
// sample, about 30% of the per-row ingest cost on plot-heavy traffic. The last decode is kept,
// keyed by the raw text and the definition object, so a redefinition still decodes afresh.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { pushBuffer, lineTick } = await import(webuiUrl("state.js"));
const { charts, plotIngest, clearAllCharts } = await import(webuiUrl("plots.js"));

// Every f4 field decode reads one float: count them.
let decodes = 0;
const real = DataView.prototype.getFloat32;
DataView.prototype.getFloat32 = function (...a) { decodes += 1; return real.apply(this, a); };

let nextId = 1;
const row = (raw) => ({ id: nextId++, ts: 1000 + nextId / 100, port: "p1", chan: "event", raw });
// What api.js routeLiveRow does with a live row.
function arrive(r) { pushBuffer(r); plotIngest(r); }

test("a live !ps row is decoded once for its tick and its points together", () => {
  clearAllCharts();
  arrive(row("!pd 0 v:f4"));
  decodes = 0;
  const r = row("!ps 0 3E8 3F800000");
  arrive(r);
  assert.equal(lineTick(r), 1000, "setup: the tick was not read");
  assert.deepEqual(charts.get("p1|s0").ys.get("v"), [1], "setup: the sample did not land");
  assert.equal(decodes, 1, `decoded ${decodes} times`);
});

test("a redefinition between two identical lines decodes against the new definition", () => {
  clearAllCharts();
  arrive(row("!pd 0 v:f4"));
  arrive(row("!ps 0 3E8 3F800000"));
  arrive(row("!pd 0 v:f4*2"));
  arrive(row("!ps 0 3E8 3F800000"));
  assert.deepEqual(charts.get("p1|s0").ys.get("v"), [1, 2], "a stale decode was reused across the !pd");
});
