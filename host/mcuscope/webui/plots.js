import { $, root, state, hooks, nearestX, lineTick, tickAnchors, sidebar, isDecimalToken, portColor,
         splitTokens, PLOT_CAP, PLOT_SLACK } from "./state.js";
import { openExportDialog, plotDecodeOptions, plotExportPath } from "./exportdlg.js";
import { buildWindowButtons, colorFor, dropWindowButtons, exitZoom, groupWindow, onZoomControls,
         openColorPicker, paintWindowGroups, rgbToHex, saveColor, showZoom,
         soloShow } from "./chrome.js";
import { continueTick, decimateColumns, firstAtOrAfter, fitAxisTicks, fmtAxisTick, fmtZoomSpan,
         getZoom, setZoom, spanFor, fmtTime, tickOffsetAt, windowFor, zoomFor,
         estimateTickX, continueHost, hostEpochAt, hostX } from "./timewindow.js";
import { bornPaused, freezeChanged, pauseAll, registerSurface } from "./freeze.js";
import { belowFold, cleanTitle, parseTitles, TITLES_KEY } from "./layout.js";
import { digitalIngest, digitalLanes, laneKey, setDigitalCursorAt, refreshDigitalReadouts,
         getDigitalCursorX, getChartHoverX, buildDigitalHead, initDigitalCursorSync, markDigitalDirty,
         onLanesChanged, onSeedBump, redrawDigital, makeSpanButton, setLanePortTags,
         tickClocks, hostClock } from "./digital.js";

// ---- realtime plots (sidebar): uPlot strip charts, one per stream (SPEC 9.2) --------
//
// Fed from the same rows as the terminal (backfill + the one /ws), decoded client-side
// the way the daemon decodes them: !pd caches a per-(port,sid) definition, !ps decodes
// against it, !p is ad-hoc. Each (port, stream) gets one chart, a port's ad-hoc channels share one;
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
  const def = plotDefs.get(port + "|" + splitTokens(raw)[1]);
  if (!def) return null;
  const sample = decodeOnce(raw, def);
  return sample ? sample.tick : null;
};
hooks.adhocTick = (raw) => {
  const sample = adhocOnce(raw);
  return sample ? sample.tick : null;
};

// pushBuffer asks for a !ps or !p line's tick (lineTick) just before plotIngest decodes the same
// row, so the last decode is kept: both calls pass the same raw text (and, for !ps, the same
// cached definition). The results are read, never mutated.
let lastDecode = { raw: null, def: null, sample: null };
function decodeOnce(raw, def) {
  if (lastDecode.raw !== raw || lastDecode.def !== def) {
    lastDecode = { raw, def, sample: decodePlotSample(raw, def) };
  }
  return lastDecode.sample;
}
let lastAdhoc = { raw: null, sample: null };
function adhocOnce(raw) {
  if (lastAdhoc.raw !== raw) lastAdhoc = { raw, sample: parsePlotAdhoc(raw) };
  return lastAdhoc.sample;
}
// Highest line id each chart already holds from the /plot/series history seed (api.js).
// The /lines backfill and the live stream both replay those lines, so without this every
// seeded sample would be ingested a second time.
const seedMaxId = new Map();    // chart key -> highest line id the seed ingested
const charts = new Map();       // chart key ("<port>|s0" | "<port>|adhoc") -> chart object

// Sids and channel names are unique only within a port (SPEC 9.2), so a chart is keyed by
// port as plotDefs and the CAN rows are. Keyed by sid alone, two boards declaring stream 0
// interleaved into one zigzag trace that read as a hardware fault.
function chartKey(port, sid) { return port + "|" + (sid === null ? "adhoc" : "s" + sid); }
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
  const parts = splitTokens(raw);
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
  const parts = splitTokens(raw);
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
  // Big-endian. A non-finite float is returned as is: decodePlotSample drops that point.
  if (isFloat) return new DataView(bytes.buffer).getFloat32(0, false);
  let v = 0;
  for (let i = 0; i < w; i++) v = v * 256 + bytes[i];
  if (signed && (bytes[0] & 0x80)) v -= 2 ** (w * 8);
  return v;
}

function decodePlotSample(raw, def) {
  const parts = splitTokens(raw);
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
      // The one finiteness check, after the scale: an f4 NaN or infinity, or a finite value a
      // large *scale carries to infinity, drops this point and keeps the rest of the sample,
      // as protocol.decode_plot_sample does (SPEC 2.5). One inside the window would make
      // uPlot.rangeNum() return [NaN, NaN] and blank every series on the chart. Enum and bits
      // channels are integer types, so they never carry one.
      if (!Number.isFinite(v)) continue;
      points.push([ch.name, v]);
    }
  }
  // Nothing finite left: the line stays a generic event, as protocol.decode_plot_sample has it.
  return points.length ? { tick, sid: def.sid, points } : null;
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
  let sample = null, unitFor = null;
  if (raw.startsWith("!ps")) {
    const sid = splitTokens(raw)[1];
    const def = plotDefs.get(port + "|" + sid);
    if (def) { sample = decodeOnce(raw, def); if (sample) unitFor = def; }
  } else if (raw.startsWith("!p")) {
    sample = adhocOnce(raw);
  } else return;
  if (!sample) return;
  const key = chartKey(port, sample.sid);
  const seeded = seedMaxId.get(key);
  if (seeded !== undefined && row.id <= seeded) return;   // already ingested by the history seed
  const x = { host: row.ts, tick: sample.tick, id: row.id };   // host seconds, MCU tick in ms
  routePoints(key, port, sample.sid, sample.points, x, unitFor);
}

// Route one decoded sample's points by channel kind: enum/bits go to the digital lanes,
// everything else (including every ad-hoc !p point, which carries no definition) to the
// analog chart. The one dispatcher for both the live decode and the history seed, so the
// two paths cannot disagree about which kinds are digital.
function routePoints(key, port, sid, points, x, def) {
  // Every member draws the host time past any backward clock step (timewindow.continueHost).
  x = { ...x, host: continueHost(hostClock, x.id, x.host) };
  const digital = [], analog = [];
  for (const [name, val] of points) {
    const ch = def && def.byName.get(name);
    if (ch && (ch.kind === "enum" || ch.kind === "bits")) digital.push([name, val, ch]);
    else analog.push([name, val]);
  }
  if (analog.length) addSample(ensureChart(key, port, sid), analog, x, def);
  if (digital.length) digitalIngest(port, digital, x, key);
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
      // This producer's own class-6 gate: a JSON `null` (or a malformed response) is not a
      // number, and one non-finite value inside the window blanks every series on the chart.
      // The point is dropped, as the live decode drops a non-finite one.
      if (!Number.isFinite(pt.value)) continue;
      let row = rows.get(pt.line_id);
      if (!row) {
        row = { id: pt.line_id, x: { host: pt.ts, tick: pt.tick_ms, id: pt.line_id }, points: new Map() };
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
function seedTargetHasData(key, port, group) {
  const chart = charts.get(key);
  if (chart && chart.xsHost.length) return true;
  for (const { channel } of group) {
    const lane = digitalLanes.get(laneKey(port, channel.name));
    if (lane && lane.vs.length) return true;
  }
  return false;
}

// Apply the fetched history. Each entry is one channel's /plot/channels metadata plus its
// /plot/series points. Nothing here touches a pause: samples go in through addSample and
// digitalIngest exactly as live ones do, and both hold their surface's freeze.
// `rows` is the backfill the seed lands under, oldest first: plotIngest caches its `!pd` rows
// only after the seed, so the field order is parsed from the newest of them here.
function plotSeed(entries, rows = []) {
  const fresh = new Map();   // "port|sid" -> the newest definition among `rows`
  for (const row of rows) {
    if (!row || row.chan !== "event" || typeof row.raw !== "string" || !row.raw.startsWith("!pd")) continue;
    const def = parsePlotDef(row.raw);
    if (def) fresh.set((row.port || "-") + "|" + def.sid, def);
  }
  const groups = new Map();   // chart key -> the entries feeding it
  for (const e of entries) {
    if (!e || !e.channel || !e.points || !e.points.length) continue;
    if (!seedNameOk(e.channel)) continue;
    // sid is NULL in the store for ad-hoc `!p` points, which share one chart (see plotIngest).
    // Each entry is one (port, name): api.js lists channels per port once two are in play.
    const key = chartKey(seedPort(e.channel), e.channel.sid == null ? null : String(e.channel.sid));
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }
  for (const [key, group] of groups) {
    // Per group, and per row below, as the live path is (api.js): one malformed seed row
    // must not abandon the rest, and one bad group must not cost the others.
    try { seedGroup(key, group, fresh); }
    catch (err) { console.error("plot history seed: a group was dropped:", err); }
  }
}

function seedPort(channel) { return typeof channel.port === "string" && channel.port ? channel.port : "-"; }

function seedGroup(key, group, fresh) {
  const port = seedPort(group[0].channel);
  const adhoc = group[0].channel.sid == null;
  const sid = adhoc ? null : String(group[0].channel.sid);
  const def = adhoc ? null : seedDef(group);   // ad-hoc carries no declaration
  // Chips, lanes and their palette slots follow the `!pd` field order, as the live decode meets
  // them; /plot/channels lists by name. A name the definition lacks goes last.
  const declared = adhoc ? null : fresh.get(port + "|" + sid) || plotDefs.get(port + "|" + sid);
  if (declared) {
    const order = [...declared.byName.keys()];
    const at = (e) => { const i = order.indexOf(e.channel.name); return i < 0 ? order.length : i; };
    group.sort((a, b) => at(a) - at(b));
  }
  // Only ever fill a surface that is still empty. The seeded samples are the older ones
  // and addSample keeps each chart's x strictly increasing by nudging anything that
  // arrives out of order, so once live samples have landed - a reconnect, or a capture
  // reset whose backfill is still in flight - a seed would stack the whole history just
  // past the live edge instead of behind it.
  if (seedTargetHasData(key, port, group)) return;
  let maxId = 0, bad = null;
  for (const row of mergeSeedSeries(group)) {
    try { routePoints(key, port, sid, row.points, row.x, def); }
    catch (err) { bad = err; continue; }
    if (row.id > maxId) maxId = row.id;
  }
  if (bad) console.error("plot history seed: some rows were dropped, last error:", bad);
  if (maxId) seedMaxId.set(key, maxId);
}

// -- chart data model + DOM --
function ensureChart(key, port, sid) {
  let chart = charts.get(key);
  if (chart) return chart;
  chart = {
    key, port, sid, xsHost: [], xsTick: [], ids: [], lastHost: null, lastTick: null, prevTick: null,
    names: [], ys: new Map(), unit: new Map(), show: new Map(), isInt: new Map(),
    window: groupWindow(), paused: false, frozen: null, frozenMaxId: null,
    collapsed: false, uplot: null, dirty: false, theme: null,
  };
  buildChartDom(chart);
  charts.set(key, chart);
  // A chart appearing while the UI is frozen joins the freeze, so the first stream after a
  // clear-all does not start the plots moving under a "resume all" button.
  if (bornPaused()) setChartPaused(chart, true);
  else freezeChanged();   // a chart born live makes a "resume all" label wrong
  syncPlotsChrome();
  return chart;
}

// ---- what the Plots section says about its widgets as a whole -----------------------
//
// Runs whenever a chart or a lane is created or cleared: the empty state, the gesture hint,
// and the port tags, which show only once more than one port has contributed.
let multiPort = false;
function syncPlotsChrome() {
  const any = charts.size + digitalLanes.size > 0;
  $("plotEmpty").hidden = any;
  $("plotHint").hidden = !any;
  const ports = new Set([...charts.values()].map((c) => c.port));
  for (const l of digitalLanes.values()) ports.add(l.port);
  const multi = ports.size > 1;
  if (multi === multiPort) return;
  multiPort = multi;
  for (const c of charts.values()) syncChartTitle(c);
  setLanePortTags(multi);
}
onLanesChanged(syncPlotsChrome);
onSeedBump(bumpSeedGen);   // a digital-only clear invalidates a seed in flight (see plotSeedGen)

// Per-browser chart titles (SPEC 2.5 declares no stream name), keyed by chart key so a
// board's rename survives a reload and a detach, and does not leak onto another board.
const plotTitles = (() => {
  try { return parseTitles(localStorage.getItem(TITLES_KEY)); } catch { return parseTitles(null); }
})();

function defaultTitle(chart) { return chart.sid === null ? "ad-hoc (!p)" : "stream " + chart.sid; }
function chartTitle(chart) { return plotTitles[chart.key] || defaultTitle(chart); }

// Set (or with empty text, drop) a chart's custom title.
function renameChart(chart, text) {
  const t = cleanTitle(text);
  if (t && t !== defaultTitle(chart)) plotTitles[chart.key] = t;
  else delete plotTitles[chart.key];
  try { localStorage.setItem(TITLES_KEY, JSON.stringify(plotTitles)); } catch { /* private mode */ }
  syncChartTitle(chart);
  syncFoldCue();   // its tooltip names the charts below the fold
}

// The head's title, port tag and, while collapsed, the shown channel names: collapse exists
// so several streams fit, and a bare "stream 0" hides what was collapsed.
function syncChartTitle(chart) {
  if (!chart.titleEl) return;
  chart.titleEl.textContent = chartTitle(chart);
  chart.portEl.hidden = !multiPort;
  chart.portEl.textContent = chart.port;
  chart.portEl.style.color = portColor(chart.port);
  const shown = chart.names.filter((n) => chart.show.get(n));
  chart.namesEl.hidden = !chart.collapsed || !shown.length;
  chart.namesEl.textContent = shown.join(", ");
  chart.namesEl.title = shown.join(", ");
}

function startRename(chart) {
  const input = document.createElement("input");
  input.className = "mini ptitle-edit";
  input.value = chartTitle(chart);
  input.maxLength = 32;
  input.setAttribute("aria-label", "Chart title");
  let done = false;
  // Only a key returns focus to the title: a blur is focus going where the user sent it, and
  // refocusing from its handler cancels that move.
  const finish = (commit, refocus) => {
    if (done) return;
    done = true;
    if (commit) renameChart(chart, input.value);
    input.remove();
    chart.titleEl.hidden = false;
    if (refocus) chart.titleEl.focus();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true, true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false, true); }
  });
  input.addEventListener("blur", () => finish(true, false));
  chart.titleEl.hidden = true;
  chart.titleEl.after(input);
  input.focus();
  input.select();
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
  // A reset or wrap continues the axis (timewindow.continueTick) and breaks the line there:
  // one point with every channel null, which uPlot draws as a gap.
  const c = continueTick(tickClocks, chart.port, chart.prevTick, x.tick, x.host);
  chart.prevTick = c;
  // A host clock step between two samples is a restart too: x.host already continues past it.
  const hostEpoch = Number.isInteger(x.id) ? hostEpochAt(hostClock, x.id) : undefined;
  const stepped = hostEpoch !== undefined && chart.hostEpoch !== undefined && chart.hostEpoch !== hostEpoch;
  if (hostEpoch !== undefined) chart.hostEpoch = hostEpoch;
  if (c.restart || stepped) breakChart(chart);
  let hx = x.host, tx = c.x;
  if (chart.lastHost !== null && hx <= chart.lastHost) hx = chart.lastHost + 1e-4;
  if (chart.lastTick !== null && tx <= chart.lastTick) tx = chart.lastTick + 1e-4;
  chart.lastHost = hx;
  chart.lastTick = tx;
  chart.xsHost.push(hx);
  chart.xsTick.push(tx);
  // Each sample's line id, for the shown-window export: the nudged x values above name no row.
  chart.ids.push(Number.isInteger(x.id) ? x.id : null);
  const len = chart.xsHost.length;
  const present = new Map(points);
  let newChannel = false, unitChanged = false;
  for (const [name, val] of points) {
    // A redefined stream can keep a name and change its unit: the newest definition is the
    // one in force for render metadata (SPEC 2.5), so the chip must not keep the old unit.
    if (def && chart.ys.has(name)) {
      const unit = unitOf(def, name) || null;
      if (chart.unit.get(name) !== unit) { chart.unit.set(name, unit); newChannel = unitChanged = true; }
    }
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
  // A channel absent from this sample: a typed stream's !ps carries every field, so there it
  // is a gap. An ad-hoc chart's channels are printed on separate !p lines as often as not, so
  // each holds its previous value (SPEC 9.2 hold-last); a null there, a break, stays a break.
  for (const name of chart.names) {
    if (present.has(name)) continue;
    const arr = chart.ys.get(name);
    arr.push(chart.sid === null && arr.length ? arr[arr.length - 1] : null);
  }
  // Block trim, matching pushBuffer in state.js: splicing one point per arriving sample is
  // O(PLOT_CAP) per sample once the ring is full.
  if (len > PLOT_CAP + PLOT_SLACK) {
    const drop = len - PLOT_CAP;
    chart.xsHost.splice(0, drop); chart.xsTick.splice(0, drop); chart.ids.splice(0, drop);
    for (const arr of chart.ys.values()) arr.splice(0, drop);
    // The freeze is a snapshot (chart.frozen), not an index into these arrays, so the trim
    // cannot reach it. An index had to be slid down by `drop` here, and once the whole ring
    // had rotated past the freeze that slide reached 0 and the paused chart blanked for good
    // (REVIEW class 26) - 100 s of a 1 kHz stream did it.
  }
  if (newChannel) renderChans(chart);
  // The single-trace y axis reads its unit label at build, and the redraw loop's rebuild
  // test does not look at units.
  if (unitChanged && chart.uplot && shownCount(chart) === 1) buildUplot(chart);
  if (!chart.paused) chart.dirty = true;   // paused charts freeze; live data still buffers
}

// One point with every channel null just past the newest sample, which uPlot draws as a gap:
// a tick reset or wrap (addSample), or rows the page never received (api.js markShed and the
// reconnect backfill's divider). Nothing it changes is drawn until the next sample, which marks
// the chart dirty. A chart with no sample yet has nothing to break.
function breakChart(chart) {
  if (chart.lastHost === null) return;
  chart.lastHost += 1e-4; chart.lastTick += 1e-4;
  chart.xsHost.push(chart.lastHost); chart.xsTick.push(chart.lastTick); chart.ids.push(null);
  for (const arr of chart.ys.values()) arr.push(null);
}

function breakCharts() { for (const chart of charts.values()) breakChart(chart); }

function addChannel(chart, name, unit, isInt) {
  const backfill = new Array(chart.xsHost.length - 1).fill(null);
  chart.ys.set(name, backfill);
  chart.names.push(name);
  chart.unit.set(name, unit || null);
  chart.show.set(name, true);
  chart.isInt.set(name, isInt);
  plotChannelMeta.set(chart.key + "|" + name, chart);   // for the total-channel count
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
    syncChartTitle(chart);
    if (!chart.collapsed) { chart.dirty = true; requestAnimationFrame(resizePlots); }
  });

  const title = document.createElement("span");
  title.className = "ptitle";
  makeSpanButton(title, "Rename this chart", () => startRename(chart));
  title.addEventListener("click", () => startRename(chart));
  title.title = "Click to rename this chart (kept in this browser; empty restores the default)";
  const port = document.createElement("span");
  port.className = "pport";
  port.title = "The port this chart's samples come from";
  const names = document.createElement("span");
  names.className = "pnames";
  chart.titleEl = title; chart.portEl = port; chart.namesEl = names;
  const ptag = document.createElement("span");
  ptag.className = "paused-tag"; ptag.textContent = "paused"; ptag.hidden = true;
  chart.pausedTag = ptag;

  // applies even while paused (redraw honours the freeze slice)
  const win = buildWindowButtons(chart.window, (secs) => { chart.window = secs; chart.dirty = true; },
                                 () => chart.paused);   // chartZoom: only a paused chart draws it
  chart.winEl = win;
  const pause = document.createElement("button");
  pause.className = "iconbtn"; pause.textContent = "pause";
  chart.pauseBtn = pause;
  pause.addEventListener("click", () => setChartPaused(chart, !chart.paused));
  const exp = document.createElement("button");
  exp.className = "iconbtn exportbtn"; exp.textContent = "export";
  exp.addEventListener("click", () => exportChart(chart));
  chart.exportBtn = exp;
  syncExportBtn(chart);
  const spacer = document.createElement("div"); spacer.className = "spacer";
  // One group, so a head too narrow for one line wraps the controls together, not "export" alone.
  const ctl = document.createElement("div"); ctl.className = "plot-ctl";
  ctl.append(win, pause, exp);
  head.append(collapse, title, port, names, ptag, spacer, ctl);
  syncChartTitle(chart);

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
  chart.valEls = new Map();
  chart.names.forEach((name, i) => {
    const lab = document.createElement("div");
    lab.className = "chan";
    lab.classList.toggle("off", !chart.show.get(name));
    const sw = document.createElement("span");
    sw.className = "swatch"; sw.style.background = colorFor(name);
    sw.title = "Click to set colour";
    // Swatch: open a colour picker (does NOT toggle show). Live swatch feedback on input (cheap),
    // but persist + re-stroke the series only on commit (change fires once when the picker closes),
    // so dragging the picker does not thrash a full uPlot destroy+recreate per tick.
    const pickColor = (e) => {
      if (e) { if (e.preventDefault) e.preventDefault(); if (e.stopPropagation) e.stopPropagation(); }
      openColorPicker(
        rgbToHex(colorFor(name)),
        (v) => { sw.style.background = v; },   // preview only, no rebuild
        (v) => {
          saveColor(name, v);
          sw.style.background = v;
          // Rebuild once, to re-stroke the series in the committed colour; the colour is keyed
          // by name, so another port's chart carrying the name follows it.
          for (const c of charts.values()) {
            if (c !== chart && c.ys.has(name)) renderChans(c);
            if (c.ys.has(name)) buildUplot(c);
          }
        },
      );
    };
    sw.addEventListener("click", pickColor);
    makeSpanButton(sw, `Set colour for ${name}`, pickColor);
    const txt = document.createElement("span"); txt.textContent = name;
    // The live value (or the value under the cursor) sits in the chip, replacing uPlot's own
    // legend, which repeated every name and swatch below the canvas for about 50 px.
    const val = document.createElement("span"); val.className = "val"; val.textContent = "--";
    chart.valEls.set(name, val);
    lab.append(sw, txt, val);
    const unit = chart.unit.get(name);
    if (unit) { const u = document.createElement("span"); u.className = "unit"; u.textContent = unit; lab.appendChild(u); }
    // Name (and the rest of the row): toggle the trace on/off. The click stays on the container so
    // the unit and the gaps remain clickable; keyboard activation is wired to the name span only,
    // which is what carries the focus and the aria-pressed state.
    const toggle = (e) => {
      const before = shownCount(chart);
      // Alt-click (Shift+Enter from makeSpanButton) solos instead of toggling.
      if (e && (e.altKey || e.shiftKey)) {
        chart.show = soloShow(chart.names, chart.show, name);
        chart.dirty = true;
        renderChans(chart);   // every row's state moved, not just this one
        buildUplot(chart);    // the shown count crossed the single-trace y-axis boundary
        return;
      }
      const on = !chart.show.get(name);
      chart.show.set(name, on);
      lab.classList.toggle("off", !on);
      txt.setAttribute("aria-pressed", on ? "true" : "false");
      syncExportBtn(chart);   // above the uplot guard: a collapsed chart still has a button
      syncChartTitle(chart);
      if (!chart.uplot) return;
      // The y axis exists only while exactly one trace is shown (buildUplot), so crossing
      // that count either way rebuilds; otherwise the series toggles in place.
      if (before === 1 || shownCount(chart) === 1) buildUplot(chart);
      else chart.uplot.setSeries(i + 1, { show: on });
    };
    lab.addEventListener("click", (e) => { if (e.preventDefault) e.preventDefault(); toggle(e); });
    makeSpanButton(txt, `Toggle channel ${name}`, toggle);
    txt.setAttribute("aria-pressed", chart.show.get(name) ? "true" : "false");
    txt.title = "Click to show / hide this trace, alt-click to show only it";
    host.appendChild(lab);
  });
  syncExportBtn(chart);
  syncChartTitle(chart);
  updatePlotCount();
  paintChanValues(chart);
}

// Each chip's readout: the value under this chart's cursor while it has one, else the newest
// value drawn (the live edge, or the frozen one while paused), so the strip always reads.
function paintChanValues(chart) {
  const u = chart.uplot;
  if (!u || !chart.valEls) return;
  const idx = cursorIdx(u);
  chart.names.forEach((name, i) => {
    const el = chart.valEls.get(name);
    const arr = u.data[i + 1];
    if (!el || !arr) return;
    let v = idx === null ? null : arr[idx];
    if (idx === null) for (let j = arr.length - 1; j >= 0 && v == null; j--) v = arr[j];
    const text = fmtPlotVal(drawnValue(chart, i, v), chart.isInt.get(name));
    if (el.textContent !== text) el.textContent = text;
  });
}

// The data index under a chart's cursor, or null while the cursor is off the chart.
function cursorIdx(u) {
  const left = u.cursor ? u.cursor.left : -1;
  if (!(left >= 0) || typeof u.posToIdx !== "function") return null;
  const idx = u.posToIdx(left);
  return Number.isInteger(idx) && idx >= 0 && idx < u.data[0].length ? idx : null;
}

// The cursor line's time tag (drawn by CSS from data-t), and the chip readouts beside it.
function onChartCursor(chart, u) {
  paintChanValues(chart);
  const cx = u.root && u.root.querySelector(".u-cursor-x");
  if (!cx) return;
  const idx = cursorIdx(u);
  if (idx === null) { cx.removeAttribute("data-t"); return; }
  cx.setAttribute("data-t", fmtTime(state, u.data[0][idx]));
  cx.classList.toggle("flip", u.cursor.left > u.width / 2);
}

// A chart with nothing shown has nothing to export, and a button that is enabled and inert
// is a control that lies about what it does (REVIEW class 12). It says why instead.
function syncExportBtn(chart) {
  if (!chart.exportBtn) return;
  const n = shownCount(chart);
  chart.exportBtn.disabled = n === 0;
  chart.exportBtn.title = n ? "Export the shown channels over a chosen range"
                            : "Nothing is shown on this chart: tick a channel to export it";
}

function shownCount(chart) {
  return chart.names.filter((n) => chart.show.get(n)).length;
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

// The x axis ticks on the same clock-friendly steps as the lane ruler (timewindow.fitAxisTicks),
// and labels them as bare numbers: the unit is shown once in the plots header (syncTimeSeg).
// A label that would run off either end of the canvas is left out (null) rather than cut.
function xAxisFor(chart) {
  return {
    splits: (u, axisIdx, min, max) => {
      const dpr = window.devicePixelRatio || 1;
      const w = u.bbox ? u.bbox.width / dpr : u.width;
      const px = axisLabelPx(u, axisIdx);
      const { step, ticks } = fitAxisTicks(state, { xmin: min, xmax: max }, w, px);
      chart.xStep = step;
      // Placed from this call's own range: the plot area starts `left` px into the canvas.
      const left = u.bbox ? u.bbox.left / dpr : 0;
      chart.xLabels = new Map(ticks.map((v) => {
        const text = fmtAxisTick(state, v, step);
        const x = left + ((v - min) / (max - min)) * w, half = px(text) / 2;
        return [v, x - half < 0 || x + half > u.width ? null : text];
      }));
      return ticks;
    },
    values: (u, splits) => splits.map((v) => {
      const hit = chart.xLabels && chart.xLabels.get(v);
      return hit !== undefined ? hit : fmtAxisTick(state, v, chart.xStep || 1);
    }),
  };
}

// A label's width in CSS px, measured in the axis's own font (uPlot keeps it scaled by the
// device pixel ratio); 7 px a character where nothing can measure (the test DOM).
let labelCtx = null;
function axisLabelPx(u, axisIdx) {
  const font = u.axes && u.axes[axisIdx] && u.axes[axisIdx].font && u.axes[axisIdx].font[0];
  if (!labelCtx) {
    const c = document.createElement("canvas");
    labelCtx = c.getContext && c.getContext("2d");
  }
  const dpr = window.devicePixelRatio || 1;
  return (text) => {
    if (font && labelCtx) {
      labelCtx.font = font;
      const m = labelCtx.measureText(text);
      if (m && m.width > 0) return m.width / dpr;
    }
    return text.length * 7;
  };
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

// Window the x axis to the last `window` (seconds for host/rel, ms for tick), anchored at
// the newest sample, so both live and frozen charts show a fixed-width strip. The shared
// drag-zoom replaces that with its own range while it stands.
function xRangeFor(chart) {
  return (u, dmin, dmax) => {
    const z = chartZoom(chart);
    if (!z && !Number.isFinite(dmax)) return [0, 1];
    const w = windowFor(z, state.timeMode, chart.window, dmax);
    return [w.xmin, w.xmax];
  };
}

// The shared zoom range while it applies to THIS chart: one drag zooms every chart and the
// digital lanes (SPEC 9.2's one x axis), and a zoom always freezes what it zooms, so a chart
// resumed on its own follows the tail again whatever the others are showing.
function chartZoom(chart) {
  return chart.paused ? zoomFor(getZoom(), state.timeMode) : null;
}

// A drag on the x axis: uPlot reports the selection, and every surface is paused so the
// follow-tail window (xRangeFor) does not overwrite it. Double-click clears it and resumes.
function onSelect(chart, u) {
  const sel = u.select;
  if (!sel || !(sel.width > 0)) return;
  const min = u.posToVal(sel.left, "x");
  const max = u.posToVal(sel.left + sel.width, "x");
  u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);   // the range is the zoom now
  if (!(max > min)) return;   // NaN from a scale with no data (the x scale never inverts)
  const z = { mode: state.timeMode, min, max };
  setZoom(z);
  showZoom(fmtZoomSpan(z));   // every window selector names the span, with its way out
  // One range for every panel means one freeze for every panel: pauseAll governs the charts,
  // the lanes and the panes, so the zoomed window and the terminal beside it are one instant.
  pauseAll(true);
  for (const c of charts.values()) c.dirty = true;
  markDigitalDirty();
}

// Drop the shared zoom and repaint; the paused state is NOT touched, because a time-mode
// change drops the range (it is in the old mode's units) and must not resume a frozen UI.
function clearZoom() {
  if (!getZoom()) return;
  setZoom(null);
  showZoom(null);
  for (const c of charts.values()) c.dirty = true;
  markDigitalDirty();
}

// The zoom chip's x and a double-click: back to the window, and live again on every surface.
// A window button leaves the zoom but not the freeze.
onZoomControls({ leave: clearZoom, exit: () => { clearZoom(); pauseAll(false); } });

// The soloed y axis's width: its widest label plus the tick and the gap, never under the 46 px it
// had fixed, which cut `10000` and `8e+307` at the left edge. uPlot calls this per layout cycle
// with the labels it will draw; past the second cycle it keeps the last size, so it converges.
const Y_AXIS_MIN_PX = 46;
function yAxisSize(u, values, axisIdx, cycleNum) {
  const axis = u.axes[axisIdx];
  if (cycleNum > 1) return axis._size;
  u.ctx.font = axis.font[0];
  let widest = 0;
  for (const v of values || []) if (v) widest = Math.max(widest, u.ctx.measureText(v).width);
  const labels = widest / (globalThis.devicePixelRatio || 1);   // the ctx measures device pixels
  return Math.max(Y_AXIS_MIN_PX, Math.ceil(axis.ticks.size + axis.gap + labels));
}

function buildUplot(chart) {
  if (chart.uplot) { chart.uplot.destroy(); chart.uplot = null; }
  const w = chart.canvasEl.clientWidth;
  if (w <= 0 || !chart.names.length) return;
  const col = plotColors();
  // Each channel gets its own auto-ranged y scale, so wildly different magnitudes (a
  // 0..65535 ramp next to a +-1 float) each use the full height instead of one flattening
  // the others. The y axis is therefore ambiguous and left undrawn; the channel chips carry
  // the real values with units.
  // Stepped paths: hold each value constant until the next sample (no linear interpolation
  // between points), which reads truer for slow/irregular signals.
  if (!stepPath) stepPath = uPlot.paths.stepped({ align: 1 });
  const series = [{}];
  const scales = { x: { time: false, range: xRangeFor(chart) } };
  chart.names.forEach((name, i) => {
    const unit = chart.unit.get(name);
    const skey = "y" + i;
    scales[skey] = { auto: true };
    series.push({
      label: unit ? `${name} (${unit})` : name,
      stroke: colorFor(name),
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
    ticks: { stroke: col.grid }, ...xAxisFor(chart),
  };
  const axes = [xaxis];   // x only while several traces share the height (see above)...
  // ...but with exactly one trace shown its scale is unambiguous, so it gets a left y axis.
  const shown = chart.names.filter((n) => chart.show.get(n));
  if (shown.length === 1) {
    // Soloing is when the axis is read for an absolute value, so it names the unit; a channel
    // with no unit gets no label rather than an empty band beside the numbers.
    const unit = axisUnitLabel(chart.unit.get(shown[0]));
    const si = chart.names.indexOf(shown[0]);
    axes.push({
      scale: "y" + si, side: 3, size: yAxisSize, incrs: Y_INCRS,
      stroke: col.label, grid: { stroke: col.grid, width: 1 }, ticks: { stroke: col.grid },
      // A scaled series' padded range runs past the double limit read back: those ticks
      // would say Infinity, so they get no label (uPlot skips a null).
      values: (u, splits) => splits.map((v) => {
        const r = drawnValue(chart, si, v);
        return Number.isFinite(r) ? fmtPlotVal(r, chart.isInt.get(shown[0])) : null;
      }),
      ...(unit ? { label: unit, labelSize: 14, labelGap: 0, labelFont: "10px " + monoFont() } : {}),
    });
  }
  const opts = {
    width: w, height: 150,
    scales,
    axes,
    series,
    // Linked cursor across every chart: since all charts share one time base, hovering one
    // draws the cursor on all of them at the same x (SPEC 9.2 "synchronized cursor").
    // An x drag zooms (onSelect); uPlot's own setScale on drag is off so the range stays
    // with xRangeFor; the container's capture-phase dblclick (below) keeps uPlot's own
    // double-click reset from ever running.
    cursor: { drag: { x: true, y: false, setScale: false }, sync: { key: "plots", scales: ["x", null] } },
    hooks: { setSelect: [(u) => onSelect(chart, u)], setCursor: [(u) => onChartCursor(chart, u)] },
    // Off: the channel chips above the canvas carry the values (paintChanValues).
    legend: { show: false },
  };
  chart.uplot = new uPlot(opts, currentData(chart, w), chart.canvasEl);
  if (!chart.zoomBound) {
    chart.zoomBound = true;
    // Double-click anywhere the zoom is drawn: back to the window selector's range, live
    // again, on every panel (digital.js binds the same on its lane wrap).
    // Capture phase, propagation stopped: uPlot's own dblclick on the overlay reset the x
    // scale to the whole buffer for one frame before the redraw put the window back.
    chart.canvasEl.addEventListener("dblclick", (e) => {
      e?.stopPropagation?.();
      if (getZoom()) exitZoom();
    }, true);
  }
  chart.theme = root.getAttribute("data-theme") || "";
  paintChanValues(chart);
}

// The soloed y axis's unit label runs along the 150 px chart height in a 10 px monospace face,
// so a longer unit (SPEC 2.5 bounds none) is cut to what fits, in code points; the chip keeps it
// whole.
const AXIS_UNIT_MAX = 22;

// The soloed y axis's tick steps: uPlot's numeric 1, 2, 2.5, 5 steps stop at 5e32, and past them
// it draws no y tick at all, so a series beyond about 1e33 (every one fitDrawSpan scales) had a
// bare axis. The same steps over the whole double range.
const Y_INCRS = [];
for (let e = -32; e <= 307; e++) for (const m of [1, 2, 2.5, 5]) Y_INCRS.push(+`${m}e${e}`);
function axisUnitLabel(unit) {
  const cps = [...(unit || "").trim()];
  return cps.length > AXIS_UNIT_MAX ? cps.slice(0, AXIS_UNIT_MAX - 1).join("") + "…" : cps.join("");
}

function monoFont() {
  return getComputedStyle(root).getPropertyValue("--font-mono").trim() || "monospace";
}

// The arrays a draw must consume: the pause-time snapshot while frozen, the live rings
// otherwise. The one seam, so a paused chart can never be re-derived from rings that kept
// filling (they do, deliberately, for the resume catch-up).
function chartDrawData(chart) {
  return chart.paused && chart.frozen ? chart.frozen : chart;
}

function currentData(chart, width = 0) {
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
  // A drag-zoom ships the selected range (plus the same one-sample margins) instead of the
  // tail window; the chart is paused, so the frozen snapshot is what it slices.
  const z = chartZoom(chart);
  const span = spanFor(state.timeMode, chart.window);
  const xmax = xsAll[total - 1];
  const winMin = z ? z.min : xmax - span, winMax = z ? z.max : xmax;   // xRangeFor's window
  let lo = firstAtOrAfter(xsAll, winMin, total);
  if (lo > 0) lo -= 1;   // include the sample just left of the window so the stepped path holds across the edge
  let hi = total;
  if (z) hi = Math.min(total, firstAtOrAfter(xsAll, z.max, total) + 1);
  // A channel first seen after the pause holds nothing the freeze covers, so it draws as a
  // gap rather than borrowing another series' length (uPlot needs every array equal-length).
  const ys = chart.names.map((nm) => src.ys.get(nm));
  // `width` px: a fast stream in a wide window is far more samples than pixels, and the stepped
  // path draws every one of them (timewindow.decimateColumns).
  const keep = decimateColumns(xsAll, ys, lo, hi, width, winMin, winMax);
  const out = keep
    ? [keep.map((i) => xsAll[i]), ...ys.map((a) => keep.map((i) => (a ? a[i] : null)))]
    : [xsAll.slice(lo, hi), ...ys.map((a) => (a ? a.slice(lo, hi) : new Array(hi - lo).fill(null)))];
  chart.drawScale = out.slice(1).map(fitDrawSpan);
  return out;
}

// uPlot ranges a scale by max - min padded by 10% of the span (or 100% of a constant value), and
// near the double limit either overflows to Infinity: the trace blanks (1.7e308 beside -1.7e308)
// or lies flat on the bottom edge ([0, 1.7e308], a constant 1.7e308). A series reaching past a
// quarter of the limit is drawn at a quarter scale, exact for a power of two, which every pad
// keeps finite; the chips and the soloed y axis divide it back out (drawnValue), so every number
// shown is still the sample's own.
function fitDrawSpan(ys) {
  let mn = Infinity, mx = -Infinity;
  for (const v of ys) if (v != null) { if (v < mn) mn = v; if (v > mx) mx = v; }
  if (!(Math.max(-mn, mx) > Number.MAX_VALUE / 4)) return 1;   // the largest magnitude; -Infinity if empty
  for (let i = 0; i < ys.length; i++) if (ys[i] != null) ys[i] *= 0.25;
  return 0.25;
}

// A value from the uPlot data of series `i` (0-based over chart.names) as the sample holds it.
function drawnValue(chart, i, v) {
  return v == null ? v : v / ((chart.drawScale && chart.drawScale[i]) || 1);
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
    // A new width re-derives the data too: the decimation (currentData) is per pixel.
    if (chart.uplot.width !== w) {
      chart.uplot.setSize({ width: w, height: 150 });
      chart.dirty = changed = true;
    }
    if (chart.dirty) {
      chart.uplot.setData(currentData(chart, w));
      chart.dirty = false;
      changed = true;
      paintChanValues(chart);
    }
  }
  return changed;
}

function resizePlots() {
  // Reads first, writes second, for the same reason as redrawPlots above.
  for (const [chart, w] of [...charts.values()].map((c) => [c, c.canvasEl.clientWidth])) {
    if (chart.uplot && w > 0 && chart.uplot.width !== w) {
      chart.uplot.setSize({ width: w, height: 150 });
      chart.dirty = true;   // the next redraw re-decimates for this width
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
let hoverAnchors = null;   // the tick anchors the hovered line's own stamp read (terminal.js buildLine)
let lastPx = -1, lastPy = -1;
let cursorShown = false;   // whether the shared cursor is currently drawn; gates idle clearHoverCursor churn
let hoverRaf = 0;          // pending rAF for the elementFromPoint hit-test (one per frame max)

function resolveLineAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const ln = el && el.closest ? el.closest(".ln") : null;
  return ln && ln.__row ? ln : null;
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
    const ln = resolveLineAt(lastPx, lastPy);
    const row = ln ? ln.__row : null;
    if (row !== hoverRow) { hoverRow = row; hoverAnchors = ln && ln.__anchors; applyHoverCursor(); }
  });
}

function paneMouseLeave() {
  lastPx = lastPy = -1;
  hoverRow = null;
  clearHoverCursor();
}

function xForRow(row) {
  const host = hostX(hostClock, row.id, row.ts);   // past any backward clock step, as the samples
  if (state.timeMode === "tick") {   // past a reset; a line with no tick sits at its estimate
    const t = lineTick(row);
    return t != null ? t + tickOffsetAt(tickClocks, row.port || "-", host)
      : estimateTickX(hoverAnchors || tickAnchors, tickClocks, row, hostClock);
  }
  return host;   // host and rel are both drawn on the host-time array
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
    // _fire=false skips the setCursor hook, so the readouts and time tag are applied here.
    if (snap == null) u.setCursor({ left: -10, top: -10 }, false, false);
    else u.setCursor({ left: u.valToPos(snap, "x"), top: (u.over.clientHeight || 100) / 2 }, false, false);
    onChartCursor(chart, u);
  }
  cursorShown = true;
}

function clearHoverCursor() {
  lastHoverX = null;
  for (const chart of charts.values()) {
    if (!chart.uplot) continue;
    chart.uplot.setCursor({ left: -10, top: -10 }, false, false);
    onChartCursor(chart, chart.uplot);
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
    ? { xsHost: chart.xsHost.slice(), xsTick: chart.xsTick.slice(), ids: chart.ids.slice(),
        ys: new Map([...chart.ys].map(([nm, arr]) => [nm, arr.slice()])) }
    : null;
  // Line-id watermark for the export (terminal.js does the same with pane.frozenId). Exact at
  // this instant because rows arrive in id order.
  chart.frozenMaxId = paused ? state.maxId : null;
  if (!paused) clearZoom();   // resuming follows the tail again, on every panel: one range
  if (chart.pauseBtn) {
    chart.pauseBtn.textContent = paused ? "resume" : "pause";
    chart.pauseBtn.classList.toggle("on", paused);
  }
  if (chart.pausedTag) chart.pausedTag.hidden = !paused;
  chart.dirty = true;
  paintWindowGroups();   // paused under a standing zoom, the chart now draws it
  freezeChanged();
}

registerSurface("charts", {
  isLive: () => [...charts.values()].some((c) => !c.paused),
  setPaused: (paused) => charts.forEach((c) => setChartPaused(c, paused)),
});


// The samples the chart draws, as line ids: the drag zoom's range while one stands on it, else
// the selector's span ending at its own newest sample (the frozen one while paused). Ids, not
// host times, under every time base: addSample nudges the x of each sample sharing a burst's
// timestamp, so a drawn edge falls inside a burst the daemon cannot split by time. Null when
// the window holds no sample. A reset's gap point carries no id and is skipped.
function chartShownWindow(chart) {
  const src = chartDrawData(chart), xs = state.timeMode === "tick" ? src.xsTick : src.xsHost;
  const n = xs.length;
  if (!n) return null;
  const z = chartZoom(chart);
  let first = firstAtOrAfter(xs, z ? z.min : xs[n - 1] - spanFor(state.timeMode, chart.window), n);
  let last = n - 1;
  if (z) { last = firstAtOrAfter(xs, z.max, n); if (last === n || xs[last] > z.max) last--; }
  while (first <= last && src.ids[first] == null) first++;
  while (last >= first && src.ids[last] == null) last--;
  return first <= last ? { sinceId: src.ids[first] - 1, idTo: src.ids[last] } : null;
}

// An ad-hoc chart's channels can come from several streams, so wide (one shared x column)
// is only offered for a chart that is one stream.
function exportChart(chart) {
  const names = chart.names.filter((n) => chart.show.get(n));
  // The button is disabled while this is empty (syncExportBtn); the guard stays because a
  // disabled button is a browser behaviour and this is the one that cannot send an empty
  // names= to the daemon.
  if (!names.length) return;
  const wide = chart.sid !== null;
  openExportDialog({
    kind: "plot",
    // A paused chart exports its frozen window, not the last N seconds up to now.
    watermark: chart.paused ? chart.frozenMaxId : null,
    shown: chartShownWindow(chart),
    options: [
      { name: "format", type: "select", label: "Format",
        choices: wide ? ["wide", "long"] : ["long"], value: wide ? "wide" : "long" },
      ...plotDecodeOptions(),
    ],
    build: (p, v) => plotExportPath(p, v, names, chart.port),
  });
}

function redrawTick() {
  if (!charts.size && !digitalLanes.size) return;   // nothing to draw and nothing to hover
  const plotsChanged = redrawPlots();
  const digitalChanged = redrawDigital();
  // Re-project the shared cursor only when something actually moved: a chart/lane repainted
  // under it, or the hovered time itself changed. Idle (no data, no hover) ticks cost nothing.
  if (plotsChanged || digitalChanged || hoverXVal() !== lastHoverX) applyHoverCursor();
}

// A widget below the visible part of the plots scroller has nothing on screen saying it
// exists (two charts at the CAN cap push the lanes out of view), so the Plots head counts
// them and scrolls to the first. All layout reads, then only the writes that change something:
// a same-value write still invalidates style. Run when the fold can move (initPlots), not per
// redraw, since each run forces a layout.
function foldItems() {
  const items = [...charts.values()].map((c) => ({ name: chartTitle(c), el: c.el }));
  if (!$("digitalHead").hidden) items.push({ name: "Digital / Enum", el: $("digitalHead") });
  return items;
}

function syncFoldCue() {
  const btn = $("plotFold");
  const box = $("plotsScroll").getBoundingClientRect();
  let below = [];
  if (box.height > 0) {
    below = belowFold(foldItems().map((it) => ({ ...it, top: it.el.getBoundingClientRect().top })),
                      box.bottom);
  }
  const text = below.length ? `↓ ${below.length} below` : "";
  const title = below.length ? `Below the visible area: ${below.map((it) => it.name).join(", ")}. `
    + "Click to scroll to the first" : "";
  if (btn.textContent !== text) btn.textContent = text;
  if (btn.hidden !== !below.length) btn.hidden = !below.length;
  if (btn.title !== title) btn.title = title;
}

function scrollToFold() {
  const sc = $("plotsScroll");
  const box = sc.getBoundingClientRect();
  const [first] = belowFold(foldItems().map((it) => ({ ...it, top: it.el.getBoundingClientRect().top })),
                            box.bottom);
  if (first) sc.scrollBy({ top: first.top - box.top, behavior: "smooth" });
}

// The Plots section is on screen: not the CAN-only view, and not a hidden sidebar, where each
// chart still reads a few px wide and the whole window would be drawn into it 5 times a second.
// Hidden is read off the layout, not #workspace.collapsed: the narrow single-column layout
// ignores that class and keeps the sidebar on screen. The tick's clientWidth reads come next.
function plotsShown() {
  return sidebar.getAttribute("data-view") !== "can" && sidebar.clientWidth > 0;
}

function initPlots() {
  // The time base is driven by the shared #timeSeg control (see setTimeMode).
  buildDigitalHead();
  initDigitalCursorSync();
  $("plotFold").addEventListener("click", scrollToFold);
  $("plotsScroll").addEventListener("scroll", syncFoldCue);
  // The fold moves when the scroller or what it holds changes size: a chart built, collapsed or
  // cleared, the lanes shown, the sidebar or a divider resized, the view switched.
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(() => syncFoldCue());
    for (const id of ["plotsScroll", "plotCharts", "digitalHead", "digitalWrap"]) ro.observe($(id));
  }
  setInterval(() => {
    // A hidden tab draws nothing: data still ingests, and the first visible tick repaints.
    if (document.hidden) return;
    // Switching back to a view with the plots, or reopening the sidebar, redraws on this tick.
    if (!plotsShown()) return;
    redrawTick();
  }, PLOT_REDRAW_MS);
  document.addEventListener("visibilitychange", () => {
    // Repaint immediately on return instead of waiting out the next timer tick.
    if (!document.hidden && plotsShown()) redrawTick();
  });
}

// Bumped by every clear-all (a capture reset is one): a history seed fetched before it holds
// samples of what was cleared, and api.js drops it rather than plot them on the emptied charts.
let seedGen = 0;
export function plotSeedGen() { return seedGen; }
// The lanes are fed through plotIngest, so a digital-only clear invalidates a seed in flight the
// same way; digital.js clearAllDigital bumps it through the callback registered at onSeedBump.
function bumpSeedGen() { seedGen += 1; }

// Clear the analog charts (see terminal.js clear-all): destroy each uPlot, drop the DOM,
// and restore the empty state once the lanes are gone too (syncPlotsChrome).
export function clearAllCharts() {
    bumpSeedGen();
    for (const chart of charts.values()) {
      if (chart.uplot) chart.uplot.destroy();
      if (chart.winEl) dropWindowButtons(chart.winEl);
      if (chart.el) chart.el.remove();
    }
    charts.clear();
    seedMaxId.clear();   // the ids it holds describe charts that no longer exist
    plotChannelMeta.clear();
    channelCapWarned = false;
    updatePlotCount();
    syncPlotsChrome();
    freezeChanged();   // the charts may have been the last live surface
}

// The three grammar parsers are exported for the shared plot-grammar fixture
// (tests/plot_grammar_cases.json), which drives them and protocol.py over one case list.
// The mirror is hand-written in seven places and has drifted twice; nothing else in the app
// calls them from outside this module.
export { parsePlotDef, parsePlotAdhoc, decodePlotSample };

export { charts, plotIngest, plotSeed, breakCharts, resizePlots, scheduleResizeRedraw, onResizeRedraw,
         setChartPaused, redrawPlots, chartDrawData, currentData, onSelect, clearZoom,
         exportChart, paneMouseMove, paneMouseLeave, applyHoverCursor, initPlots, renameChart,
         paintChanValues };
// The solo decision is chrome.js's (the digital lane gutter needs it too, and digital.js must
// not import this module); re-exported here because the analog legend is its other caller.
export { soloShow };
