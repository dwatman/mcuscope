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

// One window object from an explicit [xmin, xmax], projected onto `width` pixels.
function windowOf(xmin, xmax, width) {
  const span = xmax - xmin;
  return {
    span,
    xmin,
    xmax,
    width,
    toPx: (t) => ((t - xmin) / span) * width,
    fromPx: (x) => xmin + (x / width) * span,
  };
}

// A right-anchored window of `windowSec`, projected onto `width` pixels. `edge` is the
// shared right edge already in this mode's units (the newest sample, or the frozen edge
// when paused - which is what pins the mapping while a surface is frozen).
export function timeWindow(timeMode, windowSec, edge, width = 0) {
  return windowOf(edge - spanFor(timeMode, windowSec), edge, width);
}

// ---- the one drag zoom, shared by every chart and the digital lanes ------------------
//
// SPEC 9.2 promises one synchronized x axis across the stacked panels, so the zoom is a
// single range, not a field per chart. It lives here, beside the projection that consumes
// it, because plots.js and digital.js both read it and digital.js must not import plots.js
// (plots.js imports digital.js, and a cycle would make the freeze registration order depend
// on which module a test imports first).
let zoom = null;                       // {mode, min, max}, in the units of the mode dragged in
export function getZoom() { return zoom; }
export function setZoom(z) { zoom = z; }

// The zoom if it stands in the active mode, else null. A range is recorded in the units of
// the mode it was dragged in, so it means nothing under another one, and a zero-width range
// has no projection at all (its span would divide by zero).
export function zoomFor(z, timeMode) {
  return z && z.mode === timeMode && z.max > z.min ? z : null;
}

// The window a chart or a lane draws: the shared zoom when one stands, else the
// right-anchored tail. The one place the two are chosen between, so a panel cannot end up
// zoomed while its sibling follows the tail under a cursor that claims to be shared.
export function windowFor(z, timeMode, windowSec, edge, width = 0) {
  const act = zoomFor(z, timeMode);
  return act ? windowOf(act.min, act.max, width) : timeWindow(timeMode, windowSec, edge, width);
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

// The on-screen segments of a transition-reduced lane: level vs[i] holds from xs[i] to xs[i+1],
// the newest to the right edge, each clamped to [0, width] px. Nothing is drawn left of xs[0],
// whose earlier level is unknown (after a clear-all, on a fresh page, past a trimmed ring), just
// as an analog trace starts at its first point. Returns [{i, x0, x1}] with x1 > x0.
export function laneSegments(xs, win) {
  const n = xs.length, out = [];
  if (!n) return out;
  const [lo, hi] = visibleRange(xs, win.xmin, win.xmax);
  for (let i = lo; i <= hi; i++) {
    const x0 = Math.max(0, win.toPx(xs[i]));
    const x1 = Math.min(win.width, i + 1 < n ? win.toPx(xs[i + 1]) : win.width);
    if (x1 > x0) out.push({ i, x0, x1 });
  }
  return out;
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

// What the plot x axis is labelled under each time base. Delta is a terminal column (a gap to
// the line above has no meaning as an axis), so the charts stay on host time under it.
export const TIME_AXIS_LABELS = {
  host: "x: host", tick: "x: tick (ms)", rel: "x: rel (s)", delta: "x: host (delta is terminal only)",
};

// Pixels per axis label, for the chart x axis and the lane ruler alike, so the two agree.
export const AXIS_PX_PER_TICK = 70;

// Axis steps at or above one second, in seconds: clock-friendly rather than 1-2-5, so a
// 5 min window ticks every minute and not every 100 s.
const NICE_SECONDS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

// Tick positions over `win` ({xmin, xmax}): the smallest nice step giving at most `maxTicks`,
// aligned to the displayed zero (the shared anchor in tick and rel mode, the epoch in host
// mode), so labels land on round numbers. Returns {step, ticks} in the mode's units.
export function axisTicks({ timeMode, anchorTs, anchorTick }, win, maxTicks) {
  const span = win.xmax - win.xmin;
  if (!(span > 0) || !(maxTicks >= 1)) return { step: 0, ticks: [] };
  const perSecond = timeMode === "tick" ? 1000 : 1;
  const s = span / maxTicks / perSecond;
  let stepS;
  if (s < 1) {
    const mag = 10 ** Math.floor(Math.log10(s));
    stepS = [1, 2, 5, 10].map((m) => m * mag).find((x) => x >= s * (1 - 1e-9));
  } else {
    stepS = NICE_SECONDS.find((x) => x >= s) || Math.ceil(s / 3600) * 3600;
  }
  const step = stepS * perSecond;
  const zero = timeMode === "tick" ? (anchorTick || 0) : timeMode === "rel" ? (anchorTs || 0) : 0;
  const ticks = [];
  for (let i = Math.ceil((win.xmin - zero) / step); ticks.length <= maxTicks; i++) {
    const v = zero + i * step;
    if (v > win.xmax) break;
    ticks.push(v);
  }
  return { step, ticks };
}

// An axis label for `v`, with as many decimals as `step` needs (a 0.2 s step shows tenths).
// Bare numbers: the unit is in TIME_AXIS_LABELS. Shared by the chart x axis and the lane ruler.
export function fmtAxisTick({ timeMode, anchorTs, anchorTick }, v, step) {
  const dec = step >= 1 || !(step > 0) ? 0 : Math.min(3, Math.ceil(-Math.log10(step) - 1e-9));
  // + 0 turns a rounded -0 (float error just below the zero tick) into a plain 0.
  const num = (x) => (Number(x.toFixed(dec)) + 0).toFixed(dec);
  if (timeMode === "tick") return num(v - (anchorTick == null ? 0 : anchorTick));
  if (timeMode === "rel") return num(v - (anchorTs == null ? 0 : anchorTs));
  const d = new Date(v * 1000), p = (n) => String(n).padStart(2, "0");
  const hms = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return dec ? hms + "." + String(d.getMilliseconds()).padStart(3, "0").slice(0, dec) : hms;
}

// The width of a drag zoom, as its chip reads it: ms under a second, then seconds, then m:ss.
export function fmtZoomSpan(z) {
  const s = (z.max - z.min) / (z.mode === "tick" ? 1000 : 1);
  if (s < 1) return Math.max(1, Math.round(s * 1000)) + " ms";
  if (s < 10) return s.toFixed(2) + " s";
  if (s < 60) return s.toFixed(1) + " s";
  const whole = Math.round(s);
  return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, "0")}s`;
}

// The terminal's delta column: seconds since the previous displayed row, signed only when
// negative (a backfill can land a row behind its neighbour). The first row of a pane has no
// predecessor and reads as zero.
export function fmtDelta(ts, prevTs) {
  const d = prevTs == null ? 0 : ts - prevTs;
  return (d < 0 ? "" : "+") + d.toFixed(3) + "s";
}

// ---- estimated MCU tick for a line that carries none (SPEC 9.1) ----------------------
//
// Only CAN, plot and firmware-marker lines carry a tick (state.js lineTick), so under the
// tick time base every debug, cmd, resp and sys line would read "-". Those get an estimate
// instead: the tick of the nearest EARLIER line from the same port that carries one, plus
// the host-time gap in ms. Earlier only, so a reboot (a later anchor with a smaller tick)
// never re-times the lines before it; per port, since two boards run two clocks.

const TICK_WRAP = 2 ** 32;          // SPEC 2.5: ticks wrap at 2^32
const ANCHOR_CAP = 10000;           // per port; the oldest anchor goes first
const ANCHOR_MIN_GAP_S = 1;         // a continuing clock needs at most one anchor a second
const ANCHOR_SLACK_MS = 200;        // host receive jitter a continuing clock may show

// port -> anchors {id, ts, tick}, ascending by id.
export function newTickAnchors() { return new Map(); }

// Index of the last anchor with id < `id`, or -1.
function lastBefore(list, id) {
  let lo = 0, hi = list.length - 1, res = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (list[m].id < id) { res = m; lo = m + 1; } else hi = m - 1;
  }
  return res;
}

// Record a line's own tick as an anchor for its port. Lines may arrive out of id order (a
// history page lands below the live rows), so the anchor is inserted in place. A newest anchor
// within ANCHOR_MIN_GAP_S of the previous one is skipped when the previous one predicts it,
// which bounds the list for a 350 lines/s plot stream; a reset or a wrap is always kept.
export function noteTickAnchor(anchors, port, id, ts, tick) {
  if (typeof id !== "number" || !Number.isFinite(ts) || !Number.isFinite(tick)) return;
  let list = anchors.get(port);
  if (!list) { list = []; anchors.set(port, list); }
  const i = lastBefore(list, id) + 1;
  if (i < list.length && list[i].id === id) return;   // already noted
  const prev = list[i - 1];
  if (i === list.length && prev && ts >= prev.ts && ts - prev.ts < ANCHOR_MIN_GAP_S
      && Math.abs(tick - prev.tick - (ts - prev.ts) * 1000) <= ANCHOR_SLACK_MS) return;
  list.splice(i, 0, { id, ts, tick });
  if (list.length > ANCHOR_CAP) list.splice(0, list.length - ANCHOR_CAP);
}

// ---- the tick axis across an MCU reset or a 2^32 wrap (SPEC 9.2) ---------------------
//
// A restarted clock is drawn continuing by the host-time gap: each port keeps epochs, each
// an offset added to the raw ticks from its host time on. A member (a chart, a lane) detects
// the jump in its own samples, which arrive in tick order; the epoch it opens is the port's,
// so a sibling, a member born after the reset and a hovered terminal line read the same x.

// A step back within this is a repeated or reordered tick, left to the caller's nudge: a
// producer reorders by milliseconds, a reset takes the clock back by the board's uptime.
export const TICK_JUMP_SLACK_MS = 100;
const TICK_EPOCH_CAP = 1000;        // per port; a boot-looping board must not grow it forever

// port -> epochs {host, offset}, ascending by host.
export function newTickClocks() { return new Map(); }

// The newest epoch of `list` starting in [lo, hi] other than `not`, or null.
function epochIn(list, lo, hi, not) {
  for (let i = list.length - 1; i >= 0; i--) {
    const e = list[i];
    if (e.host > hi) continue;
    return e.host >= lo && e !== not ? e : null;
  }
  return null;
}

// The offset in force at host time `host` on `port`: what a terminal line's tick is drawn at.
export function tickOffsetAt(clocks, port, host) {
  const e = epochIn(clocks.get(port) || [], -Infinity, host, null);
  return e ? e.offset : 0;
}

// One sample's drawn tick. `prev` is what this returned for the member's previous sample, or
// null. Returns {tick, host, offset, epoch, x, restart}; `restart` says the offset moved, so
// the member breaks its line before this sample.
export function continueTick(clocks, port, prev, tick, host) {
  let list = clocks.get(port);
  if (!list) { list = []; clocks.set(port, list); }
  const out = (epoch, restart) => {
    const offset = epoch ? epoch.offset : 0;
    return { tick, host, offset, epoch, x: tick + offset, restart };
  };
  if (!prev) return out(epochIn(list, -Infinity, host, null), false);
  if (tick < prev.tick - TICK_JUMP_SLACK_MS) {
    // A sibling may have opened this reset's epoch already, a little later on the host clock.
    let e = epochIn(list, prev.host, host + TICK_JUMP_SLACK_MS / 1000, prev.epoch);
    if (!e) {
      e = { host, offset: prev.tick + prev.offset + Math.max(0, host - prev.host) * 1000 - tick };
      const at = list.findIndex((o) => o.host > host);
      list.splice(at < 0 ? list.length : at, 0, e);
      if (list.length > TICK_EPOCH_CAP) list.splice(0, list.length - TICK_EPOCH_CAP);
    }
    return out(e, true);
  }
  // No jump of its own, yet the port restarted since: this member was born in the same read
  // just before the sibling that saw the reset.
  const e = epochIn(list, prev.host, host, prev.epoch);
  return e ? out(e, true) : out(prev.epoch, false);
}

// The anchor `row` is estimated from and the host gap to it in ms, or null.
function anchorFor(anchors, row) {
  if (!row || typeof row.id !== "number" || !Number.isFinite(row.ts)) return null;
  const list = anchors.get(row.port || "-");
  const i = list ? lastBefore(list, row.id) : -1;
  return i < 0 ? null : { a: list[i], gap: Math.round((row.ts - list[i].ts) * 1000) };
}

// The estimated tick for `row`, or null with no earlier anchor on its port.
export function estimateTick(anchors, row) {
  const f = anchorFor(anchors, row);
  if (!f) return null;
  const t = f.a.tick + f.gap;
  return ((t % TICK_WRAP) + TICK_WRAP) % TICK_WRAP;
}

// Where that estimate is drawn: the anchor's drawn tick plus the gap, unwrapped. The offset is
// the anchor's, not the row's: a line read with the first sample after a reset shares its host time.
export function estimateTickX(anchors, clocks, row) {
  const f = anchorFor(anchors, row);
  return f ? f.a.tick + tickOffsetAt(clocks, row.port || "-", f.a.ts) + f.gap : null;
}
