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

// A surface of the shape a real one has: a live flag, and - the part that matters - a setPaused that calls freezeChanged(), which every shipped
// surface does. Without it this double could not see the pause-all latch being cleared
// mid-fan-out by its own siblings, and did not.
function surface(name, { live = true } = {}) {
  const s = { name, live, calls: [] };
  freeze.registerSurface(name, {
    isLive: () => s.live,
    setPaused: (paused) => {
      s.calls.push(paused);
      s.live = !paused;
      freeze.freezeChanged();
    },
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

test("a surface without both functions is refused at registration", () => {
  freeze.resetSurfaces();
  assert.throws(() => freeze.registerSurface("no-setter", { isLive: () => true }), /needs a setPaused/);
  assert.throws(() => freeze.registerSurface("no-live", { setPaused: () => {} }), /needs a isLive/);
});

test("pausing notifies whatever renders the shared label", () => {
  // hooks.liveChanged existed only for this, and only plots.js and digital.js called it.
  freeze.resetSurfaces();
  surface("a");
  let notified = 0;
  freeze.onFreezeChanged(() => { notified += 1; });
  freeze.pauseAll(true);
  // At least once, not exactly once: each surface notifies from its own setPaused as well,
  // so a fan-out over N surfaces refreshes the label N+1 times. The render is one
  // textContent assignment, so the repeats cost nothing and are not worth suppressing.
  assert.ok(notified >= 1, "pause-all did not refresh the shared label");
  const after = notified;
  freeze.freezeChanged();              // a surface's own pause button
  assert.equal(notified, after + 1);
});

test("a member created while the UI is frozen is born paused", () => {
  // Three surfaces got this wrong the same way: pause-all only froze the members that
  // existed, so a pane added afterwards, and a chart rebuilt after clear-all, came up live
  // under a button still reading "resume all".
  freeze.resetSurfaces();
  // TWO live surfaces, not one: the latch is cleared by a SIBLING's freezeChanged() during
  // the fan-out, which cannot happen with a single surface (its own freezeChanged() sees
  // nothing else live, so the latch survives either ordering). With one surface registered
  // this test passed against the pre-fix ordering. The app registers three.
  const a = surface("a"), z = surface("z");
  assert.equal(freeze.bornPaused(), false, "nothing has been paused yet");

  freeze.pauseAll(true);
  assert.equal(freeze.bornPaused(), true,
    "a sibling's freezeChanged() cleared the latch part-way through the fan-out");
  assert.equal(z.live, false);

  // A new member joins the freeze, so the label stays honest.
  const b = surface("b", { live: false });
  assert.equal(freeze.pauseAllLabel(), "resume all");

  // Anything running again ends the latch: the next member is born live.
  b.live = true;
  freeze.freezeChanged();
  assert.equal(freeze.bornPaused(), false);
  assert.equal(a.live, false, "ending the latch must not thaw anything by itself");
});

test("the latch does not freeze the first member at load", () => {
  // bornPaused() cannot be !anyLive(): at first load every surface is empty, so anyLive() is
  // false and the very first pane would come up frozen with no way to know why.
  freeze.resetSurfaces();
  freeze.registerSurface("panes", { isLive: () => false, setPaused: () => {} });
  assert.equal(freeze.anyLive(), false);
  assert.equal(freeze.bornPaused(), false);
});

test("resume-all clears the latch", () => {
  freeze.resetSurfaces();
  surface("a");
  freeze.pauseAll(true);
  freeze.pauseAll(false);
  assert.equal(freeze.bornPaused(), false);
});
