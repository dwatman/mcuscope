// plots.js: the history seed is a third producer into addSample, and it must obey the same
// grammar the live decoders do.
//
// A chart keeps ONE x array shared by every channel, so ys(name).length must equal
// xsHost.length after every sample. parsePlotAdhoc and parsePlotDef each carry a name
// uniqueness gate for that reason; mergeSeedSeries had none, and /plot/series (long form)
// legitimately answers duplicate (line_id, name) rows for a capture written by a pre-0.2.1
// daemon (SPEC 9.2). Two entries under one name pushed two y values against one x, and the
// block trim splices both arrays equally, so the offset never healed.
//
// Same boundary, second half: a channel name from /plot/channels reached a DOM id without
// ever being tested against PLOT_NAME_RE, which the live path applies to every name.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
await import(webuiUrl("state.js"));
const { charts, plotSeed } = await import(webuiUrl("plots.js"));
const { digitalLanes } = await import(webuiUrl("digital.js"));

const analog = (name, sid) => ({ name, sid, kind: "analog", type: null, scale: null, unit: null });

test("a duplicate (line_id, name) keeps the y array aligned with the shared x array", () => {
  plotSeed([{
    channel: analog("ax", "0"),
    points: [
      { line_id: 10, ts: 1000.0, tick_ms: 10, value: 1 },
      { line_id: 11, ts: 1000.1, tick_ms: 11, value: 2 },
      { line_id: 11, ts: 1000.1, tick_ms: 11, value: 20 },   // duplicate (line, name)
      { line_id: 12, ts: 1000.2, tick_ms: 12, value: 3 },
    ],
  }]);
  const c = charts.get("s0");
  assert.ok(c, "the seed must build the stream's chart");
  assert.equal(c.xsHost.length, 3, "three line ids are three samples");
  assert.deepEqual([...c.ys.get("ax")], [1, 20, 3],
    "the duplicate must collapse, last row winning as server._csv_wide does");
  assert.equal(c.ys.get("ax").length, c.xsHost.length,
    "one duplicate row misaligns this channel against the chart's x array for good");
});

test("a duplicate does not shift a sibling channel on the same line", () => {
  plotSeed([
    { channel: analog("p", "1"),
      points: [{ line_id: 20, ts: 1, tick_ms: 1, value: 5 },
               { line_id: 20, ts: 1, tick_ms: 1, value: 6 },
               { line_id: 21, ts: 2, tick_ms: 2, value: 7 }] },
    { channel: analog("q", "1"),
      points: [{ line_id: 20, ts: 1, tick_ms: 1, value: 8 },
               { line_id: 21, ts: 2, tick_ms: 2, value: 9 }] },
  ]);
  const c = charts.get("s1");
  assert.deepEqual([...c.ys.get("p")], [6, 7]);
  assert.deepEqual([...c.ys.get("q")], [8, 9], "the clean sibling must be untouched");
  assert.equal(c.xsHost.length, 2);
});

test("a seed channel name that fails PLOT_NAME_RE is dropped, not charted", () => {
  plotSeed([{
    channel: analog("1bad name", "2"),
    points: [{ line_id: 30, ts: 1, tick_ms: 1, value: 1 }],
  }]);
  assert.equal(charts.get("s2"), undefined,
    "the live path rejects this name at parseChannelSpec; the seed path must agree");
});

test("a seed bit lane whose group name fails PLOT_NAME_RE is dropped", () => {
  plotSeed([{
    channel: { name: "led", sid: "3", kind: "bit", group: "gp io", type: "u1",
               scale: null, unit: null },
    points: [{ line_id: 40, ts: 1, tick_ms: 1, value: 1 }],
  }]);
  assert.equal(digitalLanes.get("led"), undefined,
    "the group reaches a DOM id (dgrp-<group>) and must pass the same grammar as a live one");
});

test("a well-formed bit lane still seeds", () => {
  plotSeed([{
    channel: { name: "run", sid: "4", kind: "bit", group: "gpio", type: "u1",
               scale: null, unit: null },
    points: [{ line_id: 50, ts: 1, tick_ms: 1, value: 1 }],
  }]);
  assert.ok(digitalLanes.get("run"), "the name gate must not reject a valid seed row");
});
