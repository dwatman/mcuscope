// state.js lineTick and plots.js decodePlotSample must accept the same !ps lines.
//
// Registry class 19: two engines validating one thing. lineTick took parts[2] as a hex tick
// with no arity check and no check that the sid was ever declared, where plots.js requires
// exactly 4 tokens and a matching definition (and the daemon stores such a line as a generic
// event). lineTick sets the sticky global state.anchorTick, so a line every other decoder
// rejected shifted every terminal timestamp and the tick x axis for the whole session.
// Driven through both real modules rather than a stub, because agreement is the invariant.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makeRow } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state, lineTick, pushBuffer } = await import(webuiUrl("state.js"));
const { charts, plotIngest, clearAllCharts } = await import(webuiUrl("plots.js"));

let nextId = 1;
const evt = (raw, port = "p1") => makeRow(nextId++, { chan: "event", port, raw });

// What plots.js makes of a line: a chart appeared (or grew) means it decoded.
function plotAccepts(row) {
  const before = charts.size;
  const chart = charts.get("s" + row.raw.split(/\s+/)[1]);
  const len = chart ? chart.xsHost.length : 0;
  plotIngest(row);
  const after = charts.get("s" + row.raw.split(/\s+/)[1]);
  return charts.size > before || Boolean(after && after.xsHost.length > len);
}

test("the two decoders agree on which !ps lines carry a tick", () => {
  clearAllCharts();
  state.anchorTick = null;
  plotIngest(evt("!pd 0 a:u2 b:u2"));   // declare stream 0 on p1

  const cases = [
    ["!ps 0 3E8 0064,0064", true, "a declared stream, four tokens"],
    ["!ps 0 ABCD", false, "three tokens"],
    // The clause the hand-written mirror dropped the second time: four tokens and a
    // declared sid, but the values do not match the definition, so the daemon and plots.js
    // both keep the line as a plain event while lineTick still took its tick.
    ["!ps 0 3E9 0064", false, "one value against a two-field definition"],
    ["!ps 0 3EA 0064,0064,0064", false, "three values against a two-field definition"],
    ["!ps 0 3EB zz,zz", false, "values that are not hex"],
    ["!ps 0 3EC ,", false, "empty values"],
    ["!ps 0 3E8 0064,0064 extra", false, "five tokens"],
    ["!ps 9 3E8 0064,0064", false, "the sid was never declared"],
    ["!ps 0 zz 0064,0064", false, "the tick is not hex"],
    ["!ps 0 100000000 0064,0064", false, "the tick is past the SPEC 2.5 range"],
  ];
  for (const [raw, want, why] of cases) {
    const row = evt(raw);
    assert.equal(plotAccepts(row), want, `plots.js disagrees about ${raw} (${why})`);
    assert.equal(lineTick(row) !== null, want, `lineTick disagrees about ${raw} (${why})`);
  }
});

test("a !ps line the charts reject cannot anchor the session's tick", () => {
  clearAllCharts();
  state.anchorTick = null;

  const bad = evt("!ps 0 ABCD");        // 0xABCD = 43981
  plotIngest(bad);
  pushBuffer(bad);
  assert.equal(charts.size, 0, "plots.js kept it as a plain event");
  assert.equal(state.anchorTick, null,
    "an undecodable line set the anchor, shifting every timestamp until 'clear all'");

  const real = evt("!can 100 - 100 -");
  pushBuffer(real);
  assert.equal(lineTick(real) - state.anchorTick, 0,
    "the first real tick anchors at zero, not at a negative offset from a phantom one");
});

test("a stream declared on another port does not vouch for this one", () => {
  clearAllCharts();
  plotIngest(evt("!pd 3 x:u2", "p1"));
  const other = evt("!ps 3 3E8 0064", "p2");
  assert.equal(plotAccepts(other), false, "plotDefs is keyed by port|sid");
  assert.equal(lineTick(other), null);
});

// Class 6, the x side. The y values pass three Number.isFinite gates; the x arrays had
// none, and the monotonic bump does not catch a bad one either (`undefined <= n` is false,
// so it passes through and becomes lastHost). A single non-finite x blanks the series.
test("a malformed row timestamp cannot put a non-finite value in the x arrays", () => {
  clearAllCharts();
  plotIngest(evt("!pd 0 a:u2 b:u2"));
  plotIngest(evt("!ps 0 3E8 0064,0064"));            // a good sample first
  const chart = charts.get("s0");
  const goodLen = chart.xsHost.length;

  for (const bad of [undefined, null, "abc", NaN, Infinity]) {
    const row = makeRow(nextId++, { chan: "event", port: "p1", raw: "!ps 0 3E9 0065,0065" });
    row.ts = bad;
    plotIngest(row);
  }
  assert.ok(chart.xsHost.every(Number.isFinite), `xsHost holds a non-finite: ${chart.xsHost}`);
  assert.ok(chart.xsTick.every(Number.isFinite), `xsTick holds a non-finite: ${chart.xsTick}`);
  assert.equal(chart.xsHost.length, goodLen, "a malformed row was charted anyway");
});
