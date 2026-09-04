import { $, root, pad2, state, hooks, downloadCsv, nearestX, lineTick, sidebar, isDecimalToken,
         PLOT_CAP, PLOT_SLACK } from "./state.js";
import { buildWindowButtons, colorFor, openColorPicker, rgbToHex, saveColor,
         PLOT_WINDOW_DEFAULT } from "./chrome.js";
import { firstAtOrAfter, spanFor, fmtTime } from "./timewindow.js";
import { bornPaused, freezeChanged, minWatermark, registerSurface } from "./freeze.js";
import { digitalIngest, digitalLanes, setDigitalCursorAt, refreshDigitalReadouts, getDigitalCursorX,
         getChartHoverX, buildDigitalHead, initDigitalCursorSync, markDigitalDirty,
         redrawDigital, makeSpanButton } from "./digital.js";

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
// state.js lineTick needs a !ps line's tick, and must accept exactly the lines this
// module's decoder accepts. It cannot import this module (it is the dependency leaf), so
// the decoder is published through hooks rather than copied there: a hand-written mirror
// dropped a clause twice. Returns null for anything decodePlotSample rejects.
hooks.plotSampleTick = (port, raw) => {
  const sid = raw.trim().split(/\s+/)[1];
  const def = plotDefs.get(port + "|" + sid);
  if (!def) return null;
  const sample = decodePlotSample(raw, def);
  return sample ? sample.tick : null;
};
// Highest line id each chart already holds from the /plot/series history seed (api.js).
// The /lines backfill and the live stream both replay those lines, so without this every
// seeded sample would be ingested a second time.
const seedMaxId = new Map();    // chart key -> highest line id the seed ingested
const charts = new Map();       // chart key ("s0" | "adhoc") -> chart object
// The theme each chart was last BUILT for is stamped per chart (chart.theme), not held once
// for all of them: a chart with no width is skipped by the redraw loop entirely, so a shared
// stamp advanced while it was collapsed and it kept the old palette after expanding, until
// some unrelated rebuild happened to come along.
let stepPath = null;            // shared uPlot stepped-path builder (lazy: needs uPlot loaded)

// -- client-side decode (mirror of protocol.py plot helpers) --
// Plot value / scale grammar (SPEC 2.5); mirrors protocol.parse_plot_value. The exponent
// is accepted because firmware with float printf emits it unprompted ("%g" -> 1.2e-05).
// Number.isFinite rejects an in-grammar literal that overflows ("1e999" -> Infinity).
function parsePlotValue(s) {
  if (!/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(s)) return null;
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : null;
}

function parsePlotAdhoc(raw) {
  const parts = raw.trim().split(/\s+/);
  if (parts.length < 3 || parts[0] !== "!p") return null;
  if (!isDecimalToken(parts[1]) || +parts[1] > 0xFFFFFFFF) return null;
  const points = [];
  const seen = new Set();
  for (const pair of parts.slice(2)) {
    const eq = pair.indexOf("=");
    if (eq < 1) return null;
    const name = pair.slice(0, eq), val = parsePlotValue(pair.slice(eq + 1));
    if (name.length > 16 || !PLOT_NAME_RE.test(name) || val === null) return null;
    // SPEC 2.5: names are unique within one line. A repeat pushes two values into one chart's
    // y array against a single x, so that channel is misaligned against the chart's x array
    // for good (and outgrows it past the block trim). Mirrors protocol.parse_plot_adhoc.
    if (seen.has(name)) return null;
    seen.add(name);
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
    // A *scale is meaningless on an enum/bits channel and makes the whole !pd line invalid
    // (SPEC 2.5), so the daemon stores nothing for that stream. Accepting it here was worse
    // than useless: the panel drew lanes for a stream that exists only in the browser, and
    // /plot/export or a page reload showed nothing. Reject exactly as _parse_channel_spec does.
    if (scale !== null) return null;
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
    // The regex bounds the character set but not the count, and the daemon rejects the whole
    // definition past this many digits. The sign is not a digit, hence the strip. Without the
    // cap the browser charted a typed stream the daemon never decoded, so the UI and
    // `mcu plot` disagreed about the same !pd.
    if (!isDecimalToken(valStr.replace("-", ""))) return null;
    // Reject on the sign CHARACTER, not on the value: monitor.c rejects any '-' on an
    // unsigned channel, so "-0" (which is 0, and passes a `v < 0` test) has to go too.
    // Mirrors protocol._parse_enum_labels; the value test let the browser build a lane for
    // a stream the daemon stored as a generic event and never exported.
    if (!signed && valStr.startsWith("-")) return null;
    const v = Number(valStr);
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
  // SPEC 2.5: channel names and bit lane names share one namespace and must be unique across
  // the whole definition, so this index cannot be last-writer-wins. A lane named after an
  // analog channel silently reclassified that channel's points as digital. Mirrors
  // protocol.parse_plot_def.
  const byName = new Map();
  for (const c of channels) {
    if (byName.has(c.name)) return null;
    byName.set(c.name, c);
    if (c.lanes) {
      for (const ln of c.lanes) {
        if (ln === null) continue;
        if (byName.has(ln)) return null;
        byName.set(ln, c);
      }
    }
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
      // Re-check after scaling: decodePlotField rejects non-finite samples, but a large
      // *scale factor can carry a finite sample to Infinity, and uPlot.rangeNum() then
      // returns [NaN, NaN] - silently erasing every series on the chart, which is exactly
      // what that earlier check exists to prevent.
      if (!Number.isFinite(v)) return null;
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
  const seeded = seedMaxId.get(key);
  if (seeded !== undefined && row.id <= seeded) return;   // already ingested by the history seed
  const x = { host: row.ts, tick: sample.tick };   // host seconds, MCU tick in ms
  routePoints(key, sample.sid, sample.points, x, unitFor);
}

// Route one decoded sample's points by channel kind: enum/bits go to the digital lanes,
// everything else (including every ad-hoc !p point, which carries no definition) to the
// analog chart. The one dispatcher for both the live decode and the history seed, so the
// two paths cannot disagree about which kinds are digital.
function routePoints(key, sid, points, x, def) {
  const digital = [], analog = [];
  for (const [name, val] of points) {
    const ch = def && def.byName.get(name);
    if (ch && (ch.kind === "enum" || ch.kind === "bits")) digital.push([name, val, ch]);
    else analog.push([name, val]);
  }
  if (analog.length) addSample(ensureChart(key, sid), analog, x, def);
  if (digital.length) digitalIngest(sid, digital, x);
}

function unitOf(def, name) {
  if (!def) return null;
  const c = def.byName.get(name);
  return c ? c.unit : null;
}

// -- history seed (SPEC 9.2: /plot/channels + /plot/series) --
//
// A page load used to discover channels from live traffic alone, so a reload showed empty
// charts until new samples arrived and a stream that had stopped never appeared at all,
// however much history the daemon held. api.js fetches that history; this puts it back into
// the shape the live decode produces, so charts and lanes are still built by one path.

// The join is `line_id`: /plot/series answers one channel at a time, every channel of a
// stream is decoded from the same `!ps` line, and a chart keeps ONE x array for all of its
// channels. Merging per line is what keeps a two-channel chart from pushing two x values per
// sample with each channel null where the other has a value.
function mergeSeedSeries(entries) {
  const rows = new Map();
  for (const { channel, points } of entries) {
    for (const pt of points) {
      if (!pt || typeof pt.line_id !== "number") continue;
      // This producer's own class-6 gate. protocol.decode_plot_sample does NOT re-check
      // finiteness after applying a *scale, so the daemon can store an Infinity that the
      // live decode here rejects - and one of those inside the window makes uPlot.rangeNum()
      // return [NaN, NaN] and blanks every series on the chart. Dropping the point leaves a
      // one-sample gap, which is what the live path's whole-sample reject leaves too.
      if (!Number.isFinite(pt.value)) continue;
      let row = rows.get(pt.line_id);
      if (!row) {
        row = { id: pt.line_id, x: { host: pt.ts, tick: pt.tick_ms }, points: new Map() };
        rows.set(pt.line_id, row);
      }
      // SPEC 2.5: names are unique within one line, and this producer must enforce it like
      // parsePlotAdhoc and parsePlotDef do. A capture written by a pre-0.2.1 daemon can hold
      // duplicate plot_points rows for one (line, name), and /plot/series (long) emits every
      // one of them: two entries under one name push two y values against a single x, so that
      // channel is misaligned against the chart's x array for the life of the page.
      // Last row wins, matching the daemon's wide-form collapse (server._csv_wide assigns
      // values[name] per row), so browser and CSV show the same value for a legacy capture.
      row.points.set(channel.name, pt.value);
    }
  }
  return [...rows.values()]
    .sort((a, b) => a.id - b.id)
    .map((r) => ({ id: r.id, x: r.x, points: [...r.points] }));
}

// Does a /plot/channels entry carry names the live path would have accepted? The live decode
// tests every channel, lane and group name against PLOT_NAME_RE; the seed path took them
// straight from the JSON, so a device-derived string reached a DOM id (digital.js builds
// "dgrp-" + group) with no grammar check at its own boundary. A failing entry is dropped,
// like every other malformed seed row.
function seedNameOk(channel) {
  const names = [channel.name];
  if (channel.kind === "bit" && channel.group) names.push(channel.group);
  return names.every((n) => typeof n === "string" && n.length <= 16 && PLOT_NAME_RE.test(n));
}

// A stream's /plot/channels metadata in the shape the live decoder's channel objects have,
// so unitOf, channelIsInt and addDigitalLane read it unchanged. A packed-bits channel is
// reported one entry per lane, each naming its parent group (protocol.channel_meta), and
// digital.js expects that group in `name` - hence the swap, and "bit" -> "bits".
function seedDef(entries) {
  const byName = new Map();
  for (const { channel } of entries) {
    const bits = channel.kind === "bit";
    byName.set(channel.name, {
      name: bits ? (channel.group || channel.name) : channel.name,
      kind: bits ? "bits" : (channel.kind || "analog"),
      type: channel.type, scale: channel.scale, unit: channel.unit,
      labels: channel.labels || null,
    });
  }
  return { byName };
}

// Has anything already reached the surfaces this group of channels feeds? A stream can be
// digital-only, so the lanes are asked as well as the chart.
function seedTargetHasData(key, group) {
  const chart = charts.get(key);
  if (chart && chart.xsHost.length) return true;
  for (const { channel } of group) {
    const lane = digitalLanes.get(channel.name);
    if (lane && lane.vs.length) return true;
  }
  return false;
}

// Apply the fetched history. Each entry is one channel's /plot/channels metadata plus its
// /plot/series points. Nothing here touches a pause: samples go in through addSample and
// digitalIngest exactly as live ones do, and both hold their surface's freeze.
function plotSeed(entries) {
  const groups = new Map();   // chart key -> the entries feeding it
  for (const e of entries) {
    if (!e || !e.channel || !e.points || !e.points.length) continue;
    if (!seedNameOk(e.channel)) continue;
    // sid is NULL in the store for ad-hoc `!p` points, which share one chart (see plotIngest).
    const key = e.channel.sid == null ? "adhoc" : "s" + e.channel.sid;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }
  for (const [key, group] of groups) {
    // Per group, and per row below, as the live path is (api.js): one malformed seed row
    // must not abandon the rest, and one bad group must not cost the others.
    try { seedGroup(key, group); }
    catch (err) { console.error("plot history seed: a group was dropped:", err); }
  }
}

function seedGroup(key, group) {
  const sid = key === "adhoc" ? null : group[0].channel.sid;
  const def = key === "adhoc" ? null : seedDef(group);   // ad-hoc carries no declaration
  // Only ever fill a surface that is still empty. The seeded samples are the older ones
  // and addSample keeps each chart's x strictly increasing by nudging anything that
  // arrives out of order, so once live samples have landed - a reconnect, or a capture
  // reset whose backfill is still in flight - a seed would stack the whole history just
  // past the live edge instead of behind it.
  if (seedTargetHasData(key, group)) return;
  let maxId = 0, bad = null;
  for (const row of mergeSeedSeries(group)) {
    try { routePoints(key, sid, row.points, row.x, def); }
    catch (err) { bad = err; continue; }
    if (row.id > maxId) maxId = row.id;
  }
  if (bad) console.error("plot history seed: some rows were dropped, last error:", bad);
  if (maxId) seedMaxId.set(key, maxId);
}

// -- chart data model + DOM --
function ensureChart(key, sid) {
  let chart = charts.get(key);
  if (chart) return chart;
  const empty = $("plotCharts").querySelector(".empty-state");
  if (empty) empty.remove();
  chart = {
    key, sid, xsHost: [], xsTick: [], lastHost: null, lastTick: null,
    names: [], ys: new Map(), unit: new Map(), show: new Map(), isInt: new Map(),
    window: PLOT_WINDOW_DEFAULT, paused: false, frozen: null, frozenMaxId: null,
    collapsed: false, uplot: null, dirty: false, theme: null,
  };
  buildChartDom(chart);
  charts.set(key, chart);
  // A chart appearing while the UI is frozen joins the freeze, so the first stream after a
  // clear-all does not start the plots moving under a "resume all" button.
  if (bornPaused()) setChartPaused(chart, true);
  return chart;
}

function addSample(chart, points, x, def) {
  // Keep BOTH x arrays strictly increasing (same reasoning as digitalIngest): uPlot needs an
  // ascending x, and currentData binary-searches whichever array the active time mode reads.
  // Host receive time arrives in TCP-batched bursts, so several samples share (or slightly
  // reorder) a timestamp. The MCU tick is NOT monotonic either: two !ps samples in the same
  // millisecond repeat a tick (anything above ~1 kHz), and SPEC 2.5 has it wrap at 2^32 - both
  // of which broke the binary search and left tick-mode charts drawing garbage.
  // The x arrays get the same gate the y values have (class 6): one non-finite value blanks
  // the series, and the monotonic bump below does not catch it, because `undefined <= n` is
  // false so a bad value passes through and then becomes lastHost. The y side gates at three
  // boundaries and the x side had none, which is only reachable from a malformed daemon or
  // proxy response rather than from device output - but each producer gates at its own
  // boundary, and "the response schema guarantees it" is the argument this class rejects.
  if (!Number.isFinite(x.host) || !Number.isFinite(x.tick)) return;
  let hx = x.host, tx = x.tick;
  if (chart.lastHost !== null && hx <= chart.lastHost) hx = chart.lastHost + 1e-4;
  if (chart.lastTick !== null && tx <= chart.lastTick) tx = chart.lastTick + 1e-4;
  chart.lastHost = hx;
  chart.lastTick = tx;
  chart.xsHost.push(hx);
  chart.xsTick.push(tx);
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
    // The freeze is a snapshot (chart.frozen), not an index into these arrays, so the trim
    // cannot reach it. An index had to be slid down by `drop` here, and once the whole ring
    // had rotated past the freeze that slide reached 0 and the paused chart blanked for good
    // (REVIEW class 26) - 100 s of a 1 kHz stream did it.
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

// The legend was a <label> wrapping a display:none checkbox plus a click-handled <span> swatch:
// neither is reachable by keyboard, so hiding a trace or recolouring one was mouse-only. The
// hidden checkbox is gone (it labelled nothing and only existed to be suppressed); the name and
// the swatch are now proper span-buttons, the same treatment digital.js gives its lane gutter.
function renderChans(chart) {
  const host = chart.chansEl;
  host.textContent = "";
  chart.names.forEach((name, i) => {
    const lab = document.createElement("div");
    lab.className = "chan";
    lab.classList.toggle("off", !chart.show.get(name));
    const sw = document.createElement("span");
    sw.className = "swatch"; sw.style.background = colorFor(name, i);
    sw.title = "Click to set colour";
    // Swatch: open a colour picker (does NOT toggle show). Live swatch feedback on input (cheap),
    // but persist + re-stroke the series only on commit (change fires once when the picker closes),
    // so dragging the picker does not thrash a full uPlot destroy+recreate per tick.
    const pickColor = (e) => {
      if (e) { if (e.preventDefault) e.preventDefault(); if (e.stopPropagation) e.stopPropagation(); }
      openColorPicker(
        rgbToHex(colorFor(name, i)),
        (v) => { sw.style.background = v; },   // preview only, no rebuild
        (v) => {
          saveColor(name, v);
          sw.style.background = v;
          buildUplot(chart);   // rebuild once, to re-stroke the series in the committed colour
        },
      );
    };
    sw.addEventListener("click", pickColor);
    makeSpanButton(sw, `Set colour for ${name}`, pickColor);
    const txt = document.createElement("span"); txt.textContent = name;
    lab.append(sw, txt);
    const unit = chart.unit.get(name);
    if (unit) { const u = document.createElement("span"); u.className = "unit"; u.textContent = unit; lab.appendChild(u); }
    // Name (and the rest of the row): toggle the trace on/off. The click stays on the container so
    // the unit and the gaps remain clickable; keyboard activation is wired to the name span only,
    // which is what carries the focus and the aria-pressed state.
    const toggle = () => {
      const on = !chart.show.get(name);
      chart.show.set(name, on);
      lab.classList.toggle("off", !on);
      txt.setAttribute("aria-pressed", on ? "true" : "false");
      if (chart.uplot) chart.uplot.setSeries(i + 1, { show: on });
    };
    lab.addEventListener("click", (e) => { e.preventDefault(); toggle(); });
    makeSpanButton(txt, `Toggle channel ${name}`, toggle);
    txt.setAttribute("aria-pressed", chart.show.get(name) ? "true" : "false");
    txt.title = "Click to show / hide this trace";
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
function fmtPlotX(u, v) { return fmtTime(state, v); }

// Window the x axis to the last `window` (seconds for host/rel, ms for tick), anchored at
// the newest sample, so both live and frozen charts show a fixed-width strip.
function xRangeFor(chart) {
  return (u, dmin, dmax) => {
    if (!Number.isFinite(dmax)) return [0, 1];
    const span = spanFor(state.timeMode, chart.window);
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
  chart.theme = root.getAttribute("data-theme") || "";
}

// The arrays a draw must consume: the pause-time snapshot while frozen, the live rings
// otherwise. The one seam, so a paused chart can never be re-derived from rings that kept
// filling (they do, deliberately, for the resume catch-up).
function chartDrawData(chart) {
  return chart.paused && chart.frozen ? chart.frozen : chart;
}

function currentData(chart) {
  // host and rel share the host-time array (rel only shifts the display labels); tick uses
  // the MCU-tick array. Keeping data monotonic and shifting only labels avoids re-scaling.
  const src = chartDrawData(chart);
  const xsAll = state.timeMode === "tick" ? src.xsTick : src.xsHost;
  const total = xsAll.length;
  if (total === 0) return [[], ...chart.names.map(() => [])];
  // Only ship the visible window (plus a one-sample left margin) to uPlot. The capped arrays hold
  // up to PLOT_CAP points but at most a screenful is visible; mirroring the digital panel's
  // visibleRange, binary-search the left edge so setData copies O(visible), not O(history). The
  // newest sample (index total-1) is always included, so xRangeFor still anchors [dmax-span, dmax]
  // exactly - follow/anchor and the freeze slice are unchanged, only the off-screen tail is dropped.
  const span = spanFor(state.timeMode, chart.window);
  const xmax = xsAll[total - 1];
  let lo = firstAtOrAfter(xsAll, xmax - span, total);
  if (lo > 0) lo -= 1;   // include the sample just left of the window so the stepped path holds across the edge
  // A channel first seen after the pause holds nothing the freeze covers, so it draws as a
  // gap rather than borrowing another series' length (uPlot needs every array equal-length).
  return [xsAll.slice(lo, total), ...chart.names.map((nm) => {
    const arr = src.ys.get(nm);
    return arr ? arr.slice(lo, total) : new Array(total - lo).fill(null);
  })];
}

// Repaint each chart's visible window. Paused charts are not skipped: they still honour
// user actions (window, x-axis, pause/resume) via the dirty flag, but currentData clamps
// them to the frozen slice so no new samples appear until resumed. Returns whether any
// chart actually changed, so the caller can skip re-projecting the shared cursor when idle.
function redrawPlots() {
  const themeNow = root.getAttribute("data-theme") || "";
  let changed = false;
  // Every width read before the first setSize/setData: interleaving them forces a synchronous
  // layout per chart, every 200 ms.
  for (const [chart, w] of [...charts.values()].map((c) => [c, c.canvasEl.clientWidth])) {
    if (w <= 0) continue;   // section hidden or chart collapsed; nothing to draw
    const need = !chart.uplot
      || chart.uplot.series.length - 1 !== chart.names.length
      || chart.theme !== themeNow;
    if (need) { buildUplot(chart); changed = true; continue; }
    if (chart.uplot.width !== w) { chart.uplot.setSize({ width: w, height: 150 }); changed = true; }
    if (chart.dirty) { chart.uplot.setData(currentData(chart)); chart.dirty = false; changed = true; }
  }
  return changed;
}

function resizePlots() {
  // Reads first, writes second, for the same reason as redrawPlots above.
  for (const [chart, w] of [...charts.values()].map((c) => [c, c.canvasEl.clientWidth])) {
    if (chart.uplot && w > 0 && chart.uplot.width !== w) {
      chart.uplot.setSize({ width: w, height: 150 });
    }
  }
}

// Coalesce resize-driven redraws into one per frame: a window resize or a divider drag
// delivers events far faster than the display refreshes, and uPlot.setSize plus a full lane
// repaint is the expensive half. Shared by app.js (dividers) and terminal.js (window resize).
let resizeRaf = 0;
// Whatever caches a measured size registers here. The terminal's per-pane scrollback
// height is the one caller: it was invalidated on window.resize alone, but a divider drag
// and a wrapping toolbar change pane height without any resize event, and a stale-small
// cache renders too few rows and leaves a blank strip below the last one.
const resizeHooks = [];
function onResizeRedraw(fn) { resizeHooks.push(fn); }

function scheduleResizeRedraw() {
  if (resizeRaf) return;
  resizeRaf = requestAnimationFrame(() => {
    resizeRaf = 0;
    for (const fn of resizeHooks) fn();
    resizePlots();
    markDigitalDirty();
  });
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
  // Snapshot what the freeze covers, and serve that to every paused draw (chartDrawData).
  // The live arrays keep filling while paused, for the resume catch-up, and an index into
  // them is not a freeze: the block trim slides it down one drop at a time until it reaches
  // zero and the paused chart blanks, unrecoverably (REVIEW class 26, the digital panel's
  // sibling). Bounded: the snapshot is what the ring held at pause, no more. Resume drops it
  // and the view returns to the live arrays, which kept every sample that arrived meanwhile.
  chart.frozen = paused
    ? { xsHost: chart.xsHost.slice(), xsTick: chart.xsTick.slice(),
        ys: new Map([...chart.ys].map(([nm, arr]) => [nm, arr.slice()])) }
    : null;
  // Line-id watermark for the export (terminal.js does the same with pane.frozenId). Exact at
  // this instant because rows arrive in id order; the sample arrays cannot supply it, since
  // addSample nudges colliding x values and keeps no per-sample id.
  chart.frozenMaxId = paused ? state.maxId : null;
  if (chart.pauseBtn) {
    chart.pauseBtn.textContent = paused ? "resume" : "pause";
    chart.pauseBtn.classList.toggle("on", paused);
  }
  if (chart.pausedTag) chart.pausedTag.hidden = !paused;
  chart.dirty = true;
  freezeChanged();
}

registerSurface("charts", {
  isLive: () => [...charts.values()].some((c) => !c.paused),
  setPaused: (paused) => charts.forEach((c) => setChartPaused(c, paused)),
  watermark: () => minWatermark([...charts.values()].filter((c) => c.paused).map((c) => c.frozenMaxId)),
});


function exportChart(chart) {
  const names = chart.names.filter((n) => chart.show.get(n));
  // A paused chart exports its frozen window, not the last N seconds up to now.
  downloadCsv(names, chart.window * 1000, chart.sid === null ? "long" : "wide",
              `plot-${chart.key}.csv`, chart.paused ? chart.frozenMaxId : null);
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
    seedMaxId.clear();   // the ids it holds describe charts that no longer exist
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

// The three grammar parsers are exported for the shared plot-grammar fixture
// (tests/plot_grammar_cases.json), which drives them and protocol.py over one case list.
// The mirror is hand-written in seven places and has drifted twice; nothing else in the app
// calls them from outside this module.
export { parsePlotDef, parsePlotAdhoc, decodePlotSample };

export { charts, plotIngest, plotSeed, resizePlots, scheduleResizeRedraw, onResizeRedraw,
         setChartPaused, redrawPlots, chartDrawData,
         exportChart, paneMouseMove, paneMouseLeave, applyHoverCursor, initPlots };
