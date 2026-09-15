// Owner ruling D-3 (2026-09-15): the tick axis continues across an MCU reset or a 2^32 wrap
// by the host-time gap. timewindow.continueTick is the DOM-free half, driven directly here.

import test from "node:test";
import assert from "node:assert/strict";
import { webuiUrl } from "./dom_stub.mjs";

const { continueTick, newTickClocks, tickOffsetAt, TICK_JUMP_SLACK_MS } =
  await import(webuiUrl("timewindow.js"));

// Feed one member a list of [tick, host] samples; returns every result.
function run(clocks, port, samples, prev = null) {
  const out = [];
  for (const [tick, host] of samples) { prev = continueTick(clocks, port, prev, tick, host); out.push(prev); }
  return out;
}

test("a repeated tick and a step back within the slack are not a restart", () => {
  const clocks = newTickClocks();
  const r = run(clocks, "p1", [[5000, 10], [5000, 10], [5000 - TICK_JUMP_SLACK_MS, 10.5]]);
  assert.deepEqual(r.map((o) => [o.x, o.restart]), [[5000, false], [5000, false], [4900, false]],
    "the caller's nudge owns these; an offset here would shift every later sample");
  assert.equal(clocks.get("p1").length, 0);
});

test("a reset continues by the host-time gap, and the samples after it keep that offset", () => {
  const clocks = newTickClocks();
  const r = run(clocks, "p1", [[1000000, 100], [1000010, 100.5], [4, 110.5], [1004, 111.5]]);
  // 10 s of host time passed since the last sample at x 1000010, so the first sample lands at 1010010.
  assert.deepEqual(r.map((o) => [o.x, o.restart]),
    [[1000000, false], [1000010, false], [1010010, true], [1011010, false]]);
  assert.equal(tickOffsetAt(clocks, "p1", 110.49), 0, "a line before the reset is not offset");
  assert.equal(tickOffsetAt(clocks, "p1", 110.5), 1010006, "the reset's own line is");
});

test("a 2^32 wrap is a restart like any other backward jump", () => {
  const clocks = newTickClocks();
  const r = run(clocks, "p1", [[4294967000, 50], [200, 50.5], [700, 51]]);
  assert.deepEqual(r.map((o) => o.x), [4294967000, 4294967500, 4294968000]);
  assert.equal(r[1].restart, true);
});

test("a second reset builds on the first offset, and the hover picks each epoch by host time", () => {
  const clocks = newTickClocks();
  const r = run(clocks, "p1", [[9000, 1], [10, 3], [2010, 5], [8, 6]]);
  assert.deepEqual(r.map((o) => o.x), [9000, 11000, 13000, 14000]);
  assert.deepEqual([0.5, 3, 5.9, 6, 99].map((h) => tickOffsetAt(clocks, "p1", h)),
    [0, 10990, 10990, 13992, 13992]);
});

test("a host time that stepped back gives a zero gap, not a negative one", () => {
  const clocks = newTickClocks();
  const r = run(clocks, "p1", [[70000, 20], [3, 19.5]]);
  assert.equal(r[1].x, 70000, "the axis must not run backwards past the sample before");
});

test("a sibling seeing the same reset shares the epoch, even a little earlier on the host clock", () => {
  const clocks = newTickClocks();
  const a = run(clocks, "p1", [[50000, 10], [7, 20]]);
  const b = run(clocks, "p1", [[50020, 10.02], [9, 19.95]]);
  assert.equal(clocks.get("p1").length, 1, "one reset is one epoch");
  assert.equal(b[1].offset, a[1].offset, "a second offset would draw the two charts apart");
  assert.equal(b[1].restart, true, "the sibling still breaks its own line");
});

test("a member born after a reset starts on the port's offset; another port is untouched", () => {
  const clocks = newTickClocks();
  run(clocks, "p1", [[50000, 10], [7, 20]]);
  const late = run(clocks, "p1", [[2007, 22]]);
  assert.equal(late[0].x, 2007 + 59993);
  assert.equal(late[0].restart, false, "nothing of its own to break");
  assert.equal(run(clocks, "p2", [[2007, 22]])[0].x, 2007);
});

test("a member born in the same read just before the sibling that saw the reset adopts its epoch", () => {
  const clocks = newTickClocks();
  const a = run(clocks, "p1", [[50000, 10]]);
  const b = run(clocks, "p1", [[5, 20]]);          // born first, in the read the reset landed in
  run(clocks, "p1", [[6, 20]], a[0]);              // the sibling sees the jump in the same read
  const b2 = continueTick(clocks, "p1", b[0], 105, 20.1);
  assert.equal(b[0].x, 5);
  assert.equal(b2.x, 105 + 50000 + 10000 - 6, "the member stayed on raw ticks while its siblings moved");
  assert.equal(b2.restart, true, "the point before the adoption must not be joined to it");
  assert.equal(continueTick(clocks, "p1", b2, 205, 20.2).restart, false, "adopted once, not per sample");
});

test("a member past the sibling's epoch does not re-adopt it on a jump of its own", () => {
  const clocks = newTickClocks();
  const a = run(clocks, "p1", [[50000, 10], [6, 20]]);
  const b = run(clocks, "p1", [[50001, 10], [7, 20.01], [9000, 29]]);
  const second = continueTick(clocks, "p1", b.at(-1), 3, 40);
  assert.notEqual(second.epoch, a[1].epoch, "a second reset reusing the first epoch draws on top of it");
  assert.equal(second.x, 9000 + b.at(-1).offset + 11000);
});

test("the epoch list is bounded for a board that resets over and over", () => {
  const clocks = newTickClocks();
  let prev = null;
  for (let i = 0; i < 1200; i++) {
    prev = continueTick(clocks, "p1", prev, 5000, i * 10);
    prev = continueTick(clocks, "p1", prev, 1, i * 10 + 5);
  }
  assert.equal(clocks.get("p1").length, 1000);
  assert.equal(clocks.get("p1")[0].host, 200 * 10 + 5, "the oldest go first");
});

test("an epoch opened earlier on the host clock than one already held is filed in host order", () => {
  const clocks = newTickClocks();
  run(clocks, "p1", [[90000, 400], [5, 500]]);                  // a live chart's reset at 500 s
  const seeded = run(clocks, "p1", [[70000, 200], [3, 300]]);   // a later seed's older reset at 300 s
  assert.deepEqual(clocks.get("p1").map((e) => e.host), [300, 500]);
  assert.equal(tickOffsetAt(clocks, "p1", 400), seeded[1].offset, "a line at 400 s read the wrong epoch");
});

test("clearing the clocks puts a new member back on raw ticks", () => {
  const clocks = newTickClocks();
  run(clocks, "p1", [[50000, 10], [7, 20]]);
  clocks.clear();
  assert.equal(run(clocks, "p1", [[7, 21]])[0].x, 7);
  assert.equal(tickOffsetAt(clocks, "p1", 21), 0);
});
