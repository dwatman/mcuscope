// can.js: the wall-clock tick refreshes the age column in place; the table is rebuilt only
// when a frame landed (canDirty), and only when the row SET changed at that.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { canIngest, renderCan, clearAllCan, initCan, canRows, canAgeClass } = await import(webuiUrl("can.js"));

env.byId("sidebar").setAttribute("data-view", "both");   // the tick idles while CAN is hidden
const before = env.intervals.length;
initCan();
const timer = env.intervals.slice(before).find((t) => t.ms === 1000);

test("the CAN tick runs once a second", () => {
  assert.ok(timer, `no 1000 ms interval registered: ${env.intervals.slice(before).map((t) => t.ms)}`);
});

test("an idle tick updates the age cells without rebuilding the table", () => {
  clearAllCan();
  canIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!can 100 - 123 DEADBEEF" });
  renderCan();
  const wrap = env.byId("canWrap");
  const table = wrap.children[0];
  const ageCell = wrap.querySelectorAll("td").at(-1);
  // A count changed in the model behind the table's back (no frame, so no canDirty): only
  // a full render would write it, so its absence in the cells proves the tick aged only.
  const entry = canRows.values().next().value;
  entry.count = 777;
  const t0 = performance.now.bind(performance);
  performance.now = () => t0() + 5000;   // five seconds later, no frame in between
  try {
    timer.fn();
  } finally {
    performance.now = t0;
  }
  assert.equal(wrap.children[0], table, "the table was rebuilt on an idle tick");
  const cells = [...wrap.querySelectorAll("td")].map((c) => c.textContent);
  assert.ok(!cells.includes("777"), "an idle tick re-rendered every cell, not just the ages");
  assert.match(ageCell.textContent, /^5\.\ds$/, ageCell.textContent);
  assert.equal(ageCell.className, "age-stale");
});

test("a tick after a frame renders the frame", () => {
  const wrap = env.byId("canWrap");
  canIngest({ id: 2, ts: 1000.5, port: "p1", chan: "event", raw: "!can 100 - 123 CAFE" });
  timer.fn();
  const data = wrap.querySelectorAll("td").find((td) => td.className.includes("data"));
  assert.equal(data.textContent, "CA FE");
});

// ---- staleness is measured in the row's own periods ------------------------------------

test("a 1 kHz id goes stale after 1 s and red after 2 s, not after a fixed 3 s", () => {
  assert.equal(canAgeClass(0.999, 1), "age-fresh");
  assert.equal(canAgeClass(1.0, 1), "age-stale", "2000 dropped frames at 1 kHz still read fresh");
  assert.equal(canAgeClass(1.999, 1), "age-stale");
  assert.equal(canAgeClass(2.0, 1), "age-dead");
});

test("a once-a-minute id stays fresh between frames, stale past 5 periods, red past 10", () => {
  assert.equal(canAgeClass(59, 60000), "age-fresh");
  assert.equal(canAgeClass(299.9, 60000), "age-fresh", "a slow id cycled stale every 3 s between its frames");
  assert.equal(canAgeClass(300, 60000), "age-stale");
  assert.equal(canAgeClass(599.9, 60000), "age-stale");
  assert.equal(canAgeClass(600, 60000), "age-dead");
});

test("an id with no period yet is stale past 3 s and never red", () => {
  assert.equal(canAgeClass(2.99, null), "age-fresh");
  assert.equal(canAgeClass(3, null), "age-stale");
  assert.equal(canAgeClass(1e6, null), "age-stale", "one frame is no rate to have missed ten periods of");
});

test("the rendered age cell takes the period's class as the tick ages it", () => {
  clearAllCan();
  const wrap = env.byId("canWrap");
  for (let i = 0; i < 20; i++) {   // a 1 kHz id
    canIngest({ id: 10 + i, ts: 2000 + i / 1000, port: "p1", chan: "event", raw: "!can 1 - 7FF 00" });
  }
  canIngest({ id: 40, ts: 2000.019, port: "p1", chan: "event", raw: "!can 1 - 700 00" });   // one frame, no period
  renderCan();
  const ageClasses = () => wrap.querySelectorAll("tr").slice(1).map((tr) => [tr.children[0].textContent, tr.children.at(-1).className]);
  assert.deepEqual(ageClasses(), [["700", "age-fresh"], ["7FF", "age-fresh"]]);
  const t0 = performance.now.bind(performance);
  performance.now = () => t0() + 2500;
  try {
    timer.fn();
  } finally {
    performance.now = t0;
  }
  assert.deepEqual(ageClasses(), [["700", "age-fresh"], ["7FF", "age-dead"]],
    "2.5 s is ten periods and more for 7FF, and under the no-period 3 s for 700");
});
