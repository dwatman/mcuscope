// The time-to-pixel projection: five hand-written copies before this, and no test.
//
// Every one of them was gated behind a canvas with a non-zero clientWidth, which the DOM
// stub never provides, so drawDigitalLane, setDigitalCursorAt and onDigitalHover all took
// an early return under test. Their arithmetic had to agree for the cursor to land on the
// waveform and nothing checked that it did. No DOM here at all - that is the point.

import test from "node:test";
import assert from "node:assert/strict";
import { webuiUrl } from "./dom_stub.mjs";

const { spanFor, timeWindow, visibleRange, firstAtOrAfter } =
  await import(webuiUrl("timewindow.js"));

const close = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;

test("tick mode counts milliseconds, host and rel count seconds", () => {
  assert.equal(spanFor("tick", 30), 30_000);
  assert.equal(spanFor("host", 30), 30);
  assert.equal(spanFor("rel", 30), 30);
  // The 1000x unit hazard was repeated in all five copies; one of them getting it wrong
  // is silent, so it is pinned here rather than inferred from a drawn pixel.
});

test("a zero or missing window never yields a divide-by-zero projection", () => {
  for (const bad of [0, NaN, undefined]) {
    assert.equal(spanFor("host", bad), 1);
    const w = timeWindow("host", bad, 100, 500);
    assert.ok(Number.isFinite(w.toPx(100)));
  }
});

test("the window is right-anchored on the edge", () => {
  const w = timeWindow("host", 30, 1000, 600);
  assert.equal(w.xmax, 1000);
  assert.equal(w.xmin, 970);
  assert.equal(w.span, 30);
  assert.equal(w.toPx(1000), 600);   // the edge is the right-hand pixel
  assert.equal(w.toPx(970), 0);      // the window start is the left-hand pixel
  assert.equal(w.toPx(985), 300);    // and the middle is the middle
});

test("fromPx inverts toPx", () => {
  for (const mode of ["host", "tick"]) {
    const w = timeWindow(mode, 30, 5000, 800);
    for (const t of [5000, 4999.5, 4985, 4970.001]) {
      assert.ok(close(w.fromPx(w.toPx(t)), t, 1e-6), `${mode} round trip at ${t}`);
    }
  }
});

test("a tick window is 1000x a host window over the same pixels", () => {
  const host = timeWindow("host", 10, 0, 100);
  const tick = timeWindow("tick", 10, 0, 100);
  assert.equal(tick.span, host.span * 1000);
  // Half a window back is half way across, whichever unit it is measured in.
  assert.equal(host.toPx(-5), 50);
  assert.equal(tick.toPx(-5000), 50);
});

test("a frozen edge pins the mapping", () => {
  // A paused surface keeps drawing against the edge it froze at, so the same time keeps
  // landing on the same pixel however far the live edge has since moved.
  const frozen = timeWindow("host", 20, 1000, 400);
  const later = timeWindow("host", 20, 1500, 400);
  assert.equal(frozen.toPx(990), 200);
  assert.notEqual(later.toPx(990), 200);
  assert.equal(timeWindow("host", 20, 1000, 400).toPx(990), frozen.toPx(990));
});

test("a time outside the window projects outside the pixel range", () => {
  // The cursor code relies on this to decide whether to hide itself.
  const w = timeWindow("host", 30, 1000, 600);
  assert.ok(w.toPx(1001) > 600);
  assert.ok(w.toPx(900) < 0);
});

test("visibleRange brackets the window and matches a linear scan", () => {
  const xs = [0, 10, 20, 30, 40, 50, 60];
  const [lo, hi] = visibleRange(xs, 22, 45);
  // lo is the last vertex at or before xmin: the level already showing at the left edge.
  assert.equal(lo, 2);          // xs[2] === 20
  // hi is the first at or after xmax.
  assert.equal(hi, 5);          // xs[5] === 50
  // Drawing [lo, hi] must cover every vertex a clipping walk would have drawn.
  const scanned = xs.map((x, i) => i).filter((i) => xs[i] >= 22 && xs[i] <= 45);
  for (const i of scanned) assert.ok(i >= lo && i <= hi, `vertex ${i} outside [${lo},${hi}]`);
});

test("visibleRange handles the edges and a single vertex", () => {
  assert.deepEqual(visibleRange([5], 0, 10), [0, 0]);
  const xs = [0, 1, 2, 3];
  assert.deepEqual(visibleRange(xs, -5, 99), [0, 3]);   // whole array visible
  const [lo] = visibleRange(xs, 3, 99);
  assert.equal(lo, 3);                                   // window starts at the last vertex
});

test("firstAtOrAfter finds the left edge and respects the freeze bound", () => {
  const xs = [0, 10, 20, 30, 40, 50];
  assert.equal(firstAtOrAfter(xs, 20, xs.length), 2);
  assert.equal(firstAtOrAfter(xs, 21, xs.length), 3);   // strictly at-or-after
  assert.equal(firstAtOrAfter(xs, -1, xs.length), 0);
  assert.equal(firstAtOrAfter(xs, 999, xs.length), 6);  // none: the length
  // `n` is a paused chart's frozenLen: the search must not look past the freeze point.
  assert.equal(firstAtOrAfter(xs, 40, 3), 3);
});
