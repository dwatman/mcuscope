// plots.js addSample: an ad-hoc chart's channels printed on separate !p lines (the SPEC 2.5
// one-variable monitor_eventf idiom, used twice) share one x array, and each line carries one
// of them. With the absent channel pushed as null every point sat isolated between nulls, and a
// stepped path with spanGaps off drew nothing: live chips, empty chart. The absent channel now
// holds its previous value (SPEC 9.2 hold-last); a real break (a reset, a shed notice) stays a
// break until the channel's own next value. A typed stream keeps its nulls.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { charts, plotIngest, plotSeed, breakCharts, clearAllCharts } = await import(webuiUrl("plots.js"));

let id = 1;
const ingest = (raw) => plotIngest({ id: id++, ts: 1000 + id / 10, port: "p1", chan: "event", raw });

test("two ad-hoc channels on alternate lines each hold their level", () => {
  clearAllCharts();
  ingest("!p 1 temp=20");
  ingest("!p 2 volt=3");
  ingest("!p 3 temp=21");
  ingest("!p 4 volt=4");
  const c = charts.get("p1|adhoc");
  assert.deepEqual(c.ys.get("temp"), [20, 20, 21, 21]);
  assert.deepEqual(c.ys.get("volt"), [null, 3, 3, 4], "nothing is held before a channel's first value");
});

test("a break stays a break until the channel reports again", () => {
  clearAllCharts();
  ingest("!p 1 temp=20");
  ingest("!p 2 volt=3");
  breakCharts();
  ingest("!p 3 temp=21");
  ingest("!p 4 volt=4");
  const c = charts.get("p1|adhoc");
  assert.deepEqual(c.ys.get("temp"), [20, 20, null, 21, 21]);
  assert.deepEqual(c.ys.get("volt"), [null, 3, null, null, 4], "a held value bridged the break");
});

test("the history seed holds the same way", () => {
  clearAllCharts();
  plotSeed([
    { channel: { name: "temp", port: "p1", sid: null, kind: "analog" },
      points: [{ line_id: 1, ts: 10, tick_ms: 1, value: 20 }, { line_id: 3, ts: 12, tick_ms: 3, value: 21 }] },
    { channel: { name: "volt", port: "p1", sid: null, kind: "analog" },
      points: [{ line_id: 2, ts: 11, tick_ms: 2, value: 3 }] },
  ]);
  const c = charts.get("p1|adhoc");
  assert.deepEqual(c.ys.get("temp"), [20, 20, 21]);
  assert.deepEqual(c.ys.get("volt"), [null, 3, 3]);
});

test("a typed stream's missing channel is still a gap", () => {
  clearAllCharts();
  ingest("!pd 0 a:u1 b:u1");
  ingest("!ps 0 1 01,02");
  ingest("!pd 0 a:u1");      // redefined without b
  ingest("!ps 0 2 03");
  const c = charts.get("p1|s0");
  assert.deepEqual(c.ys.get("b"), [2, null]);
});

// A chart can exist with no sample: addSample refuses a non-finite x after ensureChart built it.
// Breaking it pushed a point at x = 1e-4 (lastHost null read as 0), a sample 50 years before the
// stream that then sat in the chart as its oldest.
test("a break on a chart with no sample yet adds nothing", () => {
  clearAllCharts();
  plotIngest({ id: id++, ts: NaN, port: "p1", chan: "event", raw: "!p 1 temp=20" });
  const c = charts.get("p1|adhoc");
  assert.ok(c, "setup: the chart was not built");
  assert.equal(c.xsHost.length, 0, "setup: the refused sample landed");
  breakCharts();
  ingest("!p 2 temp=21");
  assert.equal(c.xsHost.length, 1, `x values ${c.xsHost}`);
  assert.equal(c.xsTick.length, 1);
});
