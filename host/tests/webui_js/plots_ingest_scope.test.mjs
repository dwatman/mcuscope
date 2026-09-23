// plots.js plotIngest: what it charts.
//  - Only an event row is a sample: a `!p 9 a=9` sent raw from the command box is a cmd row,
//    stored and shown as such, and never charted (can.js has the same gate for !can).
//  - After a clear-all (a capture reset is one) the history seed's id floor goes with the
//    charts: the new capture's ids restart low, and a floor left in place silently dropped
//    every new sample below the old seed's newest id.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { charts, plotIngest, plotSeed, clearAllCharts } = await import(webuiUrl("plots.js"));

const points = (key) => (charts.get(key) ? charts.get(key).xsHost.length : 0);

test("a raw-sent !p line (a cmd row) is not charted; the same text as an event is", () => {
  clearAllCharts();
  plotIngest({ id: 1, ts: 10, port: "p", chan: "cmd", raw: "!p 9 a=9" });
  assert.equal(points("p|adhoc"), 0, "a command the user typed was charted as a sample");
  plotIngest({ id: 2, ts: 11, port: "p", chan: "event", raw: "!p 9 a=9" });
  assert.equal(points("p|adhoc"), 1);
});

test("after a clear-all, samples below the old seed's ids are charted", () => {
  clearAllCharts();
  plotSeed([{ channel: { name: "a", port: "p", sid: null, kind: "analog" },
              points: [{ line_id: 499, ts: 10, tick_ms: 1, value: 1 }, { line_id: 500, ts: 11, tick_ms: 2, value: 2 }] }]);
  assert.equal(points("p|adhoc"), 2, "setup: the seed did not land");
  plotIngest({ id: 400, ts: 12, port: "p", chan: "event", raw: "!p 3 a=3" });
  assert.equal(points("p|adhoc"), 2, "a row the seed already holds must not be charted twice (positive control)");
  clearAllCharts();
  for (let id = 1; id <= 3; id++) plotIngest({ id, ts: 20 + id, port: "p", chan: "event", raw: `!p ${id} a=${id}` });
  assert.equal(points("p|adhoc"), 3, "the new capture's low ids were taken for rows the old seed held");
});
