import { $, root, state, hooks, nearestX, portColor, PLOT_CAP, PLOT_SLACK } from "./state.js";
import { openExportDialog } from "./exportdlg.js";
import { buildWindowButtons, colorFor, exitZoom, leaveZoom, openColorPicker, rgbToHex, saveColor,
         soloShow, PLOT_WINDOW_DEFAULT } from "./chrome.js";
import { AXIS_PX_PER_TICK, axisTicks, fmtAxisTick, getZoom, laneSegments, fmtTime, windowFor,
         TIME_AXIS_LABELS } from "./timewindow.js";
import { freezeChanged, registerSurface } from "./freeze.js";

// ---- digital / enum panel: canvas lanes below the analog charts ---------------------
//
// Enum and packed-bits channels do not belong on an auto-ranged y axis; they render as
// aligned logic-analyser lanes. Each lane keeps a transition-reduced ring (one vertex per
// value change, so a level held constant is a single segment), drawn to its own <canvas>
// at devicePixelRatio. bits draw as square waves with a faint high-fill; enums draw as an
// FPGA-style monochrome bus envelope with X-crossings and a right-clipped centred label.
// The panel shares the analog time base (host/tick/rel), window, and global pause.

const DLANE_H = 34;                 // must match .dlane { height } in style.css
const RULER_H = 18;                 // must match .druler { height } in style.css
const MAX_LANES = 64;               // cap on distinct digital lanes, so a device emitting rotating
                                     // enum/bits names cannot grow the DOM/heap forever
let laneCapWarned = false;
const digitalLanes = new Map();     // "<port>|<name>" -> lane {key, port, name, kind, group, labels, color, xs..., canvas}
const laneGroups = new Map();       // "<port>|<group>" -> the packed group's header element
let lanePortTags = false;           // gutters name the port once more than one has contributed
let lanesChanged = () => {};        // plots.js: the Plots section's empty state, hint and port tags
let digitalPaused = false;          // global freeze (mirrors the analog charts)
let digitalLast = null;             // {host, tick} newest sample seen, transition or not: the
                                     // live right edge. A lane's last vertex is NOT it - a held
                                     // level stores no vertex, so a constant signal would freeze
let digitalFrozen = null;           // {host, tick} right-edge captured at pause; each lane also
                                     // snapshots its vertices then (lane.frozen, see anchorDigitalFreeze)
let digitalFrozenId = null;         // line-id watermark at pause, for the export's id_to
let digitalCursorX = null;          // time value the digital panel is currently driving the analog cursor to
let chartHoverX = null;             // time under the pointer while it rests over an analog chart
let cursorReadout = false;          // gutter readouts show the value at the cursor, not the live edge
let digitalWindow = PLOT_WINDOW_DEFAULT;   // seconds shown; the panel has its OWN window (like each chart)
let digitalCollapsed = false;       // lanes hidden via the header collapse button
let digitalPauseBtn = null;         // header pause/resume button (built in buildDigitalHead)
let digitalPausedTag = null;        // header "paused" tag
let digitalExportBtn = null;        // header export button (disabled while no lane is shown)

// Lane names, like channel names, are unique only within a port (SPEC 9.2).
function laneKey(port, name) { return port + "|" + name; }
function onLanesChanged(fn) { lanesChanged = fn; }

function digitalIngest(port, points, x) {
  // The same class-6 gate addSample has, at this producer's own boundary: one non-finite x
  // is permanent here, because the monotonic bump below is `hx <= xsHost[n-1]` and
  // `hx <= NaN` is false, so no later sample is ever bumped again. valueAt/nearestX then
  // binary-search a non-monotonic array and anchorDigitalFreeze takes a max over it.
  if (!Number.isFinite(x.host) || !Number.isFinite(x.tick)) return;
  showDigital();
  if (digitalLast === null) digitalLast = { host: x.host, tick: x.tick };
  else {   // per field: the history seed and the live stream can interleave out of order
    if (x.host > digitalLast.host) digitalLast.host = x.host;
    if (x.tick > digitalLast.tick) digitalLast.tick = x.tick;
  }
  for (const [name, val, ch] of points) {
    let lane = digitalLanes.get(laneKey(port, name));
    if (!lane) {
      if (digitalLanes.size >= MAX_LANES) {
        if (!laneCapWarned) {
          laneCapWarned = true;
          console.warn(`digital: lane cap (${MAX_LANES}) reached, ignoring new lane "${name}"`);
          updateDigitalCount();
        }
        continue;
      }
      lane = addDigitalLane(port, name, ch);
    }
    const n = lane.xsHost.length;
    // Transition reduction: store a vertex only when the value changes (plus the first sample).
    // vs[i] is held from its stored time xs[i] until the next vertex xs[i+1], and the draw
    // functions extend the newest segment to the right edge - so a repeat value adds
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
      // The gutter readout is written by redrawDigital (5 Hz, hidden lanes skipped), not per
      // decoded point: this path runs per sample and the text usually does not change at all.
      lane.pendingVal = val;
    }
  }
  // Paused with no frozen edge (paused before any digital data, or clear-all took the edge):
  // digitalRightEdge() would fall through to the newest sample and advance while "paused".
  if (digitalPaused && digitalFrozen === null) anchorDigitalFreeze();
}

// Pin the frozen window to the newest sample across every lane. Null while no lane holds one.
function anchorDigitalFreeze() {
  digitalFrozen = digitalLast === null ? null : { ...digitalLast };
  // The rings keep filling while paused (deliberately, for the resume catch-up), so the time
  // pin alone is not enough: a fast-toggling lane's ring rotates fully past the frozen edge,
  // and any paused redraw re-derived from it draws post-freeze data flat across the frozen
  // window (REVIEW class 26). Snapshot what the freeze covers; laneDrawData serves it while
  // paused, resume drops it. Bounded: each snapshot is the ring's content at pause, no more.
  for (const l of digitalLanes.values()) {
    l.frozen = { xsHost: l.xsHost.slice(), xsTick: l.xsTick.slice(), vs: l.vs.slice() };
  }
  // The drawn freeze is a time, but the export needs an id (see exportDigital); rows arrive
  // in id order, so state.maxId is exact here. Same shape as terminal.js's pane.frozenId.
  digitalFrozenId = state.maxId;
}

// The vertex arrays a redraw (and the cursor/readout paths) must consume: the pause-time
// snapshot while frozen, the live rings otherwise. The one seam every consumer goes through,
// so a paused view can never be re-derived from a ring that rotated past the freeze.
function laneDrawData(lane) {
  const src = digitalPaused && lane.frozen ? lane.frozen : lane;
  return { xs: state.timeMode === "tick" ? src.xsTick : src.xsHost, vs: src.vs };   // rel shares host
}

// Single writer for a lane's gutter readout, so an unchanged value costs no DOM write
// (mousemove drives this at pointer rate).
function setLaneVal(lane, text) {
  if (lane.valText === text) return;
  lane.valText = text;
  lane.valEl.textContent = text;
}

function enumLabel(lane, v) {
  const hit = (lane.labels || []).find((p) => p[0] === v);
  return hit ? hit[1] : String(v);
}

// Per-kind behaviour of a lane: readout text and waveform drawing. The one place the
// kinds diverge in this module, so a new lane kind is one entry here (plus its routing
// in plots.js routePoints).
const LANE_KINDS = {
  bits: { fmt: (lane, v) => String(v), draw: drawBits },
  enum: { fmt: enumLabel, draw: drawEnum },
};

function addDigitalLane(port, name, ch) {
  const isBit = ch.kind === "bits";
  const lane = {
    key: laneKey(port, name), port, name, kind: ch.kind, group: isBit ? ch.name : null,
    labels: ch.labels || null, color: colorFor(name), show: true,
    xsHost: [], xsTick: [], vs: [], frozen: null, dirty: true, _sizedirty: false,
  };
  // A lane born after the freeze holds nothing the freeze covers: an empty snapshot keeps it
  // blank while paused, instead of leaking its (all post-freeze) ring into the frozen view.
  if (digitalPaused && digitalFrozen) lane.frozen = { xsHost: [], xsTick: [], vs: [] };
  // Packed bit lanes are grouped under their parent byte name (once per port).
  const gk = isBit && ch.name ? laneKey(port, ch.name) : null;
  if (gk && !laneGroups.has(gk)) {
    const grp = document.createElement("div");
    grp.className = "dgroup";
    laneGroups.set(gk, { el: grp, port, name: ch.name });
    paintGroup(laneGroups.get(gk));
    $("digitalLanes").appendChild(grp);
  }
  const row = document.createElement("div");
  row.className = "dlane";
  const gut = document.createElement("div"); gut.className = "gut";
  const sw = document.createElement("span"); sw.className = "sw";
  const pt = document.createElement("span"); pt.className = "pt";
  const nm = document.createElement("span"); nm.className = "nm" + (lane.group ? " sub" : "");
  const val = document.createElement("span"); val.className = "val";
  gut.append(sw, pt, nm, val);
  const cv = document.createElement("canvas");
  row.append(gut, cv);
  $("digitalLanes").appendChild(row);
  sw.style.background = lane.color;
  nm.textContent = name;
  pt.textContent = port;
  pt.title = "Port " + port;
  pt.style.color = portColor(port);
  pt.hidden = !lanePortTags;
  lane.canvas = cv;
  lane.rowEl = row;
  lane.valEl = val;
  lane.swEl = sw;
  lane.nameEl = nm;
  lane.portEl = pt;
  digitalLanes.set(lane.key, lane);
  wireLaneColor(lane);
  updateDigitalCount();
  syncDigitalExportBtn();
  lanesChanged();
  return lane;
}

function paintGroup(g) {
  g.el.textContent = g.name + " (packed)" + (lanePortTags ? " on " + g.port : "");
}

// Name the port on every gutter and group header, or stop naming it (plots.js decides).
function setLanePortTags(on) {
  lanePortTags = on;
  for (const l of digitalLanes.values()) l.portEl.hidden = !on;
  for (const g of laneGroups.values()) paintGroup(g);
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

// One writer for a lane's shown state, so the solo path and the plain toggle cannot drift.
function applyLaneShow(lane, on) {
  lane.show = on;
  lane.rowEl.classList.toggle("off", !on);
  lane.nameEl.setAttribute("aria-pressed", on ? "true" : "false");
  lane.dirty = true;
}

function wireLaneColor(lane) {
  lane.swEl.title = "Click to set colour";
  const pickColor = (e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    const apply = (v) => {
      saveColor(lane.name, v);
      // The colour is keyed by name, so another port's lane of that name follows it.
      for (const l of digitalLanes.values()) {
        if (l.name !== lane.name) continue;
        l.color = v;
        l.swEl.style.background = v;
        l.dirty = true;
      }
      redrawDigital();
    };
    openColorPicker(rgbToHex(lane.color), apply, apply);
  };
  lane.swEl.onclick = pickColor;
  makeSpanButton(lane.swEl, `Set colour for ${lane.name}`, pickColor);

  lane.nameEl.title = "Click to show / hide this lane, alt-click to show only it";
  const toggleLane = (e) => {
    // Alt-click (Shift+Enter from makeSpanButton) solos, as the analog legend does: 64 lanes
    // is 63 clicks to isolate one otherwise.
    if (e && (e.altKey || e.shiftKey)) {
      const names = [...digitalLanes.keys()];
      const show = soloShow(names, new Map(names.map((n) => [n, digitalLanes.get(n).show])), lane.key);
      for (const [n, on] of show) applyLaneShow(digitalLanes.get(n), on);
    } else {
      applyLaneShow(lane, !lane.show);
    }
    syncDigitalExportBtn();
    redrawDigital();
  };
  lane.nameEl.onclick = toggleLane;
  syncDigitalExportBtn();
  makeSpanButton(lane.nameEl, `Toggle lane ${lane.name}`, toggleLane);
  lane.nameEl.setAttribute("aria-pressed", lane.show ? "true" : "false");
}

// Runs per ingested sample, so the DOM work happens once and the flag carries the rest.
// clearAllDigital re-hides the panel and clears it.
let digitalShown = false;
function showDigital() {
  if (digitalShown) return;
  digitalShown = true;
  $("digitalHead").hidden = false;
  $("digitalWrap").hidden = digitalCollapsed;
}
function updateDigitalCount() {
  const n = digitalLanes.size;
  let text = n ? `${n} lane${n === 1 ? "" : "s"}` : "";
  if (laneCapWarned) text += ` (limit ${MAX_LANES} reached)`;
  $("digitalCount").textContent = text;
}

// The digital panel has its OWN window (independent of the analog charts, like each chart).
function currentWindowSec() { return digitalWindow; }

// The window a lane draws and both cursor projections use: the shared drag zoom (plots.js
// onSelect, which freezes every panel) while the panel is frozen on it, else the tail
// window. Drawing and cursor must take the same one or the cursor lands off the waveform.
function laneWindow(winSec, edge, w) {
  return windowFor(digitalPaused ? getZoom() : null, state.timeMode, winSec, edge, w);
}


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
  exp.className = "iconbtn exportbtn"; exp.textContent = "export";
  exp.addEventListener("click", exportDigital);
  digitalExportBtn = exp;
  syncDigitalExportBtn();

  const ctl = document.createElement("div"); ctl.className = "plot-ctl";
  ctl.append(win, pause, exp);
  head.append(collapse, title, count, ptag, spacer, ctl);
}

// No lane shown, nothing to export: say so on the button rather than letting the click do
// nothing at all (REVIEW class 12; the digital side had no pin at all, mutation M27).
function syncDigitalExportBtn() {
  if (!digitalExportBtn) return;
  const n = [...digitalLanes.values()].filter((l) => l.show).length;
  digitalExportBtn.disabled = n === 0;
  digitalExportBtn.title = n ? "Export the shown lanes over a chosen range"
                             : "No lanes are shown: enable one to export it";
}


// Export the shown digital lanes. Digital channels can span several streams, so only the long
// format is valid (wide assumes one shared x column).
// While paused the window is anchored at the pause watermark, not at now.
// /plot/export scopes to one port (names are unique only within one), so lanes shown from
// several ports offer a Port choice and export that port's shown lanes.
function exportDigital() {
  const shown = [...digitalLanes.values()].filter((l) => l.show);
  if (!shown.length) return;
  const ports = [...new Set(shown.map((l) => l.port))];
  const namesOf = (port) => [...new Set(shown.filter((l) => l.port === port).map((l) => l.name))];
  const portOpt = ports.length > 1
    ? [{ name: "port", type: "select", label: "Port", choices: ports, value: ports[0] }] : [];
  openExportDialog({
    kind: "plot",
    watermark: digitalPaused ? digitalFrozenId : null,
    shownLastMs: digitalWindow * 1000,
    options: [
      ...portOpt,
      { name: "format", type: "select", label: "Format", choices: ["long"], value: "long" },
      { name: "decode", type: "check", label: "decode values (enum labels, bit lanes)", value: true },
      { name: "changes", type: "check", label: "changes only", value: false,
        enabledBy: "decode" },
      { name: "deadband", type: "text", label: "Deadband", value: "",
        placeholder: "channel=0.5,other=2", enabledBy: "changes" },
    ],
    build: (p, v) => {
      const port = ports.length > 1 && ports.includes(v.port) ? v.port : ports[0];
      p.set("names", namesOf(port).join(","));
      if (port !== "-") p.set("port", port);
      p.set("format", "long");
      if (v.decode) p.set("decode", "1");
      if (v.changes) {
        p.set("changes", "1");
        p.set("decode", "1");   // changes=1 without decode=1 is a 400, not an export (SPEC 9.2)
        if (v.deadband.trim()) p.set("deadband", v.deadband.trim());
      }
      return "/plot/export?" + p.toString();
    },
  });
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
  // Every clientWidth read before the first canvas write: interleaving the two forces one
  // synchronous layout per lane.
  const lanes = [...digitalLanes.values()].map((lane) => [lane, lane.canvas.clientWidth]);
  const ruler = $("dRuler");
  const rulerW = ruler.clientWidth;
  // The time axis every lane shares, computed once: the gridlines and the ruler take the same
  // ticks from the same projection the waveforms use. Colours are read only when drawing.
  let axis = null;
  const axisFor = (w) => {
    if (!axis) {
      const win = laneWindow(winSec, xmax, w);
      const cs = getComputedStyle(root);
      axis = { win, ...axisTicks(state, win, Math.max(2, Math.floor(w / AXIS_PX_PER_TICK))),
               grid: cs.getPropertyValue("--border").trim() || "#333",
               label: cs.getPropertyValue("--text-faint").trim() || "#889" };
    }
    return axis;
  };
  for (const [lane, cw] of lanes) {
    if (cw <= 0) continue;   // panel hidden; leave the lane dirty for when it is shown
    const sizeChanged = lane.canvas.width !== Math.round(cw * dpr);
    const repaint = lane.dirty || lane._sizedirty || sizeChanged;
    // The live value must not overwrite the value under the cursor. This write ran above the
    // dirty check, so every idle tick clobbered a cursor readout with the live edge - and
    // redrawTick re-applies the cursor only when something moved, so the wrong number stayed
    // on screen beside a cursor line drawn at another time. A repaint is followed by
    // applyHoverCursor, so writing there is safe; an idle tick is not.
    if (lane.pendingVal !== undefined && (repaint || !cursorReadout)) {
      setLaneVal(lane, LANE_KINDS[lane.kind].fmt(lane, lane.pendingVal));
    }
    if (!repaint) continue;
    drawDigitalLane(lane, winSec, xmax, cw, xmax === null ? null : axisFor(cw));
    lane.dirty = false;
    // Cleared here, not by the caller: redrawDigital skips a lane with no width, and
    // markDigitalDirty used to clear the flag for those lanes too, so a time-base change made
    // while the panel was hidden was lost and the lane stayed drawn in the old time base.
    lane._sizedirty = false;
    drew = true;
  }
  const rulerStale = rulerW > 0 && ruler.width !== Math.round(rulerW * dpr);
  if (xmax !== null && rulerW > 0 && (drew || rulerStale)) drawRuler(ruler, axisFor(rulerW), rulerW);
  return drew;
}

// The lanes' own time axis, a ruler row under them: without it a digital-only stream had no
// time reference at all, and a pulse width could not be read off.
function drawRuler(cv, axis, w) {
  const label = $("dRulerLabel");
  const text = TIME_AXIS_LABELS[state.timeMode] || "";
  if (label.textContent !== text) label.textContent = text;
  if (!cv.getContext) return;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(RULER_H * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(RULER_H * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, RULER_H);
  g.font = "10px ui-monospace, monospace";
  g.textBaseline = "top";
  g.strokeStyle = axis.grid;
  g.fillStyle = axis.label;
  g.lineWidth = 1;
  for (const t of axis.ticks) {
    const x = Math.round(axis.win.toPx(t)) + 0.5;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 4); g.stroke();
    const text = fmtAxisTick(state, t, axis.step);
    const tw = g.measureText ? g.measureText(text).width || 0 : 0;
    // Centred on its tick, but kept inside the ruler at either end.
    g.textAlign = "left";
    g.fillText(text, Math.max(0, Math.min(w - tw, x - tw / 2)), 5);
  }
}

// `w` comes from the caller's hoisted read (see redrawDigital), never from clientWidth here.
// `axis` carries the shared ticks, drawn as faint gridlines under the waveform.
function drawDigitalLane(lane, winSec, xmax, w, axis) {
  const cv = lane.canvas, dpr = window.devicePixelRatio || 1;
  const h = DLANE_H;
  if (w <= 0) return;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (axis) {
    g.strokeStyle = axis.grid; g.lineWidth = 1;
    g.beginPath();
    for (const t of axis.ticks) { const x = Math.round(axis.win.toPx(t)) + 0.5; g.moveTo(x, 0); g.lineTo(x, h); }
    g.stroke();
  }
  if (!lane.show) return;   // disabled via the name click: leave the lane cleared
  const data = laneDrawData(lane);   // pause-time snapshot while frozen, live ring otherwise
  if (!data.xs.length) return;
  // Shared edge (already in this state.timeMode's units); fall back to this lane's last vertex only
  // if no edge is available (should not happen once any lane has samples).
  const edge = xmax != null ? xmax : data.xs[data.xs.length - 1];
  // The window object carries the whole projection (span/xmin/xmax/width/toPx), so the
  // draw functions take it as one argument instead of its unpacked fields.
  LANE_KINDS[lane.kind].draw(g, lane, data, laneWindow(winSec, edge, w), h);
}

// bits: a square wave. Each stored vertex is a value change; the level vs[i] holds from its
// sample to the next (or the right edge), and starts at the lane's first sample (laneSegments).
// A faint fill sits under the high level.
function drawBits(g, lane, { xs, vs }, win, h) {
  const yHi = 8, yLo = h - 8, n = xs.length;
  const y = (v) => (v ? yHi : yLo);
  const segs = laneSegments(xs, win);   // only the on-screen vertices
  if (!segs.length) return;
  g.fillStyle = lane.color + "22";
  for (const { i, x0, x1 } of segs) if (vs[i]) g.fillRect(x0, yHi, x1 - x0, yLo - yHi);
  g.strokeStyle = lane.color; g.lineWidth = 1.6;
  g.beginPath();
  g.moveTo(segs[0].x0, y(vs[segs[0].i]));                 // level active at the left edge
  for (const { i, x1 } of segs) {
    g.lineTo(x1, y(vs[i]));                               // hold this level
    if (i + 1 < n) g.lineTo(x1, y(vs[i + 1]));            // vertical edge to the next level
  }
  g.stroke();
}

// enum: a monochrome FPGA bus envelope (top/bottom rails joined by X-crossings at each
// transition), a whisper of fill, and the label centred and hard-clipped to the segment so
// it never spills past its crossings (a very narrow segment shows no text).
function drawEnum(g, lane, { xs, vs }, win, h) {
  const yT = 6, yB = h - 6, ym = (yT + yB) / 2, xo = 5;
  g.font = "10px ui-monospace, monospace";
  g.textBaseline = "middle"; g.textAlign = "center";
  for (const { i, x0, x1 } of laneSegments(xs, win)) {   // only the on-screen segments
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
      g.fillText(enumLabel(lane, vs[i]), (x0 + x1) / 2, ym);
      g.restore();
    }
  }
}

// Force a repaint of every lane (time-mode change, resize) even when no new samples arrived.
function markDigitalDirty() {
  for (const l of digitalLanes.values()) l._sizedirty = true;
  redrawDigital();   // clears the flag per lane it actually painted; a hidden lane keeps it
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
      if (type === "mouseleave") { chartHoverX = null; pendingCursorX = null; $("dCursor").hidden = true; return; }
      if (type !== "mousemove") return;
      if (x == null || x < 0 || !self || typeof self.posToVal !== "function") { pendingCursorX = null; $("dCursor").hidden = true; return; }
      const tval = self.posToVal(x, "x");
      if (!Number.isFinite(tval)) return;
      chartHoverX = tval;   // remember the time so applyHoverCursor re-pins it while the pointer rests
      scheduleDigitalCursor(tval);
    },
  });
  const wrap = $("digitalWrap");
  // The pointer being here means it is not on a chart, so the remembered chart hover is
  // over. uPlot does not publish "mouseleave" into the sync group for every exit, so a stale
  // chartHoverX outlives the pointer and hoverXVal() falls back to it.
  // Double-click clears the shared zoom from the lanes too: it is drawn here as much as on
  // the charts, so it must be dismissable here. One exit (chrome.js), so the zoom chips go too.
  wrap.addEventListener("dblclick", () => { if (getZoom()) exitZoom(); });
  wrap.addEventListener("mouseenter", () => { chartHoverX = null; });
  wrap.addEventListener("mousemove", onDigitalHover);
  wrap.addEventListener("mouseleave", onDigitalLeave);
}

// Right edge shared by every lane's window (frozen on pause, else the newest sample seen).
function digitalRightEdge() {
  if (digitalPaused && digitalFrozen) return state.timeMode === "tick" ? digitalFrozen.tick : digitalFrozen.host;
  if (digitalLast === null) return null;
  return state.timeMode === "tick" ? digitalLast.tick : digitalLast.host;
}

// The held value of a lane at time t: the last stored vertex at or before t (levels hold
// forward). Returns "" before the first sample. Enum values map through the label table.
function valueAt(lane, t) {
  const { xs, vs } = laneDrawData(lane);   // frozen snapshot while paused, like the draw path
  const n = xs.length;
  if (!n || t < xs[0]) return "";
  // Binary-search the held level: the largest index i with xs[i] <= t (levels hold forward).
  let lo = 0, hi = n - 1, idx = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1;
  }
  const v = vs[idx];
  if (v == null) return "";
  return LANE_KINDS[lane.kind].fmt(lane, v);
}

// Place #dCursor at the time `tval`, snapped to the nearest transition across all lanes, and
// refresh each lane's readout to the held value there. The overlay left accounts for the gutter
// (waveform area = canvas, offset by the fixed name/value gutter). Returns the snapped time.
function setDigitalCursorAt(tval) {
  const cur = $("dCursor");
  if (!digitalLanes.size) { cur.hidden = true; return null; }
  const winSec = currentWindowSec();
  const xmax = digitalRightEdge();
  if (xmax === null) { cur.hidden = true; return null; }
  // Snap to the nearest transition across every lane (edges are dense enough on live bits).
  let snapped = tval, best = Infinity, ref = null;
  for (const l of digitalLanes.values()) {
    const { xs } = laneDrawData(l);   // snap to drawn transitions, not to post-freeze ones
    const c = nearestX(xs, tval);
    if (c != null) { const d = Math.abs(c - tval); if (d < best) { best = d; snapped = c; } }
    if (!ref && l.canvas && l.canvas.clientWidth > 0) ref = l;   // first visible lane, no spread/find
  }
  for (const l of digitalLanes.values()) setLaneVal(l, valueAt(l, snapped));
  cursorReadout = true;   // the readouts now show the cursor's time, not the live edge
  if (!ref) { cur.hidden = true; return snapped; }
  const cw = ref.canvas.clientWidth;
  const gut = $("digitalWrap").clientWidth - cw;   // fixed name/value gutter width
  const px = gut + laneWindow(winSec, xmax, cw).toPx(snapped);
  if (px < gut - 0.5 || px > gut + cw + 0.5) { cur.hidden = true; }
  else {
    cur.style.left = px + "px";
    cur.dataset.t = fmtTime(state, snapped);        // time tag, drawn by .dcursor::after
    cur.classList.toggle("flip", px > gut + cw / 2);   // tag on the side with room for it
    cur.hidden = false;
  }
  return snapped;
}

// The projection below binary-searches every lane, rewrites every readout and reads layout,
// so a 120 Hz pointer stream is coalesced to one pass per displayed frame (as plots.js does
// for the terminal hit-test). Only the latest position matters.
let cursorRaf = 0;
let pendingCursorX = null;   // time under an analog chart's pointer, null when there is none
let pendingClientX = -1;     // viewport x over the digital panel, -1 when the pointer is elsewhere

function scheduleDigitalCursor(tval) {
  pendingCursorX = tval;
  pendingClientX = -1;
  scheduleCursorFrame();
}

function scheduleCursorFrame() {
  if (cursorRaf) return;
  cursorRaf = requestAnimationFrame(() => {
    cursorRaf = 0;
    if (pendingClientX >= 0) digitalHoverAt(pendingClientX);
    else if (pendingCursorX != null) setDigitalCursorAt(pendingCursorX);
  });
}

// Pointer over the digital panel -> map its x (relative to the waveform/canvas) to a time,
// draw the digital cursor there, and drive the analog charts to the same time.
function onDigitalHover(e) {
  chartHoverX = null;   // mouseenter can be missed (pointer entering over a child); see above
  pendingCursorX = null;
  pendingClientX = e.clientX;
  scheduleCursorFrame();
}

function digitalHoverAt(clientX) {
  const ref = [...digitalLanes.values()].find((l) => l.canvas && l.canvas.clientWidth > 0);
  if (!ref) return;
  const rect = ref.canvas.getBoundingClientRect();
  const px = clientX - rect.left;
  if (px < 0 || px > rect.width || rect.width <= 0) { onDigitalLeave(); return; }   // over the gutter
  const winSec = currentWindowSec();
  const xmax = digitalRightEdge();
  if (xmax === null) return;
  const tval = laneWindow(winSec, xmax, rect.width).fromPx(px);
  digitalCursorX = tval;
  setDigitalCursorAt(tval);
  hooks.reapplyCursor();   // project onto the analog charts (respects a terminal hover if present)
}

function onDigitalLeave() {
  digitalCursorX = null;
  pendingClientX = -1;
  pendingCursorX = null;   // a queued frame must not redraw the cursor the pointer just left
  $("dCursor").hidden = true;
  hooks.reapplyCursor();   // clears the analog cursor unless a terminal row is still hovered
}

// Freeze/thaw the panel with the analog charts. On pause the right edge is pinned at the
// newest sample so the window stops advancing; samples keep buffering for the resume catch-up.
function setDigitalPaused(paused) {
  if (digitalPaused === paused) return;
  digitalPaused = paused;
  if (paused) {
    anchorDigitalFreeze();
  } else {
    digitalFrozen = null;
    digitalFrozenId = null;
    // Back to the live rings, which kept every sample that arrived while frozen.
    for (const l of digitalLanes.values()) l.frozen = null;
    // Resuming follows the tail again, as a resumed chart does: the zoom (and its chips) go.
    leaveZoom();
  }
  if (digitalPauseBtn) {
    digitalPauseBtn.textContent = paused ? "resume" : "pause";
    digitalPauseBtn.classList.toggle("on", paused);
  }
  if (digitalPausedTag) digitalPausedTag.hidden = !paused;
  for (const l of digitalLanes.values()) l.dirty = true;
  redrawDigital();
  freezeChanged();   // recompute the pause-all button text
}

registerSurface("digital", {
  // A panel with no lanes has nothing to freeze, so it is not "live" and cannot hold the
  // pause-all button in the paused state before any digital channel has ever appeared.
  isLive: () => digitalLanes.size > 0 && !digitalPaused,
  setPaused: (paused) => setDigitalPaused(paused),
  watermark: () => digitalFrozenId,
});


// Small accessors + reset used by plots.js / terminal.js (avoid reaching into module lets).
export function getDigitalCursorX() { return digitalCursorX; }
export function getChartHoverX() { return chartHoverX; }
export function isDigitalPaused() { return digitalPaused; }

// Restore each lane's gutter readout to its held value at the live (or frozen) right edge.
// While a cursor is active setDigitalCursorAt writes the value-at-cursor into every readout;
// without this, a quiet lane would keep showing that scrubbed value after the pointer leaves,
// where the analog legend snaps back to the latest value on mouseleave.
function refreshDigitalReadouts() {
  cursorReadout = false;   // back to the live edge; the redraw tick may write pendingVal again
  const edge = digitalRightEdge();
  if (edge == null) return;
  for (const l of digitalLanes.values()) setLaneVal(l, valueAt(l, edge));
}

// Reset the digital panel to first-load state (see terminal.js clear-all).
export function clearAllDigital() {
    // Clearing empties the panel; it does not resume it. The frozen edge does go, because it
    // names a sample that no longer exists - digitalIngest re-anchors at the next one.
    digitalFrozen = null;
    digitalLast = null;
    digitalFrozenId = digitalPaused ? state.maxId : null;
    digitalLanes.clear();
    laneGroups.clear();
    $("digitalLanes").textContent = "";
    digitalCursorX = null;
    pendingCursorX = null;
    pendingClientX = -1;
    digitalShown = false;
    laneCapWarned = false;
    $("dCursor").hidden = true;
    $("digitalWrap").hidden = true;
    $("digitalHead").hidden = true;
    updateDigitalCount();
    syncDigitalExportBtn();   // no lanes left, so nothing to export
    lanesChanged();
}

export { digitalIngest, digitalLanes, setDigitalPaused, exportDigital, markDigitalDirty, redrawDigital,
         setDigitalCursorAt, refreshDigitalReadouts, buildDigitalHead, initDigitalCursorSync,
         makeSpanButton, laneDrawData, digitalRightEdge, laneKey, onLanesChanged, setLanePortTags };
