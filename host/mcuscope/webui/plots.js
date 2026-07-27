import { $, root, pad2, state, hooks, colorFor, saveColor, buildWindowButtons,
         downloadCsv, nearestX, lineTick, sidebar, PLOT_CAP, PLOT_SLACK,
         rgbToHex } from "./state.js";
import { digitalIngest, setDigitalCursorAt, refreshDigitalReadouts, getDigitalCursorX,
         getChartHoverX, buildDigitalHead, initDigitalCursorSync,
         redrawDigital } from "./digital.js";

// ---- realtime plots (sidebar): uPlot strip charts, one per stream (SPEC 9.2) --------
//
// Fed from the same rows as the terminal (backfill + the one /ws), decoded client-side
// the way the daemon decodes them: !pd caches a per-(port,sid) definition, !ps decodes
// against it, !p is ad-hoc. Each stream (sid) gets one chart, ad-hoc channels share one;
// every channel keeps a capped ring buffer, and a redraw timer repaints the visible
// window. X axis is host receive time by default, toggleable to the MCU tick.

const PLOT_REDRAW_MS = 200;                    // ~5 fps repaint of the visible window
// type -> [byte width, signed, is_float]; mirrors protocol._PLOT_TYPES.
// Null-prototype on purpose. As a plain object, `"toString" in PLOT_TYPES` is true, so a
// device-supplied `!pd 0 a:toString` passed validation and then threw a TypeError deep in
// decode - inside the WebSocket message loop, which discarded every remaining row in that
// frame. Untrusted device output must not be able to reach Object.prototype.
const PLOT_TYPES = Object.assign(Object.create(null), {
  u1: [1, false, false], s1: [1, true, false], u2: [2, false, false], s2: [2, true, false],
  u4: [4, false, false], s4: [4, true, false], f4: [4, false, true],
});
const PLOT_NAME_RE = /^[A-Za-z_][A-Za-z0-9_.]*$/;
// Enum/bits sigils in the unit slot (SPEC 2.5); mirrors protocol._ENUM_TYPES etc.
const ENUM_TYPES = new Set(["u1", "s1", "u2", "s2", "u4", "s4"]);
const BITS_TYPES = new Set(["u1", "u2", "u4"]);
const LABEL_RE = /^[A-Za-z0-9_.]{1,16}$/;

const MAX_CHANNELS = 64;        // cap on distinct analog channels across all charts, so a
                                 // device emitting rotating channel names cannot grow the DOM/heap forever
let channelCapWarned = false;

const plotDefs = new Map();     // "port|sid" -> {sid, channels:[{name,type,scale,unit,kind,labels,lanes}]}
const charts = new Map();       // chart key ("s0" | "adhoc") -> chart object
let plotTheme = "";             // last theme charts were built for (recolor on change)
let stepPath = null;            // shared uPlot stepped-path builder (lazy: needs uPlot loaded)

// -- client-side decode (mirror of protocol.py plot helpers) --
function parsePlotValue(s) { return /^-?\d+(\.\d+)?$/.test(s) ? parseFloat(s) : null; }

function parsePlotAdhoc(raw) {
  const parts = raw.trim().split(/\s+/);
  if (parts.length < 3 || parts[0] !== "!p") return null;
  if (!/^\d+$/.test(parts[1]) || +parts[1] > 0xFFFFFFFF) return null;
  const points = [];
  for (const pair of parts.slice(2)) {
    const eq = pair.indexOf("=");
    if (eq < 1) return null;
    const name = pair.slice(0, eq), val = parsePlotValue(pair.slice(eq + 1));
    if (name.length > 16 || !PLOT_NAME_RE.test(name) || val === null) return null;
    points.push([name, val]);
  }
  return points.length ? { tick: +parts[1], sid: null, points } : null;
}

function parseChannelSpec(spec) {
  const f = spec.split(":");
  if (f.length < 2 || f.length > 3) return null;
  const name = f[0];
  let unit = f.length === 3 ? f[2] : null;
  if (name.length > 16 || !PLOT_NAME_RE.test(name)) return null;
  const star = f[1].indexOf("*");
  const type = star < 0 ? f[1] : f[1].slice(0, star);
  if (!(type in PLOT_TYPES)) return null;
  let scale = null;
  if (star >= 0) { scale = parsePlotValue(f[1].slice(star + 1)); if (scale === null) return null; }
  if (unit === "") return null;
  let kind = "analog", labels = null, lanes = null;
  if (unit !== null && (unit[0] === "=" || unit[0] === "/")) {
    const [w, signed] = PLOT_TYPES[type];
    if (unit[0] === "=") {
      if (!ENUM_TYPES.has(type)) return null;
      labels = parseEnumLabels(unit.slice(1), signed);
      if (!labels) return null;
      kind = "enum";
    } else {
      if (!BITS_TYPES.has(type)) return null;
      lanes = parseBitLanes(unit.slice(1), w);
      if (!lanes) return null;
      kind = "bits";
    }
    unit = null;   // the sigil consumed the unit slot; it is not a display unit
  }
  return { name, type, scale, unit, kind, labels, lanes };
}

function parseEnumLabels(body, signed) {
  const out = [];
  for (const item of body.split(",")) {
    const eq = item.indexOf("=");
    if (eq < 1) return null;
    const label = item.slice(eq + 1);
    if (!LABEL_RE.test(label)) return null;
    const valStr = item.slice(0, eq);
    if (!/^-?\d+$/.test(valStr)) return null;   // decimal only, mirrors int(val_s, 10)
    const v = Number(valStr);
    if (!signed && v < 0) return null;
    out.push([v, label]);
  }
  return out.length ? out : null;
}

function parseBitLanes(body, width) {
  const lanes = body.split(",").map((s) => (s === "" ? null : s));
  if (lanes.some((x) => x !== null && (x.length > 16 || !PLOT_NAME_RE.test(x)))) return null;
  if (!lanes.length || lanes.length > width * 8 || lanes.every((x) => x === null)) return null;
  return lanes;
}

function parsePlotDef(raw) {
  const parts = raw.trim().split(/\s+/);
  if (parts.length < 3 || parts[0] !== "!pd") return null;
  if (!/^\d$/.test(parts[1])) return null;
  const channels = [];
  for (const spec of parts.slice(2)) { const c = parseChannelSpec(spec); if (!c) return null; channels.push(c); }
  // Index every emitted point name (channel name, or each bit lane) -> its channel, so ingest
  // is an O(1) lookup per point instead of an O(channels) scan per point per sample.
  const byName = new Map();
  for (const c of channels) {
    byName.set(c.name, c);
    if (c.lanes) for (const ln of c.lanes) if (ln !== null) byName.set(ln, c);
  }
  return { sid: parts[1], channels, byName };
}

function decodePlotField(hex, type) {
  const [w, signed, isFloat] = PLOT_TYPES[type];
  if (hex.length !== w * 2 || !/^[0-9a-fA-F]+$/.test(hex)) return null;
  const bytes = new Uint8Array(w);
  for (let i = 0; i < w; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  if (isFloat) {
    const f = new DataView(bytes.buffer).getFloat32(0, false);   // big-endian
    // Drop non-finite samples rather than plotting them. A single +/-Infinity (7F800000,
    // an ordinary firmware divide-by-zero) propagates into uPlot's min/max scan, and
    // uPlot.rangeNum() then returns [NaN, NaN] - so the entire trace silently disappears
    // for as long as that sample is inside the window, with no error anywhere.
    return Number.isFinite(f) ? f : null;
  }
  let v = 0;
  for (let i = 0; i < w; i++) v = v * 256 + bytes[i];
  if (signed && (bytes[0] & 0x80)) v -= 2 ** (w * 8);
  return v;
}

function decodePlotSample(raw, def) {
  const parts = raw.trim().split(/\s+/);
  if (parts.length !== 4 || parts[0] !== "!ps" || parts[1] !== def.sid) return null;
  if (!/^[0-9a-fA-F]+$/.test(parts[2])) return null;
  const tick = parseInt(parts[2], 16);
  if (!(tick >= 0 && tick <= 0xFFFFFFFF)) return null;   // one out-of-range tick must not yank the shared window
  const vals = parts[3].split(",");
  if (vals.length !== def.channels.length) return null;
  const points = [];
  for (let i = 0; i < vals.length; i++) {
    const ch = def.channels[i];
    let v = decodePlotField(vals[i], ch.type);
    if (v === null) return null;
    if (ch.kind === "bits") {
      const bits = Math.trunc(v);
      (ch.lanes || []).forEach((lane, b) => { if (lane !== null) points.push([lane, (bits >> b) & 1]); });
    } else if (ch.kind === "enum") {
      points.push([ch.name, v]);           // raw integer, unscaled
    } else {
      if (ch.scale !== null) v *= ch.scale;
      points.push([ch.name, v]);
    }
  }
  return { tick, sid: def.sid, points };
}

// -- ingest --
function plotIngest(row) {
  if (row.chan !== "event") return;
  const raw = row.raw;
  const port = row.port || "-";
  if (raw.startsWith("!pd")) {
    const def = parsePlotDef(raw);
    if (def) plotDefs.set(port + "|" + def.sid, def);
    return;
  }
  let sample = null, key = null, unitFor = null;
  if (raw.startsWith("!ps")) {
    const sid = raw.trim().split(/\s+/)[1];
    const def = plotDefs.get(port + "|" + sid);
    if (def) { sample = decodePlotSample(raw, def); if (sample) { key = "s" + sample.sid; unitFor = def; } }
  } else if (raw.startsWith("!p")) {
    sample = parsePlotAdhoc(raw); if (sample) key = "adhoc";
  } else return;
  if (!sample) return;
  const x = { host: row.ts, tick: sample.tick };   // host seconds, MCU tick in ms
  if (key === "adhoc") {                           // ad-hoc !p is always analog
    addSample(ensureChart(key, sample.sid), sample.points, x, unitFor);
    return;
  }
  const digital = [], analog = [];
  for (const [name, val] of sample.points) {
    const ch = unitFor && unitFor.byName.get(name);
    if (ch && (ch.kind === "enum" || ch.kind === "bits")) digital.push([name, val, ch]);
    else analog.push([name, val]);
  }
  if (analog.length) addSample(ensureChart(key, sample.sid), analog, x, unitFor);
  if (digital.length) digitalIngest(sample.sid, digital, x);
}

function unitOf(def, name) {
  if (!def) return null;
  const c = def.byName.get(name);
  return c ? c.unit : null;
}

// -- chart data model + DOM --
function ensureChart(key, sid) {
  let chart = charts.get(key);
  if (chart) return chart;
  const empty = $("plotCharts").querySelector(".empty-state");
  if (empty) empty.remove();
  chart = {
    key, sid, xsHost: [], xsTick: [], lastHost: null,
    names: [], ys: new Map(), unit: new Map(), show: new Map(), isInt: new Map(),
    window: 30, paused: false, frozenLen: null, collapsed: false, uplot: null, dirty: false,
  };
  buildChartDom(chart);
  charts.set(key, chart);
  return chart;
}

function addSample(chart, points, x, def) {
  // Host receive time arrives in TCP-batched bursts, so several samples can share (or
  // even slightly reorder) a timestamp; uPlot needs a strictly increasing x, so nudge
  // any non-advancing host time forward by a sliver. The MCU tick is already monotonic.
  let hx = x.host;
  if (chart.lastHost !== null && hx <= chart.lastHost) hx = chart.lastHost + 1e-4;
  chart.lastHost = hx;
  chart.xsHost.push(hx);
  chart.xsTick.push(x.tick);
  const len = chart.xsHost.length;
  const present = new Map(points);
  let newChannel = false;
  for (const [name, val] of points) {
    if (!chart.ys.has(name)) {
      if (plotChannelMeta.size >= MAX_CHANNELS) {
        if (!channelCapWarned) {
          channelCapWarned = true;
          console.warn(`plots: channel cap (${MAX_CHANNELS}) reached, ignoring new channel "${name}"`);
          updatePlotCount();
        }
        continue;   // drop the sample for this (uncreated) channel, keep the rest of the row
      }
      addChannel(chart, name, unitOf(def, name), channelIsInt(def, name));
      newChannel = true;
    }
    chart.ys.get(name).push(val);
    // Ad-hoc channels have no declared type; treat them as integer until a fractional
    // value proves otherwise (then stay float, so the readout does not flip per sample).
    if (chart.sid === null && chart.isInt.get(name) && !Number.isInteger(val)) {
      chart.isInt.set(name, false);
    }
  }
  for (const name of chart.names) {                 // channels absent from this sample get a gap
    if (!present.has(name)) chart.ys.get(name).push(null);
  }
  // Block trim, matching pushBuffer in state.js: splicing one point per arriving sample is
  // O(PLOT_CAP) per sample once the ring is full.
  if (len > PLOT_CAP + PLOT_SLACK) {
    const drop = len - PLOT_CAP;
    chart.xsHost.splice(0, drop); chart.xsTick.splice(0, drop);
    for (const arr of chart.ys.values()) arr.splice(0, drop);
    if (chart.frozenLen != null) chart.frozenLen = Math.max(0, chart.frozenLen - drop);
  }
  if (newChannel) renderChans(chart);
  if (!chart.paused) chart.dirty = true;   // paused charts freeze; live data still buffers
}

function addChannel(chart, name, unit, isInt) {
  const backfill = new Array(chart.xsHost.length - 1).fill(null);
  chart.ys.set(name, backfill);
  chart.names.push(name);
  chart.unit.set(name, unit || null);
  chart.show.set(name, true);
  chart.isInt.set(name, isInt);
  plotChannelMeta.set(name, chart);   // for the total-channel count
}

// A typed channel reads as integer when its type is an integer type and any scale factor
// is itself integer (a fractional scale, or an f4 type, makes the decoded value a float).
function channelIsInt(def, name) {
  if (!def) return true;   // ad-hoc: assume integer until a fractional value appears
  const c = def.byName.get(name);
  if (!c) return true;
  return c.type !== "f4" && (c.scale === null || Number.isInteger(c.scale));
}

const plotChannelMeta = new Map();

function buildChartDom(chart) {
  const el = document.createElement("div");
  el.className = "plot-chart";
  const head = document.createElement("div");
  head.className = "plot-head";

  const collapse = document.createElement("button");
  collapse.className = "iconbtn plot-collapse"; collapse.textContent = "▾";  // down triangle
  collapse.title = "Hide / show this chart";
  collapse.addEventListener("click", () => {
    chart.collapsed = !chart.collapsed;
    chart.bodyEl.hidden = chart.collapsed;
    collapse.textContent = chart.collapsed ? "▸" : "▾";
    if (!chart.collapsed) { chart.dirty = true; requestAnimationFrame(resizePlots); }
  });

  const title = document.createElement("span");
  title.className = "ptitle";
  title.textContent = chart.sid === null ? "ad-hoc (!p)" : "stream " + chart.sid;
  const ptag = document.createElement("span");
  ptag.className = "paused-tag"; ptag.textContent = "paused"; ptag.hidden = true;
  chart.pausedTag = ptag;

  // applies even while paused (redraw honours the freeze slice)
  const win = buildWindowButtons(chart.window, (secs) => { chart.window = secs; chart.dirty = true; });
  const pause = document.createElement("button");
  pause.className = "iconbtn"; pause.textContent = "pause";
  chart.pauseBtn = pause;
  pause.addEventListener("click", () => setChartPaused(chart, !chart.paused));
  const exp = document.createElement("button");
  exp.className = "iconbtn"; exp.textContent = "csv";
  exp.title = "Export the shown channels over the current window as CSV";
  exp.addEventListener("click", () => exportChart(chart));
  const spacer = document.createElement("div"); spacer.className = "spacer";
  head.append(collapse, title, ptag, spacer, win, pause, exp);

  const body = document.createElement("div");
  body.className = "plot-body";
  const chans = document.createElement("div");
  chans.className = "plot-chans";
  const canvas = document.createElement("div");
  canvas.className = "plot-canvas";
  body.append(chans, canvas);
  el.append(head, body);
  $("plotCharts").appendChild(el);
  chart.el = el; chart.bodyEl = body; chart.chansEl = chans; chart.canvasEl = canvas;
}

function renderChans(chart) {
  const host = chart.chansEl;
  host.textContent = "";
  chart.names.forEach((name, i) => {
    const lab = document.createElement("label");
    lab.classList.toggle("off", !chart.show.get(name));
    const sw = document.createElement("span");
    sw.className = "swatch"; sw.style.background = colorFor(name, i);
    sw.title = "Click to set colour";
    // Swatch: open a colour picker (does NOT toggle show). Live swatch feedback on input (cheap),
    // but persist + re-stroke the series only on commit (change fires once when the picker closes),
    // so dragging the picker does not thrash a full uPlot destroy+recreate per tick.
    sw.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const inp = document.createElement("input");
      inp.type = "color";
      inp.value = rgbToHex(colorFor(name, i));
      inp.oninput = () => { sw.style.background = inp.value; };   // preview only, no rebuild
      inp.onchange = () => {
        saveColor(name, inp.value);
        sw.style.background = inp.value;
        buildUplot(chart);   // rebuild once, to re-stroke the series in the committed colour
      };
      inp.click();
    });
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = chart.show.get(name); cb.style.display = "none";
    const txt = document.createElement("span"); txt.textContent = name;
    lab.append(cb, sw, txt);
    const unit = chart.unit.get(name);
    if (unit) { const u = document.createElement("span"); u.className = "unit"; u.textContent = unit; lab.appendChild(u); }
    // Name (the rest of the label): toggle the trace on/off.
    lab.addEventListener("click", (e) => {
      e.preventDefault();
      const on = !chart.show.get(name);
      chart.show.set(name, on);
      lab.classList.toggle("off", !on);
      if (chart.uplot) chart.uplot.setSeries(i + 1, { show: on });
    });
    host.appendChild(lab);
  });
  updatePlotCount();
}

function updatePlotCount() {
  const n = plotChannelMeta.size;
  let text = n ? `${n} channel${n === 1 ? "" : "s"}` : "";
  if (channelCapWarned) text += ` (limit ${MAX_CHANNELS} reached)`;
  $("plotCount").textContent = text;
}

// -- uPlot creation / redraw --
function plotColors() {
  const cs = getComputedStyle(root);
  return {
    axis: cs.getPropertyValue("--text-faint").trim() || "#889",
    grid: cs.getPropertyValue("--border").trim() || "#333",
    label: cs.getPropertyValue("--text-dim").trim() || "#aaa",
  };
}

function relBase() { return state.anchorTs == null ? 0 : state.anchorTs; }
function tickBase() { return state.anchorTick == null ? 0 : state.anchorTick; }

// Axis labels are bare numbers (no sign, no unit): the unit is shown once in the plots
// header (see syncTimeSeg). Relative modes are zeroed at the shared reset point.
function xAxisValues(u, splits) {
  return splits.map((v) => {
    if (state.timeMode === "tick") return String(Math.round(v - tickBase()));
    if (state.timeMode === "rel") return (v - relBase()).toFixed(1);
    const d = new Date(v * 1000);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  });
}

// Legend value formatter. Integer channels show as integers, float channels to 3 decimals
// (or scientific at the extremes). The bounded width plus tabular-nums + a fixed min-width
// in CSS keeps the readout from shuffling sideways as the number of digits changes.
function fmtPlotVal(v, isInt) {
  if (v == null) return "--";
  if (isInt) return String(Math.round(v));
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return v.toExponential(2);
  return v.toFixed(3);
}

// The cursor readout keeps a unit (there is room) but no leading "+".
function fmtPlotX(u, v) {
  if (v == null) return "--";
  if (state.timeMode === "tick") return Math.round(v - tickBase()) + " ms";
  if (state.timeMode === "rel") return (v - relBase()).toFixed(3) + " s";
  const d = new Date(v * 1000);
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${ms}`;
}

// Window the x axis to the last `window` (seconds for host/rel, ms for tick), anchored at
// the newest sample, so both live and frozen charts show a fixed-width strip.
function xRangeFor(chart) {
  return (u, dmin, dmax) => {
    if (!Number.isFinite(dmax)) return [0, 1];
    const span = state.timeMode === "tick" ? chart.window * 1000 : chart.window;
    return [dmax - span, dmax];
  };
}

function buildUplot(chart) {
  if (chart.uplot) { chart.uplot.destroy(); chart.uplot = null; }
  const w = chart.canvasEl.clientWidth;
  if (w <= 0 || !chart.names.length) return;
  const col = plotColors();
  // Each channel gets its own auto-ranged y scale, so wildly different magnitudes (a
  // 0..65535 ramp next to a +-1 float) each use the full height instead of one flattening
  // the others. The y axis is therefore ambiguous and left undrawn; the legend carries the
  // real values with units.
  // Stepped paths: hold each value constant until the next sample (no linear interpolation
  // between points), which reads truer for slow/irregular signals.
  if (!stepPath) stepPath = uPlot.paths.stepped({ align: 1 });
  const series = [{ value: fmtPlotX }];
  const scales = { x: { time: false, range: xRangeFor(chart) } };
  chart.names.forEach((name, i) => {
    const unit = chart.unit.get(name);
    const skey = "y" + i;
    scales[skey] = { auto: true };
    series.push({
      label: unit ? `${name} (${unit})` : name,
      stroke: colorFor(name, i),
      show: chart.show.get(name),
      width: 1.5,
      spanGaps: false,
      scale: skey,
      paths: stepPath,
      value: (u, v) => fmtPlotVal(v, chart.isInt.get(name)),
    });
  });
  const xaxis = {
    stroke: col.label, grid: { stroke: col.grid, width: 1 },
    ticks: { stroke: col.grid }, values: xAxisValues,
  };
  const opts = {
    width: w, height: 150,
    scales,
    axes: [xaxis],                         // x only; per-series y scales are undrawn
    series,
    // Linked cursor across every chart: since all charts share one time base, hovering one
    // draws the cursor on all of them at the same x (SPEC 9.2 "synchronized cursor").
    cursor: { drag: { x: false, y: false }, sync: { key: "plots", scales: ["x", null] } },
    legend: { live: true },
  };
  chart.uplot = new uPlot(opts, currentData(chart), chart.canvasEl);
  plotTheme = root.getAttribute("data-theme") || "";
}

// First index in [0, total) whose x is >= xmin, via binary search. `total` bounds the search so
// a paused chart (frozenLen) never looks past its freeze point.
function firstAtOrAfter(xs, xmin, total) {
  let a = 0, b = total - 1, res = total;
  while (a <= b) { const m = (a + b) >> 1; if (xs[m] >= xmin) { res = m; b = m - 1; } else a = m + 1; }
  return res;
}

function currentData(chart) {
  // host and rel share the host-time array (rel only shifts the display labels); tick uses
  // the MCU-tick array. Keeping data monotonic and shifting only labels avoids re-scaling.
  const xsAll = state.timeMode === "tick" ? chart.xsTick : chart.xsHost;
  const total = chart.frozenLen === null ? xsAll.length : chart.frozenLen;   // paused: up to the freeze
  if (total === 0) return [[], ...chart.names.map(() => [])];
  // Only ship the visible window (plus a one-sample left margin) to uPlot. The capped arrays hold
  // up to PLOT_CAP points but at most a screenful is visible; mirroring the digital panel's
  // visibleRange, binary-search the left edge so setData copies O(visible), not O(history). The
  // newest sample (index total-1) is always included, so xRangeFor still anchors [dmax-span, dmax]
  // exactly - follow/anchor and the freeze slice are unchanged, only the off-screen tail is dropped.
  const span = state.timeMode === "tick" ? chart.window * 1000 : chart.window;
  const xmax = xsAll[total - 1];
  let lo = firstAtOrAfter(xsAll, xmax - span, total);
  if (lo > 0) lo -= 1;   // include the sample just left of the window so the stepped path holds across the edge
  return [xsAll.slice(lo, total), ...chart.names.map((nm) => chart.ys.get(nm).slice(lo, total))];
}

// Repaint each chart's visible window. Paused charts are not skipped: they still honour
// user actions (window, x-axis, pause/resume) via the dirty flag, but currentData clamps
// them to the frozen slice so no new samples appear until resumed. Returns whether any
// chart actually changed, so the caller can skip re-projecting the shared cursor when idle.
function redrawPlots() {
  const themeNow = root.getAttribute("data-theme") || "";
  // Snapshot the theme comparison before the loop. buildUplot() assigns plotTheme itself,
  // so testing `themeNow !== plotTheme` per iteration meant the first chart rebuilt, set
  // plotTheme, and every later chart then compared equal and kept the old palette for
  // good - a theme toggle recoloured exactly one chart.
  const themeChanged = themeNow !== plotTheme;
  let changed = false;
  for (const chart of charts.values()) {
    const w = chart.canvasEl.clientWidth;
    if (w <= 0) continue;   // section hidden or chart collapsed; nothing to draw
    const need = !chart.uplot
      || chart.uplot.series.length - 1 !== chart.names.length
      || themeChanged;
    if (need) { buildUplot(chart); changed = true; continue; }
    if (chart.uplot.width !== w) { chart.uplot.setSize({ width: w, height: 150 }); changed = true; }
    if (chart.dirty) { chart.uplot.setData(currentData(chart)); chart.dirty = false; changed = true; }
  }
  return changed;
}

function resizePlots() {
  for (const chart of charts.values()) {
    const w = chart.canvasEl.clientWidth;
    if (chart.uplot && w > 0 && chart.uplot.width !== w) {
      chart.uplot.setSize({ width: w, height: 150 });
    }
  }
}

// Drive every plot's cursor to the time of the hovered terminal line, so scrubbing the log
// reads the plotted values at that instant.
//
// The hovered line is an identity: a `row` object with a fixed timestamp, pinned until the
// human genuinely moves the pointer onto a different line. New data, virtualized re-renders
// (replaceChildren) and autoscroll must NEVER re-point it - resolving "which row is under the
// pixel" on every new line is what made the cursor beat between two neighbouring samples. So
// there is one writer (a real mousemove) and one idempotent projector that the redraw loop can
// re-run as often as it likes.
let hoverRow = null;
let lastPx = -1, lastPy = -1;
let cursorShown = false;   // whether the shared cursor is currently drawn; gates idle clearHoverCursor churn
let hoverRaf = 0;          // pending rAF for the elementFromPoint hit-test (one per frame max)

function resolveRowAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const ln = el && el.closest ? el.closest(".ln") : null;
  return ln && ln.__row ? ln.__row : null;
}

// The only writer of hoverRow. Gated on real pointer movement, so the synthetic mouseover/
// re-layout that fires when data scrolls under a still pointer can never re-point the line.
// The elementFromPoint hit-test forces a synchronous layout, so coalesce a fast pointer
// stream to one resolve per displayed frame.
function paneMouseMove(e) {
  if (e.clientX === lastPx && e.clientY === lastPy) return;
  lastPx = e.clientX; lastPy = e.clientY;
  if (hoverRaf) return;
  hoverRaf = requestAnimationFrame(() => {
    hoverRaf = 0;
    if (lastPx < 0) return;   // pointer left the pane while this frame was pending
    const row = resolveRowAt(lastPx, lastPy);
    if (row !== hoverRow) { hoverRow = row; applyHoverCursor(); }
  });
}

function paneMouseLeave() {
  lastPx = lastPy = -1;
  hoverRow = null;
  clearHoverCursor();
}

function xForRow(row) {
  if (state.timeMode === "tick") { const t = lineTick(row); return t == null ? null : t; }
  return row.ts;   // host and rel are both drawn on the host-time array
}

// The time value the shared cursor should sit at right now, or null when nothing is hovered.
// Priority: a terminal-row hover wins; else a digital-panel hover; else an analog-chart hover.
function hoverXVal() {
  if (hoverRow) return xForRow(hoverRow);
  const dx = getDigitalCursorX();
  return dx != null ? dx : getChartHoverX();
}

let lastHoverX = null;   // last x applyHoverCursor projected; lets the redraw loop skip idle re-applies

// Idempotent projection of the single pinned row onto every chart. No hit-test and no hoverRow
// mutation, so the 200 ms redraw loop can re-apply it freely (the row/ts is fixed; only the
// window pans, moving valToPos smoothly with zero flicker).
function applyHoverCursor() {
  const xval = hoverXVal();
  lastHoverX = xval;
  if (xval == null) {
    if (cursorShown) { clearHoverCursor(); cursorShown = false; }
    return;
  }
  // xval is in the same units the digital X() mapping uses (host seconds for host/rel, tick ms
  // for tick - the reference digitalRightEdge() returns), so a terminal hover snaps #dCursor too.
  setDigitalCursorAt(xval);
  for (const chart of charts.values()) {
    const u = chart.uplot;
    if (!u) continue;
    const sx = u.scales.x;
    const snap = (sx.min == null || xval < sx.min || xval > sx.max) ? null : nearestX(u.data[0], xval);
    // setCursor(opts, _fire, _pub): _pub=false so we do not re-publish through the cursor-sync
    // group (we set every chart ourselves). left off-canvas hides the cursor where the time is
    // outside that chart's window.
    if (snap == null) { u.setCursor({ left: -10, top: -10 }, false, false); continue; }
    u.setCursor({ left: u.valToPos(snap, "x"), top: (u.over.clientHeight || 100) / 2 }, false, false);
  }
  cursorShown = true;
}

function clearHoverCursor() {
  lastHoverX = null;
  for (const chart of charts.values()) {
    if (chart.uplot) chart.uplot.setCursor({ left: -10, top: -10 }, false, false);
  }
  $("dCursor").hidden = true;   // hide the digital cursor together with the analog cursors
  refreshDigitalReadouts();     // snap the gutter readouts back to the live/frozen edge value
}


function setChartPaused(chart, paused) {
  if (chart.paused === paused) return;
  chart.paused = paused;
  chart.frozenLen = paused ? chart.xsHost.length : null;   // freeze at this sample
  if (chart.pauseBtn) {
    chart.pauseBtn.textContent = paused ? "resume" : "pause";
    chart.pauseBtn.classList.toggle("on", paused);
  }
  if (chart.pausedTag) chart.pausedTag.hidden = !paused;
  chart.dirty = true;
  hooks.liveChanged();
}

function exportChart(chart) {
  const names = chart.names.filter((n) => chart.show.get(n));
  downloadCsv(names, chart.window * 1000, chart.sid === null ? "long" : "wide", `plot-${chart.key}.csv`);
}

function redrawTick() {
  const plotsChanged = redrawPlots();
  const digitalChanged = redrawDigital();
  // Re-project the shared cursor only when something actually moved: a chart/lane repainted
  // under it, or the hovered time itself changed. Idle (no data, no hover) ticks cost nothing.
  if (plotsChanged || digitalChanged || hoverXVal() !== lastHoverX) applyHoverCursor();
}

function initPlots() {
  // The time base is driven by the shared #timeSeg control (see setTimeMode).
  buildDigitalHead();
  initDigitalCursorSync();
  setInterval(() => {
    // A hidden tab draws nothing: data still ingests, and the first visible tick repaints.
    if (document.hidden) return;
    // Plots are hidden in the "can" view; switching back triggers a redraw via setView/resizePlots.
    if (sidebar.getAttribute("data-view") === "can") return;
    redrawTick();
  }, PLOT_REDRAW_MS);
  document.addEventListener("visibilitychange", () => {
    // Repaint immediately on return instead of waiting out the next timer tick.
    if (!document.hidden && sidebar.getAttribute("data-view") !== "can") redrawTick();
  });
}

// Clear the analog charts (see terminal.js clear-all): destroy each uPlot, drop the DOM,
// and restore the empty state.
export function clearAllCharts() {
    for (const chart of charts.values()) {
      if (chart.uplot) chart.uplot.destroy();
      if (chart.el) chart.el.remove();
    }
    charts.clear();
    plotChannelMeta.clear();
    channelCapWarned = false;
    updatePlotCount();
    const pc = $("plotCharts");
    if (!pc.querySelector(".empty-state")) {
      const e = document.createElement("div");
      e.className = "empty-state";
      e.textContent = "No plot data yet. !p / !pd / !ps events stream live into strip charts here.";
      pc.appendChild(e);
    }
}

export { charts, plotIngest, resizePlots, setChartPaused, paneMouseMove, paneMouseLeave,
         applyHoverCursor, initPlots };
