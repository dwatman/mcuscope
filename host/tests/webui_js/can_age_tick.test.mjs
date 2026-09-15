// can.js: the wall-clock tick refreshes the age column in place; the table is rebuilt only
// when a frame landed (canDirty), and only when the row SET changed at that.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { canIngest, renderCan, clearAllCan, initCan, canRows, canAgeClass, canPeriodic } = await import(webuiUrl("can.js"));

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
  // The count is drawn only in the row's hover (can.js), never in a cell's text.
  const row = wrap.querySelectorAll("tr").at(-1);
  assert.ok(!String(row.title).includes("777"), "an idle tick re-rendered the rows, not just the ages");
  assert.match(ageCell.textContent, /^5\.\ds$/, ageCell.textContent);
  assert.equal(ageCell.className, "age-fresh", "one frame is no period to have missed");
  // The positive control: a render does write 777 where the assertion above looks.
  renderCan();
  assert.match(String(wrap.querySelectorAll("tr").at(-1).title), /^777 frames/);
});

test("a tick after a frame renders the frame", () => {
  const wrap = env.byId("canWrap");
  canIngest({ id: 2, ts: 1000.5, port: "p1", chan: "event", raw: "!can 100 - 123 CAFE" });
  timer.fn();
  const data = wrap.querySelectorAll("td").find((td) => td.className.includes("data"));
  assert.equal(data.textContent, "CA FE");
});

// ---- staleness is measured in the row's own periods ------------------------------------

test("a 10 Hz id goes stale after 5 missed periods and red after 10", () => {
  assert.equal(canAgeClass(0.499, 100), "age-fresh");
  assert.equal(canAgeClass(0.5, 100), "age-stale");
  assert.equal(canAgeClass(0.999, 100), "age-stale");
  assert.equal(canAgeClass(1.0, 100), "age-dead");
});

test("a 1 kHz id is held to the 250 ms and 500 ms delivery-jitter floor, not a 1 s one", () => {
  assert.equal(canAgeClass(0.249, 1), "age-fresh");
  assert.equal(canAgeClass(0.25, 1), "age-stale");
  assert.equal(canAgeClass(0.499, 1), "age-stale");
  assert.equal(canAgeClass(0.5, 1), "age-dead");
});

test("a once-a-minute id stays fresh between frames, stale past 5 periods, red past 10", () => {
  assert.equal(canAgeClass(59, 60000), "age-fresh");
  assert.equal(canAgeClass(299.9, 60000), "age-fresh", "a slow id cycled stale every 3 s between its frames");
  assert.equal(canAgeClass(300, 60000), "age-stale");
  assert.equal(canAgeClass(599.9, 60000), "age-stale");
  assert.equal(canAgeClass(600, 60000), "age-dead");
});

test("an id with no period, or an irregular one, is never coloured", () => {
  assert.equal(canAgeClass(1e6, null), "age-fresh");
  assert.equal(canAgeClass(1e6, 100, false), "age-fresh");
});

// Feed one id at the given gaps (ms) and report whether it counts as periodic.
function periodicAfter(gapsMs) {
  clearAllCan();
  let ts = 5000;
  canIngest({ id: 1, ts, port: "p1", chan: "event", raw: "!can 1 - 321 00" });
  gapsMs.forEach((g, i) => {
    ts += g / 1000;
    canIngest({ id: 2 + i, ts, port: "p1", chan: "event", raw: "!can 1 - 321 00" });
  });
  return canPeriodic(canRows.values().next().value);
}

test("periodic needs three steady gaps; irregular gaps never qualify", () => {
  assert.equal(periodicAfter([]), false, "one frame");
  assert.equal(periodicAfter([100, 100]), false, "two gaps are too few to call it periodic");
  assert.equal(periodicAfter([100, 100, 100]), true);
  assert.equal(periodicAfter([100, 101, 99, 100, 102]), true, "ordinary timestamp jitter");
  assert.equal(periodicAfter([10, 300, 20, 500, 50, 400]), false, "an event-driven id");
  assert.equal(periodicAfter([100, 100, 100, 200, 100]), true, "one missed frame is not irregular");
});

test("the rendered age cell takes the period's class as the tick ages it", () => {
  clearAllCan();
  const wrap = env.byId("canWrap");
  for (let i = 0; i < 20; i++) {   // a 1 kHz id
    canIngest({ id: 10 + i, ts: 2000 + i / 1000, port: "p1", chan: "event", raw: "!can 1 - 7FF 00" });
  }
  canIngest({ id: 40, ts: 2000.019, port: "p1", chan: "event", raw: "!can 1 - 700 00" });   // one frame, no period
  [1999.0, 1999.01, 1999.3, 1999.32, 1999.8, 1999.85, 2000.01].forEach((ts, i) =>   // irregular
    canIngest({ id: 50 + i, ts, port: "p1", chan: "event", raw: "!can 1 - 600 00" }));
  renderCan();
  const ageClasses = () => wrap.querySelectorAll("tr").slice(1).map((tr) => [tr.children[0].textContent, tr.children.at(-1).className]);
  assert.deepEqual(ageClasses(), [["600", "age-fresh"], ["700", "age-fresh"], ["7FF", "age-fresh"]]);
  const t0 = performance.now.bind(performance);
  performance.now = () => t0() + 2500;
  try {
    timer.fn();
  } finally {
    performance.now = t0;
  }
  assert.deepEqual(ageClasses(), [["600", "age-fresh"], ["700", "age-fresh"], ["7FF", "age-dead"]],
    "2.5 s is ten periods and more for 7FF; 600 is irregular and 700 sent one frame, so neither has a period to miss");
});
