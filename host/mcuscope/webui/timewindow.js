// ---- the time window, and where a time sits in it -----------------------------------
//
// One question - "given the active time mode, the window and the shared right edge, where
// does time t sit on screen, and inversely" - was written five times with independently
// authored arithmetic: three in digital.js (draw, cursor placement, hover inverse) and two
// in plots.js. All five had to agree for the cursor to land on the waveform, and none of
// them was tested: every path is gated on a non-zero clientWidth, which a stubbed DOM
// never has, so the whole concept sat behind the canvas.
//
// It does not need a canvas. Pulling it out is what makes it assertable, and it puts the
// tick-versus-seconds unit hazard behind one function instead of five.

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
  let lo = 0, a = 0, b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] <= xmin) { lo = m; a = m + 1; } else b = m - 1; }
  let hi = n - 1; a = 0; b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] >= xmax) { hi = m; b = m - 1; } else a = m + 1; }
  return [lo, hi];
}

// First index in xs[0, n) whose value is >= xmin, or n if there is none. The analog half
// of visibleRange: uPlot is handed a contiguous slice, so only the left edge is searched,
// and `n` bounds it at a chart's freeze point rather than the array length.
export function firstAtOrAfter(xs, xmin, n) {
  let res = n, a = 0, b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] >= xmin) { res = m; b = m - 1; } else a = m + 1; }
  return res;
}
