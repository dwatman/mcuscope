// ---- which surfaces are live, and freezing them together ----------------------------
//
// Surfaces register once here, with one polarity: `setPaused(true)` pauses. What lives
// here is the set, the fan-out, the label, the requirement to have an export bound, and
// what a member created later is born into. The drawn freeze stays with each surface,
// because index, time and id genuinely differ.
//
// The high-rate guard in api.js is deliberately NOT a surface. It stops feeding panes
// under backpressure and releases itself, so folding it in would make a burst relabel the
// button "resume all" while the user had paused nothing. It is automatic, not intent.

const surfaces = new Map();
let onChanged = () => {};
// The pause-all latch: what a member created from now on is born into, so "pause all"
// governs a pane added afterwards and a chart rebuilt after clear-all.
let allPaused = false;

// Register a freeze surface. `watermark()` returns the line id this surface would export up
// to while paused, or null while live. Nothing here consumes it; requiring it is the point,
// so a new panel cannot ship a freeze and a live-edge export.
export function registerSurface(name, { isLive, setPaused, watermark }) {
  for (const [key, fn] of [["isLive", isLive], ["setPaused", setPaused],
                           ["watermark", watermark]]) {
    if (typeof fn !== "function") {
      throw new Error(`freeze surface "${name}" needs a ${key}() function`);
    }
  }
  surfaces.set(name, { isLive, setPaused, watermark });
}

// The export bound for a surface made of several frozen members: the earliest id among them,
// null while none is frozen. One shape for every such surface, because the two hand-written
// versions disagreed about the empty set: a filter that empties a non-empty list left
// Math.min() answering Infinity, which is not a line id at all. A member frozen before it
// held any row answers 0, which exports nothing.
export function minWatermark(ids) {
  if (!ids.length) return null;             // nothing frozen: the surface is live
  const known = ids.filter((v) => v != null);
  return known.length ? Math.min(...known) : 0;
}

// True while anything the pause-all button governs is still live. One definition, so the
// button label and what the button does cannot disagree.
export function anyLive() {
  for (const s of surfaces.values()) if (s.isLive()) return true;
  return false;
}

// Freeze or thaw every surface at one instant. `paused` means paused, for all of them.
export function pauseAll(paused) {
  // The latch is set AFTER the fan-out: each surface calls freezeChanged() from its own
  // setPaused, which ends the latch while siblings are still live.
  for (const s of surfaces.values()) s.setPaused(paused);
  allPaused = paused;
  freezeChanged();
}

// Should a member created right now (a new pane, a chart rebuilt after clear-all) come up
// paused? True from "pause all" until anything is live again. It cannot be derived from
// anyLive(): at first load nothing has any members, so anyLive() is false there too, and the
// first pane would be born frozen.
export function bornPaused() { return allPaused; }

// What the pause-all button should read now.
export function pauseAllLabel() {
  return anyLive() ? "pause all" : "resume all";
}

// Each surface's export bound, by name; null where the surface is live.
export function watermarks() {
  const out = {};
  for (const [name, s] of surfaces) out[name] = s.watermark();
  return out;
}

// A surface changed its own live state, so whatever renders the shared label needs to run.
export function onFreezeChanged(fn) { onChanged = fn; }
export function freezeChanged() {
  if (anyLive()) allPaused = false;   // anything running again ends the latch

  onChanged();
}

// Tests register real surfaces against a fresh registry.
export function resetSurfaces() { surfaces.clear(); onChanged = () => {}; allPaused = false; }
