import { $, state, hooks, colorFor, saveColor, buildWindowButtons, downloadCsv,
         nearestX, PLOT_CAP, PLOT_SLACK, rgbToHex } from "./state.js";

// ---- digital / enum panel: canvas lanes below the analog charts ---------------------
//
// Enum and packed-bits channels do not belong on an auto-ranged y axis; they render as
// aligned logic-analyser lanes. Each lane keeps a transition-reduced ring (one vertex per
// value change, so a level held constant is a single segment), drawn to its own <canvas>
// at devicePixelRatio. bits draw as square waves with a faint high-fill; enums draw as an
// FPGA-style monochrome bus envelope with X-crossings and a right-clipped centred label.
// The panel shares the analog time base (host/tick/rel), window, and global pause.

const DLANE_H = 34;                 // must match .dlane { height } in style.css
const MAX_LANES = 64;               // cap on distinct digital lanes, so a device emitting rotating
                                     // enum/bits names cannot grow the DOM/heap forever
let laneCapWarned = false;
const digitalLanes = new Map();     // name -> lane {name, kind, group, labels, color, xsHost, xsTick, vs, canvas, ...}
let digitalPaused = false;          // global freeze (mirrors the analog charts)
let digitalFrozen = null;           // {host, tick} right-edge captured at pause
let digitalCursorX = null;          // time value the digital panel is currently driving the analog cursor to
let chartHoverX = null;             // time under the pointer while it rests over an analog chart
let digitalWindow = 30;             // seconds shown; the panel has its OWN window (like each chart)
let digitalCollapsed = false;       // lanes hidden via the header collapse button
let digitalPauseBtn = null;         // header pause/resume button (built in buildDigitalHead)
let digitalPausedTag = null;        // header "paused" tag

function digitalIngest(sid, points, x) {
  showDigital();
  for (const [name, val, ch] of points) {
    let lane = digitalLanes.get(name);
    if (!lane) {
      if (digitalLanes.size >= MAX_LANES) {
        if (!laneCapWarned) {
          laneCapWarned = true;
          console.warn(`digital: lane cap (${MAX_LANES}) reached, ignoring new lane "${name}"`);
          updateDigitalCount();
        }
        continue;
      }
      lane = addDigitalLane(name, ch);
    }
    const n = lane.xsHost.length;
    // Transition reduction: store a vertex only when the value changes (plus the first sample).
    // vs[i] is held from its stored time xs[i] until the next vertex xs[i+1], and the draw
    // functions extend the first/last segment to the visible edges - so a repeat value adds
    // nothing and must NEVER overwrite the held level's recorded start time (doing so would
    // drag the segment forward and render it as a narrow right-shifted sliver).
    if (n === 0 || lane.vs[n - 1] !== val) {
      // Keep BOTH arrays strictly increasing: valueAt/nearestX/digitalRightEdge binary-search
      // and take a max, which need monotonic x in whichever array the active time mode reads.
      let hx = x.host, tx = x.tick;
      if (n) {
        if (hx <= lane.xsHost[n - 1]) hx = lane.xsHost[n - 1] + 1e-4;
        if (tx <= lane.xsTick[n - 1]) tx = lane.xsTick[n - 1] + 1e-4;
      }
      lane.xsHost.push(hx); lane.xsTick.push(tx); lane.vs.push(val);
      // Block trim (see PLOT_SLACK): shift() per sample is O(PLOT_CAP) once at cap.
      if (lane.vs.length > PLOT_CAP + PLOT_SLACK) {
        const drop = lane.vs.length - PLOT_CAP;
        lane.xsHost.splice(0, drop); lane.xsTick.splice(0, drop); lane.vs.splice(0, drop);
      }
    }
    if (!digitalPaused) {   // paused: freeze the readout with the frozen window
      lane.dirty = true;
      lane.valEl.textContent = lane.kind === "enum" ? enumLabel(lane, val) : String(val);
    }
  }
}

function enumLabel(lane, v) {
  const hit = (lane.labels || []).find((p) => p[0] === v);
  return hit ? hit[1] : String(v);
}

function addDigitalLane(name, ch) {
  const isBit = ch.kind === "bits";
  const lane = {
    name, kind: ch.kind, group: isBit ? ch.name : null, labels: ch.labels || null,
    color: colorFor(name, digitalLanes.size), show: true,
    xsHost: [], xsTick: [], vs: [], dirty: true, _sizedirty: false,
  };
  // Packed bit lanes are grouped under their parent byte name (once).
  if (isBit && ch.name && !document.getElementById("dgrp-" + ch.name)) {
    const grp = document.createElement("div");
    grp.className = "dgroup"; grp.id = "dgrp-" + ch.name;
    grp.textContent = ch.name + " (packed)";
    $("digitalLanes").appendChild(grp);
  }
  const row = document.createElement("div");
  row.className = "dlane";
  row.innerHTML = `<div class="gut"><span class="sw"></span><span class="nm${lane.group ? " sub" : ""}"></span><span class="val"></span></div>`;
  const cv = document.createElement("canvas");
  row.appendChild(cv);
  $("digitalLanes").appendChild(row);
  row.querySelector(".sw").style.background = lane.color;
  row.querySelector(".nm").textContent = name;
  lane.canvas = cv;
  lane.rowEl = row;
  lane.valEl = row.querySelector(".val");
  lane.swEl = row.querySelector(".sw");
  lane.nameEl = row.querySelector(".nm");
  digitalLanes.set(name, lane);
  wireLaneColor(lane);
  updateDigitalCount();
  return lane;
}

// -- per-lane controls: click the NAME to enable/disable the lane, the SWATCH to recolour.
// Colour is persisted in the shared store; mirrors the analog charts' name/swatch split.

// Make a span behave like a button for the keyboard: focusable, announced as a button, and
// activated by Enter/Space (in addition to its mouse click handler).
function makeSpanButton(el, label, onActivate) {
  el.setAttribute("role", "button");
  el.tabIndex = 0;
  el.setAttribute("aria-label", label);
  el.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onActivate(e); }
  };
}

function wireLaneColor(lane) {
  lane.swEl.title = "Click to set colour";
  const pickColor = (e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    const inp = document.createElement("input");
    inp.type = "color";
    inp.value = rgbToHex(lane.color);
    inp.oninput = () => {
      lane.color = inp.value;
      lane.swEl.style.background = inp.value;
      saveColor(lane.name, inp.value);
      lane.dirty = true;
      redrawDigital();
    };
    inp.click();
  };
  lane.swEl.onclick = pickColor;
  makeSpanButton(lane.swEl, `Set colour for ${lane.name}`, pickColor);

  lane.nameEl.title = "Click to show / hide this lane";
  const toggleLane = () => {
    lane.show = !lane.show;
    lane.rowEl.classList.toggle("off", !lane.show);
    lane.nameEl.setAttribute("aria-pressed", lane.show ? "true" : "false");
    lane.dirty = true;
    redrawDigital();
  };
  lane.nameEl.onclick = toggleLane;
  makeSpanButton(lane.nameEl, `Toggle lane ${lane.name}`, toggleLane);
  lane.nameEl.setAttribute("aria-pressed", lane.show ? "true" : "false");
}

function showDigital() { $("digitalHead").hidden = false; $("digitalWrap").hidden = digitalCollapsed; }
function updateDigitalCount() {
  const n = digitalLanes.size;
  let text = n ? `${n} lane${n === 1 ? "" : "s"}` : "";
  if (laneCapWarned) text += ` (limit ${MAX_LANES} reached)`;
  $("digitalCount").textContent = text;
}

// The digital panel has its OWN window (independent of the analog charts, like each chart).
function currentWindowSec() { return digitalWindow; }


// Digital panel header, mirroring the analog .plot-head: collapse / title / count / paused tag /
// window buttons / pause-resume / csv. Built once at boot into the (initially hidden) #digitalHead.
function buildDigitalHead() {
  const head = $("digitalHead");
  head.textContent = "";
  head.className = "plot-head";

  const collapse = document.createElement("button");
  collapse.className = "iconbtn plot-collapse";
  collapse.textContent = digitalCollapsed ? "▸" : "▾";   // right / down triangle
  collapse.title = "Hide / show the digital lanes";
  collapse.addEventListener("click", () => {
    digitalCollapsed = !digitalCollapsed;
    $("digitalWrap").hidden = digitalCollapsed || digitalLanes.size === 0;
    collapse.textContent = digitalCollapsed ? "▸" : "▾";
    if (!digitalCollapsed) markDigitalDirty();
  });

  const title = document.createElement("span");
  title.className = "ptitle"; title.textContent = "Digital / Enum";

  const count = document.createElement("span");
  count.className = "count"; count.id = "digitalCount";

  const ptag = document.createElement("span");
  ptag.className = "paused-tag"; ptag.textContent = "paused"; ptag.hidden = !digitalPaused;
  digitalPausedTag = ptag;

  const spacer = document.createElement("div"); spacer.className = "spacer";

  const win = buildWindowButtons(digitalWindow, (secs) => { digitalWindow = secs; markDigitalDirty(); });

  const pause = document.createElement("button");
  pause.className = "iconbtn"; pause.textContent = digitalPaused ? "resume" : "pause";
  pause.classList.toggle("on", digitalPaused);
  pause.addEventListener("click", () => setDigitalPaused(!digitalPaused));
  digitalPauseBtn = pause;

  const exp = document.createElement("button");
  exp.className = "iconbtn"; exp.textContent = "csv";
  exp.title = "Export the shown lanes over the current window as CSV";
  exp.addEventListener("click", exportDigital);

  head.append(collapse, title, count, ptag, spacer, win, pause, exp);
}


// Export the shown digital lanes over the current window. Digital channels can span several
// streams, so only the long format is valid (wide assumes one shared x column).
function exportDigital() {
  const names = [...new Set([...digitalLanes.values()].filter((l) => l.show).map((l) => l.name))];
  downloadCsv(names, digitalWindow * 1000, "long", "digital.csv");
}

// Repaint dirty lanes on the shared PLOT_REDRAW_MS timer. A backing-store size mismatch
// (any width change: window/sidebar drag, popout, view switch) forces a redraw too, so the
// lanes track resizes without wiring every resize path. Returns whether any lane repainted,
// so the caller can skip re-projecting the shared cursor on idle ticks.
function redrawDigital() {
  if (!digitalLanes.size) return false;
  let drew = false;
  const winSec = currentWindowSec();
  // One shared right edge for every lane (frozen on pause, else the newest sample across all
  // lanes). Transition reduction means a quiet lane's own last vertex is stale, so anchoring
  // each lane to its own last sample would render siblings at different scales and disagree
  // with #dCursor - the shared edge keeps lanes + cursor + pause-freeze on one time base.
  const xmax = digitalRightEdge();
  const dpr = window.devicePixelRatio || 1;
  for (const lane of digitalLanes.values()) {
    const cw = lane.canvas.clientWidth;
    if (cw <= 0) continue;   // panel hidden; leave the lane dirty for when it is shown
    const sizeChanged = lane.canvas.width !== Math.round(cw * dpr);
    if (!lane.dirty && !lane._sizedirty && !sizeChanged) continue;
    drawDigitalLane(lane, winSec, xmax);
    lane.dirty = false;
    drew = true;
  }
  return drew;
}

function drawDigitalLane(lane, winSec, xmax) {
  const cv = lane.canvas, dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = DLANE_H;
  if (w <= 0) return;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (!lane.show) return;   // disabled via the name click: leave the lane cleared
  const xs = state.timeMode === "tick" ? lane.xsTick : lane.xsHost;   // rel shares the host array
  if (!xs.length) return;
  const span = (state.timeMode === "tick" ? winSec * 1000 : winSec) || 1;   // tick is in ms
  // Shared edge (already in this state.timeMode's units); fall back to this lane's last vertex only
  // if no edge is available (should not happen once any lane has samples).
  const edge = xmax != null ? xmax : xs[xs.length - 1];
  const xmin = edge - span;
  const X = (t) => ((t - xmin) / span) * w;
  if (lane.kind === "bits") drawBits(g, lane, xs, X, w, h, xmin, edge);
  else drawEnum(g, lane, xs, X, w, h, xmin, edge);
}

// The index range of vertices that touch the visible window [xmin, xmax]: `lo` is the last
// vertex at or before xmin (the level shown at the left edge), `hi` the first at or after
// xmax. Drawing only [lo, hi] is pixel-identical to walking all vertices and clipping, but
// O(visible) instead of O(history) - a fast-toggling lane over a wide buffer stays cheap.
function visibleRange(xs, xmin, xmax) {
  const n = xs.length;
  let lo = 0, a = 0, b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] <= xmin) { lo = m; a = m + 1; } else b = m - 1; }
  let hi = n - 1; a = 0; b = n - 1;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] >= xmax) { hi = m; b = m - 1; } else a = m + 1; }
  return [lo, hi];
}

// bits: a square wave. Each stored vertex is a value change; the level vs[i] holds from its
// sample to the next (or the right edge). The first level is extended to the left edge so a
// held signal reads across the whole lane. A faint fill sits under the high level.
function drawBits(g, lane, xs, X, w, h, xmin, xmax) {
  const yHi = 8, yLo = h - 8, n = xs.length;
  const y = (v) => (v ? yHi : yLo);
  const [lo, hi] = visibleRange(xs, xmin, xmax);   // only the on-screen vertices
  g.fillStyle = lane.color + "22";
  for (let i = lo; i <= hi; i++) {
    if (!lane.vs[i]) continue;
    const x0 = Math.max(0, i === 0 ? 0 : X(xs[i]));
    const x1 = Math.min(w, i + 1 < n ? X(xs[i + 1]) : w);
    if (x1 > x0) g.fillRect(x0, yHi, x1 - x0, yLo - yHi);
  }
  g.strokeStyle = lane.color; g.lineWidth = 1.6;
  g.beginPath();
  g.moveTo(0, y(lane.vs[lo]));                            // level active at the left edge
  for (let i = lo; i <= hi; i++) {
    const xEnd = i + 1 < n ? X(xs[i + 1]) : w;
    g.lineTo(xEnd, y(lane.vs[i]));                        // hold this level
    if (i + 1 < n) g.lineTo(xEnd, y(lane.vs[i + 1]));     // vertical edge to the next level
  }
  g.stroke();
}

// enum: a monochrome FPGA bus envelope (top/bottom rails joined by X-crossings at each
// transition), a whisper of fill, and the label centred and hard-clipped to the segment so
// it never spills past its crossings (a very narrow segment shows no text).
function drawEnum(g, lane, xs, X, w, h, xmin, xmax) {
  const yT = 6, yB = h - 6, ym = (yT + yB) / 2, xo = 5, n = xs.length;
  g.font = "10px ui-monospace, monospace";
  g.textBaseline = "middle"; g.textAlign = "center";
  const [lo, hi] = visibleRange(xs, xmin, xmax);   // only the on-screen segments
  for (let i = lo; i <= hi; i++) {
    const x0 = Math.max(0, i === 0 ? 0 : X(xs[i]));
    const x1 = Math.min(w, i + 1 < n ? X(xs[i + 1]) : w);
    if (x1 <= 0 || x0 >= w || x1 <= x0) continue;
    const inW = Math.max(0, x1 - x0 - 2 * xo);   // width between the two crossings
    g.fillStyle = lane.color + "14";
    if (inW > 0) g.fillRect(x0 + xo, yT, inW, yB - yT);
    g.strokeStyle = lane.color; g.lineWidth = 1.4;
    g.beginPath();
    g.moveTo(x0 + xo, yT); g.lineTo(x1 - xo, yT);   // top rail
    g.moveTo(x0 + xo, yB); g.lineTo(x1 - xo, yB);   // bottom rail
    g.moveTo(x0, ym); g.lineTo(x0 + xo, yT);        // opening crossing (upper)
    g.moveTo(x0, ym); g.lineTo(x0 + xo, yB);        // opening crossing (lower)
    g.moveTo(x1 - xo, yT); g.lineTo(x1, ym);        // closing crossing (upper)
    g.moveTo(x1 - xo, yB); g.lineTo(x1, ym);        // closing crossing (lower)
    g.stroke();
    if (inW > 6) {
      g.save();
      g.beginPath(); g.rect(x0 + xo, yT, inW, yB - yT); g.clip();
      g.fillStyle = lane.color;
      g.fillText(enumLabel(lane, lane.vs[i]), (x0 + x1) / 2, ym);
      g.restore();
    }
  }
}

// Force a repaint of every lane (time-mode change, resize) even when no new samples arrived.
function markDigitalDirty() {
  for (const l of digitalLanes.values()) l._sizedirty = true;
  redrawDigital();
  for (const l of digitalLanes.values()) l._sizedirty = false;
}

// ---- shared cursor: join the analog "plots" sync group, both directions -------------
//
// analog -> digital: a passive client subscribed to uPlot's "plots" sync group receives
// each publishing chart's cursor as a pixel; we map it back to a time value on the source
// chart (posToVal) and drive #dCursor + the per-lane readouts. digital -> analog: a
// mousemove over the panel maps a pixel to a time and drives every analog chart's cursor
// (via applyHoverCursor, so the 200 ms redraw loop keeps it pinned while the pointer rests).
function initDigitalCursorSync() {
  const sync = uPlot.sync("plots");
  sync.sub({
    pub(type, self, x) {
      if (type === "mouseleave") { chartHoverX = null; $("dCursor").hidden = true; return; }
      if (type !== "mousemove") return;
      if (x == null || x < 0 || !self || typeof self.posToVal !== "function") { $("dCursor").hidden = true; return; }
      const tval = self.posToVal(x, "x");
      if (!Number.isFinite(tval)) return;
      chartHoverX = tval;   // remember the time so applyHoverCursor re-pins it while the pointer rests
      setDigitalCursorAt(tval);
    },
  });
  const wrap = $("digitalWrap");
  wrap.addEventListener("mousemove", onDigitalHover);
  wrap.addEventListener("mouseleave", onDigitalLeave);
}

// Right edge shared by every lane's window (frozen on pause, else the newest sample seen).
function digitalRightEdge() {
  if (digitalPaused && digitalFrozen) return state.timeMode === "tick" ? digitalFrozen.tick : digitalFrozen.host;
  let xmax = -Infinity;
  for (const l of digitalLanes.values()) {
    const xs = state.timeMode === "tick" ? l.xsTick : l.xsHost;
    if (xs.length) xmax = Math.max(xmax, xs[xs.length - 1]);
  }
  return Number.isFinite(xmax) ? xmax : null;
}

// The held value of a lane at time t: the last stored vertex at or before t (levels hold
// forward). Returns "" before the first sample. Enum values map through the label table.
function valueAt(lane, t) {
  const xs = state.timeMode === "tick" ? lane.xsTick : lane.xsHost;   // same array selection as nearestX
  const n = xs.length;
  if (!n || t < xs[0]) return "";
  // Binary-search the held level: the largest index i with xs[i] <= t (levels hold forward).
  let lo = 0, hi = n - 1, idx = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1;
  }
  const v = lane.vs[idx];
  if (v == null) return "";
  return lane.kind === "enum" ? enumLabel(lane, v) : String(v);
}

// Place #dCursor at the time `tval`, snapped to the nearest transition across all lanes, and
// refresh each lane's readout to the held value there. The overlay left accounts for the gutter
// (waveform area = canvas, offset by the fixed name/value gutter). Returns the snapped time.
function setDigitalCursorAt(tval) {
  const cur = $("dCursor");
  if (!digitalLanes.size) { cur.hidden = true; return null; }
  const winSec = currentWindowSec();
  const span = (state.timeMode === "tick" ? winSec * 1000 : winSec) || 1;
  const xmax = digitalRightEdge();
  if (xmax === null) { cur.hidden = true; return null; }
  const xmin = xmax - span;
  // Snap to the nearest transition across every lane (edges are dense enough on live bits).
  let snapped = tval, best = Infinity, ref = null;
  for (const l of digitalLanes.values()) {
    const xs = state.timeMode === "tick" ? l.xsTick : l.xsHost;
    const c = nearestX(xs, tval);
    if (c != null) { const d = Math.abs(c - tval); if (d < best) { best = d; snapped = c; } }
    if (!ref && l.canvas && l.canvas.clientWidth > 0) ref = l;   // first visible lane, no spread/find
  }
  for (const l of digitalLanes.values()) l.valEl.textContent = valueAt(l, snapped);
  if (!ref) { cur.hidden = true; return snapped; }
  const cw = ref.canvas.clientWidth;
  const gut = $("digitalWrap").clientWidth - cw;   // fixed name/value gutter width
  const px = gut + ((snapped - xmin) / span) * cw;
  if (px < gut - 0.5 || px > gut + cw + 0.5) { cur.hidden = true; }
  else { cur.style.left = px + "px"; cur.hidden = false; }
  return snapped;
}

// Pointer over the digital panel -> map its x (relative to the waveform/canvas) to a time,
// draw the digital cursor there, and drive the analog charts to the same time.
function onDigitalHover(e) {
  const ref = [...digitalLanes.values()].find((l) => l.canvas && l.canvas.clientWidth > 0);
  if (!ref) return;
  const rect = ref.canvas.getBoundingClientRect();
  const px = e.clientX - rect.left;
  if (px < 0 || px > rect.width || rect.width <= 0) { onDigitalLeave(); return; }   // over the gutter
  const winSec = currentWindowSec();
  const span = (state.timeMode === "tick" ? winSec * 1000 : winSec) || 1;
  const xmax = digitalRightEdge();
  if (xmax === null) return;
  const tval = (xmax - span) + (px / rect.width) * span;
  digitalCursorX = tval;
  setDigitalCursorAt(tval);
  hooks.reapplyCursor();   // project onto the analog charts (respects a terminal hover if present)
}

function onDigitalLeave() {
  digitalCursorX = null;
  $("dCursor").hidden = true;
  hooks.reapplyCursor();   // clears the analog cursor unless a terminal row is still hovered
}

// Freeze/thaw the panel with the analog charts. On pause the right edge is pinned at the
// newest sample so the window stops advancing; samples keep buffering for the resume catch-up.
function setDigitalPaused(paused) {
  if (digitalPaused === paused) return;
  digitalPaused = paused;
  if (paused) {
    let mh = -Infinity, mt = -Infinity;
    for (const l of digitalLanes.values()) {
      const n = l.xsHost.length;
      if (n) { mh = Math.max(mh, l.xsHost[n - 1]); mt = Math.max(mt, l.xsTick[n - 1]); }
    }
    digitalFrozen = Number.isFinite(mh) ? { host: mh, tick: mt } : null;
  } else {
    digitalFrozen = null;
  }
  if (digitalPauseBtn) {
    digitalPauseBtn.textContent = paused ? "resume" : "pause";
    digitalPauseBtn.classList.toggle("on", paused);
  }
  if (digitalPausedTag) digitalPausedTag.hidden = !paused;
  for (const l of digitalLanes.values()) l.dirty = true;
  redrawDigital();
  hooks.liveChanged();   // recompute the pause-all button text (mirrors setChartPaused)
}

// Small accessors + reset used by plots.js / terminal.js (avoid reaching into module lets).
export function getDigitalCursorX() { return digitalCursorX; }
export function getChartHoverX() { return chartHoverX; }
export function isDigitalPaused() { return digitalPaused; }

// Restore each lane's gutter readout to its held value at the live (or frozen) right edge.
// While a cursor is active setDigitalCursorAt writes the value-at-cursor into every readout;
// without this, a quiet lane would keep showing that scrubbed value after the pointer leaves,
// where the analog legend snaps back to the latest value on mouseleave.
function refreshDigitalReadouts() {
  const edge = digitalRightEdge();
  if (edge == null) return;
  for (const l of digitalLanes.values()) l.valEl.textContent = valueAt(l, edge);
}

// Reset the digital panel to first-load state (see terminal.js clear-all).
export function clearAllDigital() {
    setDigitalPaused(false);
    digitalLanes.clear();
    $("digitalLanes").textContent = "";
    digitalFrozen = null;
    digitalCursorX = null;
    laneCapWarned = false;
    $("dCursor").hidden = true;
    $("digitalWrap").hidden = true;
    $("digitalHead").hidden = true;
    updateDigitalCount();
}

export { digitalIngest, digitalLanes, setDigitalPaused, markDigitalDirty, redrawDigital,
         setDigitalCursorAt, refreshDigitalReadouts, buildDigitalHead, initDigitalCursorSync };
