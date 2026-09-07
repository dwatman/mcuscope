// can.js: the wall-clock tick refreshes the age column in place; the table is rebuilt only
// when a frame landed (canDirty), and only when the row SET changed at that.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { canIngest, renderCan, clearAllCan, initCan, canRows } = await import(webuiUrl("can.js"));

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
