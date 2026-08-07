// The freeze registry: one polarity, one live set, one label.
//
// Pause used to be four implementations of one concept. The fan-out inverted polarity
// mid-line - setAutoscroll(p, !live) beside setChartPaused(c, live) - with nothing
// checking the three agreed, and "a rebuild must not un-freeze" was a per-surface
// obligation every surface had failed at least once (c581c07, f40737e, 11f340e).

import test from "node:test";
import assert from "node:assert/strict";
import { webuiUrl } from "./dom_stub.mjs";

const freeze = await import(webuiUrl("freeze.js"));

// A surface of the shape a real one has: a live flag and an id watermark taken at freeze.
function surface(name, { live = true, maxId = 0 } = {}) {
  const s = { name, live, frozenId: null, calls: [] };
  freeze.registerSurface(name, {
    isLive: () => s.live,
    setPaused: (paused) => {
      s.calls.push(paused);
      s.live = !paused;
      s.frozenId = paused ? maxId : null;
    },
    watermark: () => s.frozenId,
  });
  return s;
}

test("pauseAll speaks one polarity to every surface", () => {
  freeze.resetSurfaces();
  const a = surface("a"), b = surface("b"), c = surface("c");

  freeze.pauseAll(true);
  for (const s of [a, b, c]) {
    assert.deepEqual(s.calls, [true], `${s.name} was told the wrong thing`);
    assert.equal(s.live, false);
  }
  freeze.pauseAll(false);
  for (const s of [a, b, c]) assert.equal(s.live, true);
});

test("anyLive is true while any single surface is live", () => {
  freeze.resetSurfaces();
  const a = surface("a"), b = surface("b");
  assert.equal(freeze.anyLive(), true);
  a.live = false;
  assert.equal(freeze.anyLive(), true, "one paused surface is not all of them");
  b.live = false;
  assert.equal(freeze.anyLive(), false);
});

test("the label follows the live set, so the button cannot lie", () => {
  freeze.resetSurfaces();
  const a = surface("a");
  assert.equal(freeze.pauseAllLabel(), "pause all");
  freeze.pauseAll(true);
  assert.equal(freeze.pauseAllLabel(), "resume all");
  a.live = true;                       // one surface resumed on its own
  assert.equal(freeze.pauseAllLabel(), "pause all");
});

test("every surface takes an export watermark when it freezes", () => {
  // The rule f40737e was: a surface shipped with a freeze and no export bound, so a paused
  // export silently ran to the live edge. Asserted across the registry, not per surface.
  freeze.resetSurfaces();
  surface("panes", { maxId: 100 });
  surface("charts", { maxId: 100 });
  surface("digital", { maxId: 100 });

  assert.deepEqual(freeze.watermarks(), { panes: null, charts: null, digital: null });
  freeze.pauseAll(true);
  for (const [name, id] of Object.entries(freeze.watermarks())) {
    assert.equal(id, 100, `${name} froze without recording an export bound`);
  }
  freeze.pauseAll(false);
  for (const [name, id] of Object.entries(freeze.watermarks())) {
    assert.equal(id, null, `${name} kept a stale bound after resuming`);
  }
});

test("a surface without a watermark is refused at registration", () => {
  // The guard that makes the rule above impossible to skip: a new panel cannot ship a
  // freeze and forget the export bound, which is how it shipped last time.
  freeze.resetSurfaces();
  assert.throws(
    () => freeze.registerSurface("half-built", { isLive: () => true, setPaused: () => {} }),
    /needs a watermark/,
  );
  assert.throws(
    () => freeze.registerSurface("no-setter", { isLive: () => true, watermark: () => null }),
    /needs a setPaused/,
  );
});

test("pausing notifies whatever renders the shared label", () => {
  // hooks.liveChanged existed only for this, and only plots.js and digital.js called it.
  freeze.resetSurfaces();
  surface("a");
  let notified = 0;
  freeze.onFreezeChanged(() => { notified += 1; });
  freeze.pauseAll(true);
  assert.equal(notified, 1);
  freeze.freezeChanged();              // a surface's own pause button
  assert.equal(notified, 2);
});
