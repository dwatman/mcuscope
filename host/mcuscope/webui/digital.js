import { $, root, state, hooks, nearestX, portColor, PLOT_CAP, PLOT_SLACK } from "./state.js";
import { openExportDialog, plotDecodeOptions, plotExportPath } from "./exportdlg.js";
import { buildWindowButtons, colorFor, exitZoom, leaveZoom, openColorPicker, rgbToHex, saveColor,
         soloShow, PLOT_WINDOW_DEFAULT } from "./chrome.js";
import { continueTick, firstAtOrAfter, fitAxisTicks, fmtAxisTick, getZoom, laneSegments, mergeNarrow,
         fmtTime, hostEpochAt, hostTsAt, newHostClock, newTickClocks, spanFor, tickOffsetAt,
         windowFor, zoomFor,
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
const RULER_CHAR_PX = 6.1;          // a digit in the ruler's 10 px monospace face
const MAX_LANES = 64;               // cap on distinct digital lanes, so a device emitting rotating
                                     // enum/bits names cannot grow the DOM/heap forever
let laneCapWarned = false;
const digitalLanes = new Map();     // "<port>|<name>" -> lane {key, port, name, kind, group, labels, color, xs..., canvas}
const laneGroups = new Map();       // "<port>|<group>" -> the packed group's header element
let lanePortTags = false;           // gutters name the port once more than one has contributed
let lanesChanged = () => {};        // plots.js: the Plots section's empty state, hint and port tags
let bumpPlotSeed = () => {};        // plots.js: the seed generation api.js's backfill gate reads
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
// Per-port reset offsets for the tick axis, shared with the charts (plots.js imports it).
const tickClocks = newTickClocks();
// Backward steps of the host wall clock (timewindow.js continueHost), shared the same way.
const hostClock = newHostClock();
let digitalCollapsed = false;       // lanes hidden via the header collapse button
let digitalPauseBtn = null;         // header pause/resume button (built in buildDigitalHead)
let digitalPausedTag = null;        // header "paused" tag
let digitalExportBtn = null;        // header export button (disabled while no lane is shown)

// Each lane sample's drawn tick, host time and line id, per stream, for the shown-window export:
// a lane stores transitions only, so it cannot name the rows a window holds. Per stream (the
// plots.js chart key), not per port, because the history seed feeds one stream after another.
// Host times are nudged apart as addSample nudges a chart's, so a stream's chart and lanes
// place a burst alike. Capped at PLOT_CAP samples per stream; once trimmed, a window edge
// before the oldest kept sample is exported by time instead (digitalShownWindow).
const laneIds = new Map();          // stream -> {port, ticks, hosts, ids, trimmed, frozen}

// Lane names, like channel names, are unique only within a port (SPEC 9.2).
function laneKey(port, name) { return port + "|" + name; }
function onLanesChanged(fn) { lanesChanged = fn; }
// Registered by plots.js, which owns the token (a static import of it here is a cycle: plots.js
// calls onLanesChanged at its top level, so whichever of the two evaluates first would read a
// binding of the other still in its TDZ).
function onSeedBump(fn) { bumpPlotSeed = fn; }

function digitalIngest(port, points, x, stream) {
  // The same class-6 gate addSample has, at this producer's own boundary: one non-finite x
  // is permanent here, because the monotonic bump below is `hx <= xsHost[n-1]` and
  // `hx <= NaN` is false, so no later sample is ever bumped again. valueAt/nearestX then
  // binary-search a non-monotonic array and anchorDigitalFreeze takes a max over it.
  if (!Number.isFinite(x.host) || !Number.isFinite(x.tick)) return;
  const hostEpoch = Number.isInteger(x.id) ? hostEpochAt(hostClock, x.id) : undefined;
  showDigital();
  let tickX = null;   // this sample's drawn tick, past any reset (timewindow.continueTick)
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
    const prev = lane.prevTick;
    const c = continueTick(tickClocks, port, prev, x.tick, x.host);
    lane.prevTick = c;
    if (tickX === null || c.x > tickX) tickX = c.x;
    // A reset, or a host clock step between its samples, breaks the lane: a null vertex where
    // its last sample before it was.
    const stepped = hostEpoch !== undefined && lane.hostEpoch !== undefined && lane.hostEpoch !== hostEpoch;
    if (hostEpoch !== undefined) lane.hostEpoch = hostEpoch;
    if ((c.restart || stepped) && lane.vs.length) pushVertex(lane, prev.host, prev.x, null);
    // Transition reduction: store a vertex only when the value changes (plus the first sample).
    // vs[i] is held from its stored time xs[i] until the next vertex xs[i+1], and the draw
    // functions extend the newest segment to the right edge - so a repeat value adds
    // nothing and must NEVER overwrite the held level's recorded start time (doing so would
    // drag the segment forward and render it as a narrow right-shifted sliver).
    if (!lane.vs.length || lane.vs[lane.vs.length - 1] !== val) pushVertex(lane, x.host, c.x, val);
    if (!digitalPaused) {   // paused: freeze the readout with the frozen window
      lane.dirty = true;
      // The gutter readout is written by redrawDigital (5 Hz, hidden lanes skipped), not per
      // decoded point: this path runs per sample and the text usually does not change at all.
      lane.pendingVal = val;
    }
  }
  // Every lane capped: no lane draws this sample, so the index must not widen the port's ids.
  if (tickX === null) tickX = x.tick + tickOffsetAt(tickClocks, port, x.host);
  else if (Number.isInteger(x.id)) noteLaneId(stream, port, tickX, x.host, x.id);
  if (digitalLast === null) digitalLast = { host: x.host, tick: tickX };
  else {   // per field: the history seed and the live stream can interleave out of order
    if (x.host > digitalLast.host) digitalLast.host = x.host;
    if (tickX > digitalLast.tick) digitalLast.tick = tickX;
  }
}

// Append one vertex, keeping BOTH arrays strictly increasing: valueAt/nearestX/digitalRightEdge
// binary-search and take a max, which need monotonic x in whichever array the time mode reads.
function pushVertex(lane, host, tick, val) {
  const n = lane.vs.length;
  if (n && host <= lane.xsHost[n - 1]) host = lane.xsHost[n - 1] + 1e-4;
  if (n && tick <= lane.xsTick[n - 1]) tick = lane.xsTick[n - 1] + 1e-4;
  lane.xsHost.push(host); lane.xsTick.push(tick); lane.vs.push(val);
  // Block trim (see PLOT_SLACK): shift() per sample is O(PLOT_CAP) once at cap.
  if (lane.vs.length > PLOT_CAP + PLOT_SLACK) {
    const drop = lane.vs.length - PLOT_CAP;
    lane.xsHost.splice(0, drop); lane.xsTick.splice(0, drop); lane.vs.splice(0, drop);
  }
}

// Break every lane after its newest sample (plots.js breakChart, for the same callers): the
// level it held is not drawn across rows the page never received.
function breakLanes() {
  for (const lane of digitalLanes.values()) {
    const prev = lane.prevTick;
    if (!prev || !lane.vs.length || lane.vs[lane.vs.length - 1] === null) continue;
    pushVertex(lane, prev.host, prev.x, null);
    if (!digitalPaused) lane.dirty = true;
  }
}

function noteLaneId(stream, port, tick, host, id) {
  let ix = laneIds.get(stream);
  if (!ix) {
    // Born after the freeze: none of it is on the frozen view (as addDigitalLane).
    const empty = () => ({ ticks: [], hosts: [], ids: [], trimmed: false });
    ix = { port, ...empty(), frozen: digitalPaused ? empty() : null };
    laneIds.set(stream, ix);
  }
  const n = ix.ticks.length;
  ix.ticks.push(n && tick < ix.ticks[n - 1] ? ix.ticks[n - 1] : tick);   // non-decreasing, for the search
  ix.hosts.push(n && host <= ix.hosts[n - 1] ? ix.hosts[n - 1] + 1e-4 : host);
  ix.ids.push(id);
  if (n + 1 > PLOT_CAP + PLOT_SLACK) {
    const drop = n + 1 - PLOT_CAP;
    ix.ticks.splice(0, drop); ix.hosts.splice(0, drop); ix.ids.splice(0, drop);
    ix.trimmed = true;
  }
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
  for (const ix of laneIds.values()) {
    ix.frozen = { ticks: ix.ticks.slice(), hosts: ix.hosts.slice(), ids: ix.ids.slice(), trimmed: ix.trimmed };
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

// A value listed twice takes its last label, as the CLI and the export do (a dict built from
// the pairs); SPEC 2.5 does not forbid the repeat.
function enumLabel(lane, v) {
  const hit = (lane.labels || []).findLast((p) => p[0] === v);
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
  // That includes a panel paused before its first lane, which stays empty as a chart born
  // paused does, and keeps its pause-time watermark.
  if (digitalPaused) lane.frozen = { xsHost: [], xsTick: [], vs: [] };
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
  freezeChanged();   // the first lane makes a live panel a live surface
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

  // No zoom predicate: a zoom pauses the lanes, and only a resume, which drops it, thaws them.
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


// The window the lanes draw, for `port`'s export: the drag zoom's range while the panel is frozen
// on one, else the selector's span ending at the shared right edge (frozen while paused); null
// with no edge. It is the ids of the first and last samples of `port` inside it (laneIds), under
// every time base, since the samples of one burst share a timestamp; null when there are none.
// An index trimmed past the lower edge names no id for the rows before its oldest sample, so
// that side goes by host time, which is exact only to the burst at that edge; so does the upper
// side when the window ends before it too.
function digitalShownWindow(port) {
  const edge = digitalPaused ? digitalFrozen : digitalLast;
  if (!edge) return null;
  const tick = state.timeMode === "tick";
  const z = digitalPaused ? zoomFor(getZoom(), state.timeMode) : null;
  // The tail ends at the newest sample, whose host time a burst's nudge can carry past the edge.
  const lo = z ? z.min : (tick ? edge.tick : edge.host) - spanFor(state.timeMode, digitalWindow);
  const hi = z ? z.max : Infinity;
  let sinceId = null, idTo = null, loCut = false, hiCut = false;
  for (const ix of laneIds.values()) {
    if (ix.port !== port) continue;
    const s = digitalPaused && ix.frozen ? ix.frozen : ix, xs = tick ? s.ticks : s.hosts, ids = s.ids;
    if (s.trimmed && lo < xs[0]) loCut = true;
    if (s.trimmed && hi < xs[0]) hiCut = true;
    const n = xs.length, first = firstAtOrAfter(xs, lo, n);
    let last = first;
    while (last < n && xs[last] <= hi) last++;
    if (--last < first) continue;
    if (sinceId === null || ids[first] - 1 < sinceId) sinceId = ids[first] - 1;
    if (idTo === null || ids[last] > idTo) idTo = ids[last];
  }
  const hostAt = (t) => hostTsAt(hostClock, tick ? hostAtTick(port, t) : t);   // drawn x to ts
  // A cut upper side implies a cut lower one: the same index starts after both edges.
  if (loCut) return hiCut ? { fromTs: hostAt(lo), toTs: hostAt(hi) } : { fromTs: hostAt(lo), idTo };
  return idTo === null ? null : { sinceId, idTo };
}

// The host time `port`'s lanes put at drawn tick `t`: interpolated between the nearest held
// samples either side of it, from the lane vertices and the id index alike. Called only with a
// trimmed index whose oldest sample is after `t`, so one always lies above it; with none below,
// nothing earlier is drawn, and that sample's time is the edge.
function hostAtTick(port, t) {
  let below = null, above = null;   // [tick, host]
  const near = (ticks, hosts) => {
    const n = ticks.length, i = firstAtOrAfter(ticks, t, n);
    if (i < n && (!above || ticks[i] < above[0])) above = [ticks[i], hosts[i]];
    if (i > 0 && (!below || ticks[i - 1] > below[0])) below = [ticks[i - 1], hosts[i - 1]];
  };
  for (const l of digitalLanes.values()) {
    if (l.port !== port) continue;
    const s = digitalPaused && l.frozen ? l.frozen : l;
    near(s.xsTick, s.xsHost);
  }
  for (const ix of laneIds.values()) {
    if (ix.port !== port) continue;
    const s = digitalPaused && ix.frozen ? ix.frozen : ix;
    near(s.ticks, s.hosts);
  }
  if (!below) return above[1];
  return below[1] + (above[1] - below[1]) * (t - below[0]) / (above[0] - below[0]);
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
  const chosen = (v) => (ports.length > 1 && ports.includes(v.port) ? v.port : ports[0]);
  // Taken now, while the freeze that drew them stands; per port, since the ids differ per port.
  const wins = new Map(ports.map((pt) => [pt, digitalShownWindow(pt)]));
  // A port with no sample in a window another port fills exports an empty range, not everything.
  const none = { sinceId: digitalFrozenId, idTo: digitalFrozenId };
  openExportDialog({
    kind: "plot",
    watermark: digitalPaused ? digitalFrozenId : null,
    shown: [...wins.values()].some((w) => w) ? (v) => wins.get(chosen(v)) || none : null,
    options: [
      ...portOpt,
      { name: "format", type: "select", label: "Format", choices: ["long"], value: "long" },
      ...plotDecodeOptions(),
    ],
    build: (p, v) => plotExportPath(p, v, namesOf(chosen(v)), chosen(v)),
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
  const themeNow = root.getAttribute("data-theme") || "";
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
      axis = { win, ...fitAxisTicks(state, win, w, (s) => s.length * RULER_CHAR_PX),
               grid: cs.getPropertyValue("--border").trim() || "#333",
               label: cs.getPropertyValue("--text-faint").trim() || "#889" };
    }
    return axis;
  };
  for (const [lane, cw] of lanes) {
    if (cw <= 0) continue;   // panel hidden; leave the lane dirty for when it is shown
    const sizeChanged = lane.canvas.width !== Math.round(cw * dpr);
    // The edge and the theme are keyed per lane: a lane whose stream went quiet gets no dirty
    // flag, yet draws against the edge a sibling stream moves, in the theme's grid colour.
    const repaint = lane.dirty || lane._sizedirty || sizeChanged
      || lane.drawnEdge !== xmax || lane.theme !== themeNow;
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
    lane.drawnEdge = xmax;
    lane.theme = themeNow;
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
  let end = -Infinity;   // right edge of the last label drawn
  for (const t of axis.ticks) {
    const x = Math.round(axis.win.toPx(t)) + 0.5;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 4); g.stroke();
    const text = fmtAxisTick(state, t, axis.step);
    const tw = g.measureText ? g.measureText(text).width || 0 : 0;
    // Centred on its tick, but kept inside the ruler at either end; a label that would then
    // overlap the one before it (the end clamp pushes the last one left) is left out.
    const at = Math.max(0, Math.min(w - tw, x - tw / 2));
    if (at < end + 4) continue;
    g.textAlign = "left";
    g.fillText(text, at, 5);
    end = at + tw;
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

// Segments narrower than this, two or more in a row, draw as one "busy" block (timewindow.js
// mergeNarrow): a lane toggling at 100 Hz is thousands of sub-pixel edges in a 30 s window.
const MIN_SEG_PX = 1.5;

// bits: a square wave. Each stored vertex is a value change; the level vs[i] holds from its
// sample to the next (or the right edge), and starts at the lane's first sample (laneSegments).
// A faint fill sits under the high level; a busy block is filled between both levels.
function drawBits(g, lane, { xs, vs }, win, h) {
  const yHi = 8, yLo = h - 8, n = xs.length;
  const y = (v) => (v ? yHi : yLo);
  const segs = mergeNarrow(laneSegments(xs, win), vs, MIN_SEG_PX);   // only the on-screen vertices
  if (!segs.length) return;
  g.fillStyle = lane.color + "22";
  for (const { busy, i, x0, x1 } of segs) if (!busy && vs[i]) g.fillRect(x0, yHi, x1 - x0, yLo - yHi);
  g.fillStyle = lane.color + "55";
  for (const { busy, x0, x1 } of segs) if (busy) g.fillRect(x0, yHi, x1 - x0, yLo - yHi);
  g.strokeStyle = lane.color; g.lineWidth = 1.6;
  g.beginPath();
  let pen = false;                                        // a null vertex (a reset) lifts it
  for (const { busy, i, x0, x1 } of segs) {
    if (busy) {                                           // both rails across the block
      g.moveTo(x0, yHi); g.lineTo(x1, yHi); g.moveTo(x0, yLo); g.lineTo(x1, yLo);
      pen = false;
      continue;
    }
    if (vs[i] == null) { pen = false; continue; }
    if (!pen) { g.moveTo(x0, y(vs[i])); pen = true; }    // level active at the left edge
    g.lineTo(x1, y(vs[i]));                               // hold this level
    if (i + 1 < n && vs[i + 1] != null) g.lineTo(x1, y(vs[i + 1]));   // edge to the next level
  }
  g.stroke();
}

// enum: a monochrome FPGA bus envelope (top/bottom rails joined by X-crossings at each
// transition), a whisper of fill, and the label centred and hard-clipped to the segment so
// it never spills past its crossings (a very narrow segment shows no text). One path for every
// rail and crossing of the lane; only a labelled segment (at least 16 px) pays for a clip.
function drawEnum(g, lane, { xs, vs }, win, h) {
  const yT = 6, yB = h - 6, ym = (yT + yB) / 2, xo = 5;
  const segs = mergeNarrow(laneSegments(xs, win), vs, MIN_SEG_PX);   // only the on-screen segments
  const inner = ({ x0, x1 }) => Math.max(0, x1 - x0 - 2 * xo);        // width between the crossings
  g.fillStyle = lane.color + "14";
  for (const s of segs) {
    if (!s.busy && vs[s.i] != null && inner(s) > 0) g.fillRect(s.x0 + xo, yT, inner(s), yB - yT);
  }
  g.fillStyle = lane.color + "44";
  for (const s of segs) if (s.busy) g.fillRect(s.x0, yT, s.x1 - s.x0, yB - yT);
  g.strokeStyle = lane.color; g.lineWidth = 1.4;
  g.beginPath();
  for (const { busy, i, x0, x1 } of segs) {
    if (busy) { g.moveTo(x0, yT); g.lineTo(x1, yT); g.moveTo(x0, yB); g.lineTo(x1, yB); continue; }
    if (vs[i] == null) continue;                          // a reset: no bus until the next value
    g.moveTo(x0 + xo, yT); g.lineTo(x1 - xo, yT);   // top rail
    g.moveTo(x0 + xo, yB); g.lineTo(x1 - xo, yB);   // bottom rail
    g.moveTo(x0, ym); g.lineTo(x0 + xo, yT);        // opening crossing (upper)
    g.moveTo(x0, ym); g.lineTo(x0 + xo, yB);        // opening crossing (lower)
    g.moveTo(x1 - xo, yT); g.lineTo(x1, ym);        // closing crossing (upper)
    g.moveTo(x1 - xo, yB); g.lineTo(x1, ym);        // closing crossing (lower)
  }
  g.stroke();
  g.font = "10px ui-monospace, monospace";
  g.textBaseline = "middle"; g.textAlign = "center";
  g.fillStyle = lane.color;
  for (const s of segs) {
    if (s.busy || vs[s.i] == null || inner(s) <= 6) continue;
    g.save();
    g.beginPath(); g.rect(s.x0 + xo, yT, inner(s), yB - yT); g.clip();
    g.fillText(enumLabel(lane, vs[s.i]), (s.x0 + s.x1) / 2, ym);
    g.restore();
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
// Null while paused with no frozen edge (paused before any lane): nothing is drawn then.
function digitalRightEdge() {
  const edge = digitalPaused ? digitalFrozen : digitalLast;
  if (edge === null) return null;
  return state.timeMode === "tick" ? edge.tick : edge.host;
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
    // Back to the live rings, which kept every sample that arrived while frozen. The readout
    // cache was not fed while paused; the ring's newest vertex is the newest value.
    for (const l of digitalLanes.values()) {
      l.frozen = null;
      if (l.vs.length) l.pendingVal = l.vs[l.vs.length - 1];
    }
    for (const ix of laneIds.values()) ix.frozen = null;
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
    // names a sample that no longer exists; a lane born before resume stays empty (addDigitalLane).
    // The lanes are fed by plotIngest, so api.js's backfill gate reads the chart seed token for
    // them too: bump it here rather than leaning on clearAllCharts being called beside this.
    bumpPlotSeed();
    digitalFrozen = null;
    digitalLast = null;
    digitalFrozenId = digitalPaused ? state.maxId : null;
    digitalLanes.clear();
    laneIds.clear();
    laneGroups.clear();
    tickClocks.clear();   // the reset offsets describe samples that no longer exist
    hostClock.epochs.length = 0; hostClock.top = null;   // and so do the host steps
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
    freezeChanged();          // no lanes, so no longer a live surface
}

export { digitalIngest, digitalLanes, breakLanes, setDigitalPaused, exportDigital, markDigitalDirty,
         redrawDigital, setDigitalCursorAt, refreshDigitalReadouts, buildDigitalHead, initDigitalCursorSync,
         makeSpanButton, laneDrawData, digitalRightEdge, laneKey, onLanesChanged, onSeedBump,
         setLanePortTags, tickClocks, hostClock };
