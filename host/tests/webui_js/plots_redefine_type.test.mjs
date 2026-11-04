// plots.js addSample: a stream redefined under the same field name takes the new definition's
// type for its readouts, as it already took the new unit (SPEC 2.5: the newest definition is in
// force for render metadata). `isInt` was set once, when the channel was added, so a field
// redefined from u2 to f4 kept reading as an integer: 3.7 showed as 4.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { state } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));

let nextId = 0;
function row(raw, ts) {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw });
}

test("a field redefined from an integer type to f4 reads as a float, and back", () => {
  P.clearAllCharts();
  row("!pd 0 v:u2", 1);
  row("!ps 0 00000001 0001", 1.1);
  const chart = P.charts.get("p1|s0");
  assert.equal(chart.isInt.get("v"), true, "the u2 field starts integer");
  row("!pd 0 v:f4", 2);
  row("!ps 0 00000002 40400000", 2.1);
  assert.equal(P.charts.get("p1|s0"), chart, "the redefinition keeps the chart");
  assert.equal(chart.isInt.get("v"), false, "an f4 field kept the integer readout");
  row("!pd 0 v:u2", 3);
  row("!ps 0 00000003 0002", 3.1);
  assert.equal(chart.isInt.get("v"), true, "and an integer redefinition reads integer again");
});
