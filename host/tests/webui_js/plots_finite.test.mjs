// plots.js: nothing non-finite may reach a chart's data arrays (REVIEW registry class 6).
//
// uPlot.rangeNum() returns [NaN, NaN] the moment a single Infinity or NaN is inside the
// visible window, and every series on that chart then disappears with no error anywhere.
// Two instances have shipped and been fixed:
//   1. an f4 sample of 7F800000 (an ordinary firmware divide-by-zero) reaching the array;
//   2. a finite sample carried to Infinity by a large *scale factor, after the decode-time
//      check had already passed.
// Both are covered below, plus the grammar-level rejections that keep a non-finite literal
// out of a definition in the first place.
//
// Driven through the exported plotIngest and the exported `charts` model, so the private
// decode chain (parsePlotDef -> decodePlotSample -> decodePlotField -> the scale multiply)
// is exercised exactly as the live stream exercises it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { charts, plotIngest, clearAllCharts } = await import(webuiUrl("plots.js"));
const { digitalLanes, clearAllDigital } = await import(webuiUrl("digital.js"));

let nextId = 1;
let nextTs = 1000;
function evt(raw) {
  return { id: nextId++, ts: (nextTs += 0.01), port: "p1", chan: "event", raw };
}
const ingest = (raw) => plotIngest(evt(raw));

// Every number a chart holds, so a single assertion can sweep the whole model.
function allNumbers() {
  const out = [];
  for (const c of charts.values()) {
    out.push(...c.xsHost, ...c.xsTick);
    for (const arr of c.ys.values()) out.push(...arr);
  }
  return out;
}
// The second class-6 producer. digitalIngest writes into its own arrays from the same `x`
// object addSample gets, so the sweep has to cover both or it only ever proves one of them.
function allLaneNumbers() {
  const out = [];
  for (const l of digitalLanes.values()) out.push(...l.xsHost, ...l.xsTick, ...l.vs);
  return out;
}
function assertAllFinite(where) {
  for (const v of [...allNumbers(), ...allLaneNumbers()]) {
    assert.ok(v === null || Number.isFinite(v),
      `${where}: ${v} reached a chart data array; uPlot.rangeNum() blanks the whole chart`);
  }
}

function series(key, name) {
  const c = charts.get(key);
  return c ? c.ys.get(name) : undefined;
}
function pointCount(key) {
  const c = charts.get(key);
  return c ? c.xsHost.length : 0;
}

test("a well-formed stream decodes and scales as declared", () => {
  ingest("!pd 0 a:f4 b:s2*0.5");
  ingest("!ps 0 3E8 3F800000,0064");     // a = 1.0f, b = 100 * 0.5
  assert.deepEqual(series("s0", "a"), [1]);
  assert.deepEqual(series("s0", "b"), [50]);
  assert.deepEqual(charts.get("s0").xsTick, [1000]);
  assertAllFinite("baseline");
});

test("a non-finite f4 sample is dropped, and takes no partial row with it", () => {
  const before = pointCount("s0");
  ingest("!ps 0 3E9 7F800000,0064");     // +Infinity
  ingest("!ps 0 3EA FF800000,0064");     // -Infinity
  ingest("!ps 0 3EB 7FC00000,0064");     // NaN
  ingest("!ps 0 3EC 7F800001,0064");     // signalling NaN
  assert.equal(pointCount("s0"), before,
    "a non-finite field must drop the whole sample, not append it");
  assert.deepEqual(series("s0", "b"), [50], "the sibling channel must not gain a phantom point");
  assertAllFinite("non-finite f4");
});

test("a finite sample that a large scale factor overflows is dropped after scaling", () => {
  // 1e308 is a legal scale (parsePlotValue accepts it: it is finite), and s4 tops out at
  // 2147483647, so the product overflows to Infinity only after the decode-time check.
  ingest("!pd 1 big:s4*1e308");
  ingest("!ps 1 3E8 7FFFFFFF");
  assert.equal(charts.has("s1"), false,
    "an overflowing product must not create a chart, let alone plot Infinity");

  // The same stream at a magnitude that stays finite does land.
  ingest("!pd 2 ok:s4*1e10");
  ingest("!ps 2 3E8 0000000A");
  assert.deepEqual(series("s2", "ok"), [1e11]);
  assertAllFinite("scale overflow");
});

test("a mid-stream overflow leaves the earlier points intact", () => {
  ingest("!ps 2 3E9 7FFFFFFF");          // 2147483647 * 1e10 = 2.1e19, still finite
  ingest("!pd 3 v:s4*1e300");
  ingest("!ps 3 3E8 00000001");          // 1 * 1e300, finite
  ingest("!ps 3 3E9 7FFFFFFF");          // overflows
  assert.deepEqual(series("s3", "v"), [1e300]);
  assert.equal(pointCount("s3"), 1);
  assertAllFinite("mid-stream overflow");
});

test("a non-finite literal cannot enter a definition or an ad-hoc point", () => {
  ingest("!pd 4 x:s2*1e999");            // scale overflows the literal grammar
  ingest("!ps 4 3E8 0064");
  assert.equal(charts.has("s4"), false, "a definition with an infinite scale must be rejected");

  ingest("!p 1000 v=1e999");
  ingest("!p 1000 v=-1e999");
  assert.equal(charts.has("adhoc"), false, "an infinite ad-hoc value must be rejected");

  ingest("!p 1000 v=1.5 w=-2e3");
  assert.deepEqual(series("adhoc", "v"), [1.5]);
  assert.deepEqual(series("adhoc", "w"), [-2000]);
  assertAllFinite("literals");
});

test("the plot value grammar accepts and rejects the same shapes the daemon does", () => {
  const start = pointCount("adhoc");
  const good = ["0", "-0", "12", "-12", "1.25", "-1.25", "1e3", "1E3", "1e+3", "1e-3", "1.5e-3"];
  for (const s of good) ingest(`!p 2000 g=${s}`);
  assert.equal(pointCount("adhoc"), start + good.length, "a legal literal was rejected");

  const mid = pointCount("adhoc");
  const bad = ["", "+1", ".5", "1.", "1e", "e5", "0x10", "1,5", "nan", "NaN", "inf", "Infinity",
               "-Infinity", "1e999", "--1", "1 2", "1.2.3", "1d5"];
  for (const s of bad) ingest(`!p 2000 g=${s}`);
  assert.equal(pointCount("adhoc"), mid, "an illegal literal was accepted");
  assertAllFinite("grammar sweep");
});

test("an out-of-range tick never reaches the x array", () => {
  const before = pointCount("s0");
  ingest("!ps 0 100000000 3F800000,0064");   // 2^32, one past the SPEC 2.5 range
  ingest("!ps 0 FFFFFFFFFF 3F800000,0064");
  ingest("!ps 0 -1 3F800000,0064");
  assert.equal(pointCount("s0"), before, "an out-of-range tick must not yank the shared window");
  ingest("!ps 0 FFFFFFFF 3F800000,0064");    // the largest legal tick
  assert.equal(pointCount("s0"), before + 1);
  assertAllFinite("tick range");
});

test("a malformed field or arity is rejected outright", () => {
  const before = pointCount("s0");
  ingest("!ps 0 3F0 3F80000,0064");      // f4 field one nibble short
  ingest("!ps 0 3F1 3F80000G,0064");     // not hex
  ingest("!ps 0 3F2 3F800000");          // too few values
  ingest("!ps 0 3F3 3F800000,0064,0064");// too many
  ingest("!ps 0 3F4 3F800000 0064");     // space-separated, not comma
  ingest("!ps 9 3F5 3F800000,0064");     // unknown stream
  assert.equal(pointCount("s0"), before);
  assertAllFinite("malformed fields");
});

test("integer fields decode with the declared width and sign", () => {
  ingest("!pd 5 u:u1 s:s1 w:u2 v:s2 q:u4 r:s4");
  ingest("!ps 5 3E8 FF,FF,FFFF,FFFF,FFFFFFFF,FFFFFFFF");
  assert.deepEqual(series("s5", "u"), [255]);
  assert.deepEqual(series("s5", "s"), [-1]);
  assert.deepEqual(series("s5", "w"), [65535]);
  assert.deepEqual(series("s5", "v"), [-1]);
  assert.deepEqual(series("s5", "q"), [4294967295]);
  assert.deepEqual(series("s5", "r"), [-1]);
  ingest("!ps 5 3E9 00,80,0100,8000,00000001,80000000");
  assert.deepEqual(series("s5", "u"), [255, 0]);
  assert.deepEqual(series("s5", "s"), [-1, -128]);
  assert.deepEqual(series("s5", "w"), [65535, 256]);
  assert.deepEqual(series("s5", "v"), [-1, -32768]);
  assert.deepEqual(series("s5", "q"), [4294967295, 1]);
  assert.deepEqual(series("s5", "r"), [-1, -2147483648]);
  assertAllFinite("integer widths");
});

test("a scale on an enum or bits channel invalidates the definition", () => {
  const lanesBefore = digitalLanes.size;
  ingest("!pd 6 f:u1*2/a,b");            // *scale plus a bit-lane sigil: illegal (SPEC 2.5)
  ingest("!ps 6 3E8 03");
  ingest("!pd 7 e:u1*2=0=off,1=on");
  ingest("!ps 7 3E8 01");
  assert.equal(digitalLanes.size, lanesBefore,
    "the daemon stores nothing for this stream, so the panel must not invent lanes");
  assert.equal(charts.has("s6"), false);
  assert.equal(charts.has("s7"), false);
});

test("a channel type that reaches Object.prototype is rejected", () => {
  // PLOT_TYPES is null-prototype for this: as a plain object `"toString" in PLOT_TYPES` is
  // true, so the spec validated and then threw a TypeError deep inside the decode, which
  // discarded every remaining row in that WebSocket frame.
  for (const t of ["toString", "constructor", "hasOwnProperty", "__proto__", "valueOf"]) {
    ingest(`!pd 8 a:${t}`);
    ingest("!ps 8 3E8 01");
  }
  assert.equal(charts.has("s8"), false,
    "device output must not be able to resolve a type through Object.prototype");
  assertAllFinite("prototype pollution");
});

test("clearAllCharts empties the model", () => {
  clearAllCharts();
  assert.equal(charts.size, 0);
  assert.deepEqual(allNumbers(), []);
});

test("an enum value the daemon rejects does not build a definition here either", () => {
  // Registry class 19: parseEnumLabels mirrors protocol._parse_enum_labels, whose digit cap
  // exists because CPython's int() raises past it - so the daemon drops the whole !pd and
  // stores it as a generic event. Without the same cap the browser decoded and charted a
  // typed stream the daemon never had, and the UI disagreed with `mcu plot` on the same line.
  //
  // Asserted on digitalLanes, not on `charts`: an enum channel renders as a digital lane, so
  // `charts.has("s7") === false` holds whether or not the definition was accepted - the first
  // version of this test proved nothing for exactly that reason.
  clearAllCharts();
  clearAllDigital();
  const huge = "9".repeat(400);
  ingest(`!pd 7 e:s1:=${huge}=on,2=off`);
  ingest("!ps 7 3E8 01");
  assert.equal(digitalLanes.size, 0, "a rejected definition must decode nothing");
  // The same definition inside the cap still works, so the check discriminates.
  ingest("!pd 7 e:s1:=1=on,2=off");
  ingest("!ps 7 3E8 01");
  assert.equal(digitalLanes.size, 1, "a legal enum definition must decode");

  // At the bound, not 400 digits past it: a wildly-over value passes any cap between 1 and
  // 399, so it cannot tell a correct 20 from an off-by-one.
  clearAllDigital();
  ingest(`!pd 6 e:s1:=${"9".repeat(21)}=on,2=off`);
  ingest("!ps 6 3E8 01");
  assert.equal(digitalLanes.size, 0, "21 digits is past the daemon's cap");
  ingest(`!pd 6 e:s1:=${"9".repeat(20)}=on,2=off`);
  ingest("!ps 6 3E8 01");
  assert.equal(digitalLanes.size, 1, "20 digits is inside it");
});

test("an ad-hoc tick past the daemon's decimal digit cap is rejected", () => {
  // protocol.parse_plot_adhoc gates the tick with is_decimal_token, and its 20-digit cap is
  // the only clause that can reject a zero-padded token: the token is numerically 0, so the
  // 2^32 range check next to it accepts it. Same shape as the two misses already fixed in
  // the !can mirror.
  clearAllCharts();
  ingest(`!p ${"0".repeat(21)} a=1`);
  assert.equal(charts.has("adhoc"), false, "21 digits is past the daemon's cap");
  ingest(`!p ${"0".repeat(19)}7 a=1`);
  assert.equal(pointCount("adhoc"), 1, "20 digits is inside it");
  assert.deepEqual(charts.get("adhoc").xsTick, [7]);
  assertAllFinite("ad-hoc tick cap");
});

test("a non-finite row timestamp never reaches the digital lanes either", () => {
  // Class 6's other producer. A bits-only stream keeps the analog side out of it, so
  // digitalIngest's own gate is the only thing that can stop a NaN row.ts - and a NaN there
  // is permanent: the monotonic bump is `hx <= xsHost[n-1]` and `hx <= NaN` is false, so no
  // later sample is ever bumped again.
  clearAllCharts();
  clearAllDigital();
  ingest("!pd 0 f:u1:/a,b");
  ingest("!ps 0 3E8 01");                                                   // a=1 b=0
  plotIngest({ id: nextId++, ts: NaN, port: "p1", chan: "event", raw: "!ps 0 3E9 02" });
  plotIngest({ id: nextId++, ts: Infinity, port: "p1", chan: "event", raw: "!ps 0 3EA 03" });
  ingest("!ps 0 3EB 02");                                                   // a=0 b=1
  assert.equal(charts.size, 0, "a bits-only stream must not create an analog chart");
  assertAllFinite("non-finite row timestamp");
  // The good samples on either side of the dropped ones still land, in order.
  assert.deepEqual(digitalLanes.get("a").vs, [1, 0]);
  assert.deepEqual(digitalLanes.get("b").vs, [0, 1]);
  const xs = digitalLanes.get("a").xsHost;
  assert.ok(xs[1] > xs[0], "the x array must stay strictly increasing");
});
