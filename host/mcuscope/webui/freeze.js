// ---- which surfaces are live, and freezing them together ----------------------------
//
// Pause was one concept with four implementations. A pane held `autoscroll`, a chart held
// `paused`, the digital panel held `digitalPaused`, and the pause-all fan-out inverted
// polarity mid-line:
//
//     setAutoscroll(p, !live); charts.forEach(c => setChartPaused(c, live)); setDigitalPaused(live);
//
// Three setters, two polarities, written adjacent, with nothing checking they agreed.
// anyLive() had to know all three internal shapes, including a guard that existed only
// because the digital flag is meaningful before any lane is. And the rule that a rebuild
// must not un-freeze a surface was a per-surface obligation each surface had failed at
// least once: c581c07 "hold the pause", f40737e "the third mirror to drop a bound",
// 11f340e "reset digital pause on clear-all" are the same fix landing three times.
//
// Surfaces register once here, with one polarity: `setPaused(true)` pauses. The drawn
// freeze stays with the surface, because index, time and id genuinely differ - what lives
// here is the set, the fan-out, the label, the requirement to have an export bound, and
// what a member created later is born into.
//
// The high-rate guard in api.js is deliberately NOT a surface. It stops feeding panes
// under backpressure and releases itself, so folding it in would make a burst relabel the
// button "resume all" while the user had paused nothing. It is automatic, not intent.

const surfaces = new Map();
let onChanged = () => {};
// The pause-all latch: what a surface member created from now on is born into. Without it
// "pause all" only froze the members that already existed - a pane added afterwards came up
// live, a chart rebuilt after clear-all came up live, and the button went on reading "resume
// all" over a UI that was already moving again.
let allPaused = false;

// Register a freeze surface. `watermark()` returns the line id this surface would export
// up to while paused, or null while live. Nothing here consumes it - requiring it is the
// point: shipping a surface with a freeze and no export bound is exactly what f40737e was.
export function registerSurface(name, { isLive, setPaused, watermark }) {
  for (const [key, fn] of [["isLive", isLive], ["setPaused", setPaused],
                           ["watermark", watermark]]) {
    if (typeof fn !== "function") {
      throw new Error(`freeze surface "${name}" needs a ${key}() function`);
    }
  }
  surfaces.set(name, { isLive, setPaused, watermark });
}

// True while anything the pause-all button governs is still live. One definition, so the
// button label and what the button does cannot disagree.
export function anyLive() {
  for (const s of surfaces.values()) if (s.isLive()) return true;
  return false;
}

// Freeze or thaw every surface at one instant. `paused` means paused, for all of them.
export function pauseAll(paused) {
  allPaused = paused;
  for (const s of surfaces.values()) s.setPaused(paused);
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

// A surface changed its own live state (its own pause button was pressed), so whatever
// renders the shared label needs to run. This is what `hooks.liveChanged` was for.
export function onFreezeChanged(fn) { onChanged = fn; }
export function freezeChanged() {
  // Anything running again ends the pause-all latch, so the next new member is born live -
  // which is also what the button now reads.
  if (anyLive()) allPaused = false;
  onChanged();
}

// Tests register real surfaces against a fresh registry.
export function resetSurfaces() { surfaces.clear(); onChanged = () => {}; allPaused = false; }
