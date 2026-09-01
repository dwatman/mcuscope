// ---- the time window, and where a time sits in it -----------------------------------
//
// "Given the active time mode, the window and the shared right edge, where does time t sit
// on screen, and inversely" - once, for the five drawing and cursor paths that must agree
// for the cursor to land on the waveform. Here rather than beside a canvas because every
// caller is gated on a non-zero clientWidth, which a stubbed DOM never has, so none of it
// was assertable in place.

// Window seconds as a span in the active mode's units. `tick` counts milliseconds; host
// and rel count seconds. Forgetting the 1000 is silent - the window is simply wrong by
// three orders of magnitude - which is exactly why it belongs in one place. The `|| 1`
// keeps a zero or NaN window from producing a divide-by-zero projection.
export function spanFor(timeMode, windowSec) {
  return (timeMode === "tick" ? windowSec * 1000 : windowSec) || 1;
}

// A right-anchored window of `windowSec`, projected onto `width` pixels. `edge` is the
// shared right edge already in this mode's units (the newest sample, or the frozen edge
// when paused - which is what pins the mapping while a surface is frozen).
export function timeWindow(timeMode, windowSec, edge, width = 0) {
  const span = spanFor(timeMode, windowSec);
  const xmin = edge - span;
  return {
    span,
    xmin,
    xmax: edge,
    width,
    toPx: (t) => ((t - xmin) / span) * width,
    fromPx: (x) => xmin + (x / width) * span,
  };
}

// The index range of vertices that touch [xmin, xmax]: `lo` is the last vertex at or
// before xmin (the level shown at the left edge), `hi` the first at or after xmax.
// Drawing only [lo, hi] is pixel-identical to walking every vertex and clipping, but
// O(visible) instead of O(history), so a fast-toggling lane over a wide buffer stays
// cheap. `xs` must be sorted ascending.
export function visibleRange(xs, xmin, xmax) {
  const n = xs.length;
  return [lastAtOrBefore(xs, xmin, n, 0), firstAtOrAfter(xs, xmax, n, n - 1)];
}

// Binary search over a sorted ascending `xs[0, n)`, in the two directions the drawing needs.
// `fallback` is the answer when nothing qualifies, which differs per caller: the left edge
// wants index 0, the right edge the last vertex, and the analog slice wants `n` (empty).
function lastAtOrBefore(xs, x, n, fallback) {
  let res = fallback, a = 0, b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] <= x) { res = m; a = m + 1; } else b = m - 1; }
  return res;
}

// First index in xs[0, n) whose value is >= xmin, or n if there is none. The analog half
// of visibleRange: uPlot is handed a contiguous slice, so only the left edge is searched,
// and `n` bounds it at a chart's freeze point rather than the array length.
export function firstAtOrAfter(xs, x, n, fallback = n) {
  let res = fallback, a = 0, b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] >= x) { res = m; b = m - 1; } else a = m + 1; }
  return res;
}

// The time under a cursor, as the analog legend and the digital cursor tag both print it, so
// one cursor never shows two times. `anchors` is the shared state (timeMode, anchorTs,
// anchorTick); relative modes are zeroed at the anchors.
export function fmtTime({ timeMode, anchorTs, anchorTick }, v) {
  if (v == null) return "--";
  if (timeMode === "tick") return Math.round(v - (anchorTick == null ? 0 : anchorTick)) + " ms";
  if (timeMode === "rel") return (v - (anchorTs == null ? 0 : anchorTs)).toFixed(3) + " s";
  const d = new Date(v * 1000), p = (n) => String(n).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${ms}`;
}
