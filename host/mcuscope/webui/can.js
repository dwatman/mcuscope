import { $, sidebar, state, hooks, portColor, isDecimalToken, splitTokens, saveBlob } from "./state.js";
import { openExportDialog } from "./exportdlg.js";
import { freezeChanged, registerSurface } from "./freeze.js";
import { makeSpanButton } from "./digital.js";

// ---- CAN table (sidebar): latest-per-id view built from !can events -----------------
//
// Classic CAN-tool view: one row per (port, bus, id), showing the latest payload, an EWMA of
// the inter-arrival period and the age since the frame was last seen; the running message
// count is in the row's hover, since a sixth column does not fit the 360 px sidebar.
// Fed from the same rows as the terminal (backfill + one /ws), so
// it costs nothing extra on the wire; a small timer re-renders to keep ages ticking.
// Rows are grouped by (port, bus) under a clickable divider once there is more than one
// group (SPEC 9.1); a single group is the plain table.

const CAN_ALPHA = 0.3;         // EWMA weight on the newest inter-arrival sample
const CAN_STALE_MIN_S = 0.25;  // floor under 5 periods: browser and WebSocket delivery jitter
const CAN_PERIODIC_GAPS = 3;   // gaps measured before an id can count as periodic
const CAN_JITTER_MAX = 0.5;    // mean gap deviation, as a fraction of the period, still periodic
const MAX_CAN_IDS = 256;       // cap on distinct (port, bus, id) rows, so a device emitting
                               // rotating or garbage CAN ids cannot grow the table/heap forever
const COLLAPSED_KEY = "canCollapsed";   // localStorage: JSON array of collapsed group labels
// key -> {port, bus, id, ext, rtr, dlc, hex, base, moved, count, period, jitter, gaps, lastTs, lastId}; `moved` is a
// bit per byte that changed in any frame since the table last painted this row, and `base` the
// last data frame's payload it is diffed against (a remote frame shows no payload and has none).
const canRows = new Map();
// Bumped wherever the ROW SET changes (insert, eviction, clear). A tick compares this instead
// of rebuilding a key-list signature.
let canRowsVersion = 0;
// Bumped by a group collapse. Kept apart from canRowsVersion because a paused table keys its
// view on the version it froze at, which no later bump of that one would move.
let canLayoutVersion = 0;
let canDirty = false;
let canLit = false;        // some painted byte is highlighted: the tick repaints to clear it
let canCapWarned = false;
let canFilter = "";        // the head's id filter, upper-case hex with any 0x stripped

// ---- freeze (SPEC 9.1 pause-all) -----------------------------------------------------
//
// The table is a freeze surface like the panes, the charts and the digital panel: a payload
// updating at 10 Hz cannot be read otherwise, and the export dialog's whole design is that a
// paused surface exports the window it shows. Frames keep ingesting into canRows while
// frozen; what the table renders comes from the snapshot taken at the pause.
let canPaused = false;
let canFrozen = null;      // Map(key -> entry copy) as of the pause, null while live
let canFrozenId = null;    // state.maxId at the pause: the export's id_to
let canFrozenNow = null;   // canNow() at the pause, so ages stop ticking too
let canFrozenVersion = 0;  // canRowsVersion at the pause: the snapshot's own row set

// The row map the table renders from: the snapshot while paused, the live rows otherwise.
// Every reader goes through this, so no path can draw live frames onto a frozen table.
function canModel() { return canPaused && canFrozen ? canFrozen : canRows; }

// Mirror of protocol.parse_can_event: decode an `!can <tick> <flags> <id> <data|->`
// body, returning null on anything malformed (matching the daemon's tolerant handling).
// The event name carries the bus (SPEC 2.5): `!can` is bus 1 (as is `!can1`), `!can2`..`!can9`
// the rest; `!can0` is not a bus and the line stays a generic event.
function parseCanEvent(raw) {
  const p = splitTokens(raw);   // runs of spaces only, as protocol.split_tokens (state.js)
  if (p.length !== 5 || !/^!can[1-9]?$/.test(p[0])) return null;
  const bus = p[0].length === 4 ? 1 : +p[0][4];
  if (!isDecimalToken(p[1]) || +p[1] > 0xFFFFFFFF) return null;   // tick wraps at 2^32
  const flags = p[2];
  let ext = false, rtr = false;
  if (flags !== "-") {
    if (!/^[xr]+$/.test(flags)) return null;
    ext = flags.includes("x"); rtr = flags.includes("r");
  }
  if (!/^(0[xX])?[0-9a-fA-F]{1,16}$/.test(p[3])) return null;   // whole-token hex, like parse_hex_int
  const id = parseInt(p[3], 16);
  // The daemon drops a frame whose id is out of range for its own flags, keeping the line
  // as a generic event with no can_frames row (protocol.parse_can_event). Without the same
  // check here the table showed rows that GET /can/frames and `mcu can` did not have.
  if (id > (ext ? 0x1FFFFFFF : 0x7FF)) return null;
  const payload = p[4];
  let dlc, hex = "";
  if (rtr) {
    if (!/^\d$/.test(payload) || +payload > 8) return null;
    dlc = +payload;
  } else if (payload === "-") {
    dlc = 0;
  } else {
    if (!/^([0-9a-fA-F]{2})+$/.test(payload) || payload.length > 16) return null;
    dlc = payload.length / 2;
    hex = payload.toUpperCase();
  }
  return { tick: +p[1], bus, id, ext, rtr, dlc, hex };
}

// The tick state.js lineTick reads off a !can line: only a line this decoder accepts has one.
hooks.canTick = (raw) => {
  const f = canOnce(raw);
  return f ? f.tick : null;
};

// pushBuffer asks for the tick just before canIngest decodes the same row, so the last parse is
// kept (plots.js decodeOnce). The frame is read, never mutated.
let lastCan = { raw: null, frame: null };
function canOnce(raw) {
  if (lastCan.raw !== raw) lastCan = { raw, frame: parseCanEvent(raw) };
  return lastCan.frame;
}

// "Now" for the age column, in the DAEMON's clock rather than the browser's. Every row.ts comes
// from the daemon, so on a remote view (SPEC 9.1 allows binding 0.0.0.0 and watching from another
// machine) Date.now() is off by whatever the two clocks disagree by - which rendered as ages like
// "-30000ms", still coloured age-fresh. Anchor on the newest row.ts seen, the way
// digitalRightEdge() does for the plots, and let locally measured elapsed time carry it forward so
// ages keep ticking while the bus is quiet. Every row is offered here, not just !can ones, so a
// silent bus on a chatty link still ages.
// The newest row alone reads a board silent since before the page loaded as fresh, so the
// daemon's own clock (/status `now`) moves the anchor forward too; an older daemon sends none,
// and the rows are then all there is.
let tsAnchor = null;   // {ts: newest daemon timestamp seen, at: performance.now() when it arrived}

function liveNow() {
  if (!tsAnchor) return Date.now() / 1000;   // nothing seen yet; nothing to age either
  return tsAnchor.ts + (performance.now() - tsAnchor.at) / 1000;
}

function canNow() {
  if (canPaused && canFrozenNow != null) return canFrozenNow;   // frozen: the ages stand still
  return liveNow();
}

// A daemon clock reading (/status `now`, epoch seconds). Forward only: the reading is as old as
// the request's round trip, and rows that arrived meanwhile already carry later times.
function noteDaemonNow(ts) {
  if (typeof ts !== "number" || !Number.isFinite(ts)) return;
  if (!tsAnchor || ts > liveNow()) tsAnchor = { ts, at: performance.now() };
}

function canIngest(row) {
  if (typeof row.ts === "number" && (!tsAnchor || row.ts > tsAnchor.ts)) {
    tsAnchor = { ts: row.ts, at: performance.now() };
  }
  if (row.chan !== "event" || !row.raw.startsWith("!can")) return;
  const f = canOnce(row.raw);
  if (!f) return;
  const port = row.port || "-";
  const key = port + "|" + f.bus + "|" + (f.ext ? "x" : "s") + f.id;
  let e = canRows.get(key);
  if (!e) {
    if (canRows.size >= MAX_CAN_IDS) {
      // Evict the least-recently-seen row so live traffic stays visible under the cap.
      let oldKey = null, oldTs = Infinity;
      for (const [k, r] of canRows) {
        const ts = r.lastTs == null ? -Infinity : r.lastTs;
        if (ts < oldTs) { oldTs = ts; oldKey = k; }
      }
      canRows.delete(oldKey);
      canRowsVersion += 1;
      if (!canCapWarned) {
        canCapWarned = true;
        console.warn(`can: id cap (${MAX_CAN_IDS}) reached, evicting least-recently-seen rows`);
      }
    }
    e = { port, bus: f.bus, id: f.id, moved: 0, count: 0, period: null, jitter: 0, gaps: 0,
          lastTs: null };
    canRows.set(key, e);
    canRowsVersion += 1;
    if (canRows.size === 1) freezeChanged();   // the first row makes a live table a live surface
  }
  if (e.lastTs !== null) {
    const dt = (row.ts - e.lastTs) * 1000;   // inter-arrival in ms
    if (dt >= 0) {
      if (e.period !== null) e.jitter = CAN_ALPHA * Math.abs(dt - e.period) + (1 - CAN_ALPHA) * e.jitter;
      e.period = e.period === null ? dt : CAN_ALPHA * dt + (1 - CAN_ALPHA) * e.period;
      e.gaps += 1;
    }
  }
  // Diffed per frame, not per paint: at 100 Hz a paint-to-paint diff lights every byte, and a
  // byte that changed and changed back between two paints would not light at all.
  // A remote frame carries no payload, so it neither diffs nor resets: an id polled by RTR
  // between its data frames still lights the bytes that moved (SPEC 9.1).
  if (!f.rtr) {
    if (e.base && f.hex && e.base.length === f.hex.length) {
      changedBytes(e.base, f.hex).forEach((c, i) => { if (c) e.moved |= 1 << i; });
    } else {
      e.moved = 0;   // first data frame or a dlc change: a new shape, nothing "moved"
    }
    e.base = f.hex;
  }
  e.ext = f.ext; e.rtr = f.rtr; e.dlc = f.dlc; e.hex = f.hex;
  e.lastTs = row.ts;
  e.lastId = row.id;
  e.count += 1;
  canDirty = true;
}

function fmtCanId(e) {
  return (e.ext ? e.id.toString(16).toUpperCase().padStart(8, "0")
                : e.id.toString(16).toUpperCase().padStart(3, "0"));
}

function fmtCanData(e) {
  if (e.rtr) return "remote";
  if (!e.hex) return "-";
  return e.hex.replace(/(..)(?=.)/g, "$1 ");   // "DEAD" -> "DE AD"
}

// Which bytes moved between two payloads of the same id: one flag per byte of `hex`.
// Nothing is flagged when there is no previous payload or the length changed - a dlc change
// is a different message shape, and calling every byte "changed" there says nothing.
export function changedBytes(prevHex, hex) {
  const n = (hex || "").length / 2;
  const flags = new Array(n).fill(false);
  if (!prevHex || !hex || prevHex.length !== hex.length) return flags;
  for (let i = 0; i < n; i++) flags[i] = prevHex.slice(i * 2, i * 2 + 2) !== hex.slice(i * 2, i * 2 + 2);
  return flags;
}

// Paint the data cell as one span per byte, highlighting the ones in `mask`. "Which byte
// moved when I pressed the button" is the question the latest-per-id view exists to answer,
// and the whole payload as a single text node cannot answer it.
function fillCanData(td, e, mask) {
  td.textContent = "";
  if (e.rtr || !e.hex) { td.textContent = fmtCanData(e); return; }
  for (let i = 0; i < e.hex.length / 2; i++) {
    const b = document.createElement("span");
    b.className = (mask >> i) & 1 ? "byte chg" : "byte";
    b.textContent = (i ? " " : "") + e.hex.slice(i * 2, i * 2 + 2);
    td.appendChild(b);
  }
}

// The pane regex that selects exactly this id's frames, in parseCanEvent's own grammar so the
// filter and the decoder cannot drift: `!can` or `!can1` for bus 1, `!can<n>` otherwise, then
// the tick and flag tokens, then the id, split on runs of spaces as the parser splits. The
// flags clause keeps a standard id from matching the extended id of the same value. Leading
// zeros are optional because the table shows the id zero-padded (fmtCanId) while the wire form
// may not be, and each hex letter takes either case. Plain classes only: the pattern runs in
// JavaScript and in the daemon's `regex`, and inline flags differ between the two.
export function canFilterPattern(e) {
  const bus = e.bus === 1 ? "1?" : String(e.bus);
  const flags = e.ext ? "[xr]*x[xr]*" : "(?:-|r+)";
  const id = fmtCanId(e).replace(/^0+(?=.)/, "")
    .replace(/[A-F]/g, (c) => `[${c}${c.toLowerCase()}]`);
  return `^!can${bus} +\\d+ +${flags} +(?:0[xX])?0*${id} `;
}

// Same units as the age column, so the two read against each other.
function fmtCanPeriod(ms) {
  if (ms == null) return "-";
  if (ms < 10) return ms.toFixed(1) + "ms";
  return fmtCanAge(ms / 1000);
}

function fmtCanAge(sec) {
  if (sec < 0.9995) return Math.round(sec * 1000) + "ms";   // never "1000ms"
  if (sec < 60) return sec.toFixed(1) + "s";
  const m = Math.floor(sec / 60);
  return m + "m" + String(Math.floor(sec % 60)).padStart(2, "0") + "s";
}

function cell(cls, text) {
  const td = document.createElement("td");
  if (cls) td.className = cls;
  if (text !== undefined) td.textContent = text;
  return td;
}

// Group order and labels. The label is what the divider shows and what the collapsed set is
// keyed by, so it is the same string whether one port or five are attached.
function groupLabel(e) { return `${e.port} CAN${e.bus}`; }
function byPortBusId(a, b) {
  return a.port < b.port ? -1 : a.port > b.port ? 1 : a.bus - b.bus || a.id - b.id;
}

// Read on every table rebuild rather than cached: rebuilds are rare (the row SET changed),
// and it keeps the stored value the single source of truth. The exception is a refused write
// (private mode, full quota): the choice is then held here for this page, or re-reading the
// unchanged store would undo every click and leave the dividers un-collapsible.
let collapsedMem = null;

function loadCollapsed() {
  if (collapsedMem) return new Set(collapsedMem);
  try {
    const v = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]");
    return new Set(Array.isArray(v) ? v.filter((s) => typeof s === "string") : []);
  } catch { return new Set(); }
}

function toggleCollapsed(label) {
  const set = loadCollapsed();
  if (set.has(label)) set.delete(label); else set.add(label);
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...set]));
    collapsedMem = null;
  } catch { collapsedMem = set; }   // private mode: applied, not remembered
  canLayoutVersion += 1;   // the visible row set changed; rebuild the table, paused or not
  renderCan();
}

// Built table kept between ticks: {version, cells: Map(key -> row refs)}. A tick only rewrites
// the text of cells whose value changed (usually just the age column); the table DOM, the count
// and the column layout all follow the row set, so they are rebuilt only when it changes.
let canView = null;

function renderCan() {
  canDirty = false;
  const wrap = $("canWrap");
  const rows = canModel();
  // In the Both view an empty table folds to its head (style.css), so a board with no CAN bus
  // does not lose the plots' room to an empty state.
  sidebar.classList.toggle("can-empty", !rows.size);
  // Folded, the head is all that shows, so it carries the empty state; the controls with nothing
  // to act on go. Pause and its tag stay, so a paused, cleared table can still be resumed, and
  // export stays: its frame history reaches the capture, which a view-only clear does not empty.
  $("canHeadEmpty").hidden = rows.size > 0;
  for (const id of ["canIdFilter", "canClear"]) $(id).hidden = !rows.size;
  if (!rows.size) {
    canView = null;
    canLit = false;
    $("canCount").textContent = "";
    const e = document.createElement("div");
    e.className = "empty-state";
    e.textContent = CAN_EMPTY_TEXT;
    e.title = CAN_EMPTY_TITLE;
    wrap.replaceChildren(e);
    return;
  }
  // The filter and the collapse are part of the view key rather than a canRowsVersion bump: a
  // paused table keys on its frozen version, which no bump would move.
  const version = canPaused ? canFrozenVersion : canRowsVersion;
  if (!canView || canView.version !== version || canView.filter !== canFilter
      || canView.layout !== canLayoutVersion) {
    const all = [...rows.entries()];
    const entries = all.filter(([, r]) => canIdMatches(r));
    const ids = (n) => `${n} id${n === 1 ? "" : "s"}`;
    let countText = canFilter ? `${entries.length} of ${ids(all.length)}` : ids(all.length);
    if (canCapWarned) countText += ` (limit ${MAX_CAN_IDS})`;
    $("canCount").textContent = countText;
    const multi = new Set(entries.map(([, r]) => groupLabel(r))).size > 1;
    entries.sort(([, a], [, b]) => byPortBusId(a, b));
    buildCanTable(wrap, entries, multi, version);
  }
  const now = canNow();
  canLit = false;
  for (const [key, e] of rows) updateCanRow(canView.cells.get(key), e, now);
}

// One line on screen; the grammar example and the doc pointers are its tooltip. index.html
// carries the same pair for the first paint.
const CAN_EMPTY_TEXT = "No CAN frames yet: the board prints !can lines";
const CAN_EMPTY_TITLE = "One line per frame: !can <tick> <flags> <id> <data>, for example "
  + "!can 1234 - 100 DEADBEEF (flags -, x or r; !can2 for bus 2). The firmware monitor does it "
  + "for you: firmware/monitor/INTEGRATION.md; the grammar is docs/SPEC.md section 2.5.";

function canIdMatches(e) { return !canFilter || fmtCanId(e).includes(canFilter); }

// Substring of the id as shown (zero-padded hex), so "100" finds 0x100 and 0x1000 alike.
function setCanFilter(text) {
  canFilter = String(text).trim().toUpperCase().replace(/^0X/, "");
  renderCan();
}

function buildCanTable(wrap, entries, multi, version) {
  const table = document.createElement("table");
  table.className = "can";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  const cols = Object.keys(COL_TITLES);
  for (const c of cols) {
    const th = document.createElement("th");
    if (c === "id" || c === "data") th.className = "l";
    th.textContent = c;
    th.title = COL_TITLES[c];
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const cells = new Map();
  const tbody = document.createElement("tbody");
  // With more than one (port, bus) group, each gets a divider row in place of a port column:
  // the port in its colour, then CANn. Clicking it collapses the group to the divider plus
  // its id count; bus 1 rows are untinted (unmarked on the wire too), the rest carry a
  // per-bus tint via data-bus so a group stays identifiable when scrolled past its divider.
  const collapsed = multi ? loadCollapsed() : new Set();
  let group = null;
  if (!entries.length) {
    const tr = document.createElement("tr");
    const td = cell("l dim", `no id contains ${canFilter}`);
    td.setAttribute("colspan", String(cols.length));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  for (const [key, e] of entries) {
    const label = groupLabel(e);
    if (multi && label !== group) {
      group = label;
      const hidden = collapsed.has(label);
      const n = entries.filter(([, r]) => groupLabel(r) === label).length;
      const tr = document.createElement("tr");
      tr.className = "bus-hdr" + (hidden ? " collapsed" : "");
      const td = cell("l");
      td.setAttribute("colspan", String(cols.length));
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = hidden ? "\u25B8 " : "\u25BE ";
      const pt = document.createElement("span");
      pt.textContent = e.port;
      pt.style.color = portColor(e.port);
      const bus = document.createElement("span");
      bus.textContent = ` CAN${e.bus}` + (hidden ? ` (${n} id${n === 1 ? "" : "s"})` : "");
      td.append(caret, pt, bus);
      tr.appendChild(td);
      tr.addEventListener("click", () => toggleCollapsed(label));
      tbody.appendChild(tr);
    }
    if (multi && collapsed.has(label)) continue;
    const tr = document.createElement("tr");
    if (e.bus !== 1) tr.dataset.bus = String(e.bus);
    const idc = cell("l");
    const r = { tr, idc, dlc: cell(""), data: cell("l data"),
                period: cell("dim"), age: cell(""), last: {} };
    fillCanId(idc, e);
    r.last.ext = e.ext; r.last.rtr = e.rtr;
    tr.append(idc, r.dlc, r.data, r.period, r.age);
    tbody.appendChild(tr);
    cells.set(key, r);
  }
  table.appendChild(tbody);
  wrap.replaceChildren(table);
  canView = { version, filter: canFilter, layout: canLayoutVersion, cells };
}

const COL_TITLES = {
  id: "CAN id in hex; ext is a 29-bit id, rtr a remote request. Click an id to filter the last pane to it",
  dlc: "Payload length in bytes",
  data: "Latest payload in hex; highlighted bytes changed in a frame since the last repaint",
  period: "Estimated period: an EWMA of the time between frames",
  age: "Since the last frame. A periodic id is amber past 5 missed periods and red past 10; an irregular or new id is never coloured",
};

// Narrow a terminal pane to one id's raw frames. The hook is wired in app.js rather than
// imported, because terminal.js already imports the table's siblings and a direct import
// would close the cycle; unwired (a test loading can.js alone) the id is simply not clickable.
let paneFilter = null;
export function setPaneFilter(fn) { paneFilter = fn; }

function filterPaneToId(e) {
  if (!paneFilter) return;
  paneFilter(canFilterPattern(e));
  const btn = $("canFilterClear");
  if (btn) btn.hidden = false;   // the way back out, shown only once there is one
}

function clearPaneFilter() {
  if (paneFilter) paneFilter("");
  showCanUnfilter(false);
}

// The unfilter button, shown while a pane holds a pattern an id click applied. terminal.js
// calls this when a pane closes, since the pane it would restore may have gone with it.
export function showCanUnfilter(on) {
  const btn = $("canFilterClear");
  if (btn) btn.hidden = !on;
}

function fillCanId(idc, e) {
  idc.textContent = "";
  const idspan = document.createElement("span");
  idspan.className = "id";
  idspan.textContent = fmtCanId(e);
  // "0x321 looks wrong, show me its raw frames" is one click: the alternative is hand-typing
  // parseCanEvent's grammar into a pane's regex box.
  idspan.title = `Filter the last terminal pane to ${fmtCanId(e)} frames`;
  makeSpanButton(idspan, idspan.title, () => filterPaneToId(e));
  idspan.addEventListener("click", () => filterPaneToId(e));
  idc.appendChild(idspan);
  if (e.ext) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "ext"; idc.appendChild(f); }
  if (e.rtr) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "rtr"; idc.appendChild(f); }
}

// Formatting is done only where a raw field moved: this runs for every row twice a second,
// and on a quiet bus nothing but the age column has anything new to say.
function updateCanRow(r, e, now) {
  if (!r) {
    // Hidden by the filter or a collapsed group: consume the mask as a paint would, or revealing
    // the row lights every byte that moved while it was hidden.
    if (!canPaused) e.moved = 0;
    return;
  }
  const L = r.last;
  const flags = L.ext !== e.ext || L.rtr !== e.rtr;   // rtr can flip per frame; redo the id cell
  if (flags) {
    fillCanId(r.idc, e);
    L.ext = e.ext; L.rtr = e.rtr;
  }
  if (L.dlc !== e.dlc) { r.dlc.textContent = String(e.dlc); L.dlc = e.dlc; }
  // The mask is consumed by the paint, so a paint with no frame since clears the highlight
  // rather than leaving it on a quiet id for the life of the page. A frozen snapshot keeps its
  // mask, so a paused table (rebuilt by a collapse or a filter) does not lose or churn it.
  const mask = e.moved || 0;
  if (flags || L.hex !== e.hex || L.mask !== mask) {
    fillCanData(r.data, e, mask);
    L.hex = e.hex;
    L.mask = mask;
  }
  if (mask) canLit = true;
  if (!canPaused) e.moved = 0;
  if (L.count !== e.count) {
    r.tr.title = `${e.count} frame${e.count === 1 ? "" : "s"} since clear`;
    L.count = e.count;
  }
  if (L.periodRaw !== e.period) {
    L.periodRaw = e.period;
    const period = fmtCanPeriod(e.period);   // an EWMA moves constantly; the text often does not
    if (L.period !== period) { r.period.textContent = period; L.period = period; }
  }
  updateCanAge(r, e, now);
}

// The age column alone: what the wall-clock tick refreshes when no frame has arrived.
function updateCanAge(r, e, now) {
  const L = r.last;
  const age = e.lastTs == null ? 0 : now - e.lastTs;
  const ageText = fmtCanAge(age);
  if (L.age !== ageText) { r.age.textContent = ageText; L.age = ageText; }
  const ageCls = canAgeClass(age, e.period, canPeriodic(e));
  if (L.ageCls !== ageCls) { r.age.className = ageCls; L.ageCls = ageCls; }
}

// Periodic: enough gaps measured, and they stay close to the period. An event-driven id's gaps
// deviate by about as much as their mean, so its period is no deadline to miss.
export function canPeriodic(e) {
  return e.period !== null && e.gaps >= CAN_PERIODIC_GAPS && e.jitter <= CAN_JITTER_MAX * e.period;
}

// Fresh reads as plain text and trouble takes the colour, so "all normal" is the quiet state.
// A periodic id is judged in its own periods: stale past 5 missed, dead past 10. Anything else
// is never coloured, since silence from an irregular or one-off id is not a fault.
export function canAgeClass(age, periodMs, periodic = true) {
  if (!periodic || periodMs == null) return "age-fresh";
  const p = periodMs / 1000;
  if (age >= Math.max(2 * CAN_STALE_MIN_S, 10 * p)) return "age-dead";
  return age >= Math.max(CAN_STALE_MIN_S, 5 * p) ? "age-stale" : "age-fresh";
}

// Tick the ages of the built table in place; nothing else has moved since the last render.
function ageCan() {
  if (!canView) return;
  const now = canNow();
  for (const [key, e] of canModel()) { const r = canView.cells.get(key); if (r) updateCanAge(r, e, now); }
}

// Freeze or thaw the table. Frames keep arriving into canRows throughout; the snapshot is
// what the table renders and what the export is bounded by (canFrozenId), so resuming shows
// the live state again with nothing lost.
function setCanPaused(paused) {
  if (canPaused === paused) return;
  canPaused = paused;
  if (paused) {
    canFrozenNow = canNow();
    canFrozen = new Map([...canRows].map(([k, e]) => [k, { ...e }]));
    canFrozenVersion = canRowsVersion;
    // Rows are written in id order, so state.maxId is the last line this table has seen.
    // Same shape as terminal.js's pane.frozenId and digital.js's digitalFrozenId.
    canFrozenId = state.maxId;
  } else {
    canFrozen = null;
    canFrozenId = null;
    canFrozenNow = null;
    // What moved while frozen is measured against a payload nobody saw; lighting it on resume
    // would light nearly every byte of a busy id.
    for (const e of canRows.values()) e.moved = 0;
  }
  const btn = $("canPause");
  if (btn) {
    btn.textContent = paused ? "resume" : "pause";
    btn.classList.toggle("on", paused);
  }
  const tag = $("canPausedTag");
  if (tag) tag.hidden = !paused;
  renderCan();
  freezeChanged();   // recompute the pause-all button text
}

registerSurface("can", {
  // An empty table has nothing to freeze, so it cannot hold the pause-all button in the
  // paused state before a single frame has been seen (the digital panel's rule).
  isLive: () => canRows.size > 0 && !canPaused,
  setPaused: (paused) => setCanPaused(paused),
});

// On screen: a view with the table, and the sidebar not hidden (the table would otherwise keep
// re-rendering into a zero-width column). Reopening repaints on the next tick. Hidden is the
// sidebar's laid-out width, as plots.js plotsShown reads it: the narrow layout ignores
// #workspace.collapsed.
export function canVisible() {
  const v = sidebar.getAttribute("data-view");
  return (v === "can" || v === "both") && sidebar.clientWidth > 0;
}

// Export the table as CSV: one row per (port, bus, id) with the latest payload and stats,
// matching what is on screen, collapsed groups included. Client-side only (this view is a
// client-side model, unlike /plot/export). `bus` is always a column, as in /can/frames.

// Mirror of server.py _csv_cell, rule for rule: a leading formula/control char
// (= + - @ tab CR) gets an apostrophe so a spreadsheet cannot execute it, and a cell
// containing a delimiter, quote or line break (CR included) is RFC-4180 quoted.
// tests/csv_cell_cases.json pins both sides to the same cases.
function csvField(s) {
  s = String(s);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

// The rows on screen: the model less what the id filter hides (collapsed groups stay in).
function shownCanRows() { return [...canModel().values()].filter(canIdMatches).sort(byPortBusId); }

function exportCan() {
  const rows = shownCanRows();
  if (!rows.length) return;
  const now = canNow();
  const lines = ["port,bus,id,ext,rtr,dlc,data,count,period_ms,age_s"];
  for (const e of rows) {
    lines.push([
      csvField(e.port), e.bus, fmtCanId(e), e.ext ? 1 : 0, e.rtr ? 1 : 0, e.dlc, e.hex || "",
      e.count, e.period == null ? "" : e.period.toFixed(1),
      e.lastTs == null ? "" : (now - e.lastTs).toFixed(2),
    ].join(","));
  }
  saveBlob(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }), "can.csv");
}

// The ids on screen from `port` (null: every port), as `/can/frames?id=` takes them (bare hex).
// Prefilled rather than imposed: the field is editable, and emptying it exports every id in the
// range. Per port, since the export is one port's and another board's ids would select nothing.
function visibleCanIds(port = null) {
  const rows = shownCanRows().filter((e) => port === null || e.port === port);
  return [...new Set(rows.map(fmtCanId))].join(",");
}

// The span the frozen table covers: from the oldest shown row's last frame (rows the id filter
// hides are not on screen) to the freeze, which is what "shown window" means for a
// latest-per-id view. By line id, as the charts' is: frames of one burst share a timestamp.
// The freeze's watermark is the upper bound. Null while live, since the table then has no
// window of its own - it shows whatever has ever arrived.
// Per port, as the history export is: `port` null takes every shown row.
function canShownWindow(port = null) {
  if (!canPaused || !canFrozen || !canFrozen.size) return null;
  const seen = shownCanRows().filter((e) => port === null || e.port === port).map((e) => e.lastId);
  if (!seen.length) return null;
  return { sinceId: Math.min(...seen) - 1 };
}

// Two different things share this button: the frame HISTORY from the capture (the daemon
// streams it over the chosen range), and a snapshot of this table, which is a client-side
// latest-per-id model the daemon has no equivalent of.
//
// The history's CSV has no port column, so it is always one board's (`port`), as the digital
// panel's export is: the ports the shown rows came from, then any other attached one, with a
// Port choice once there are two.
function openCanExport() {
  const ports = [...new Set([...shownCanRows().map((e) => e.port), ...state.knownAliases])]
    .filter((pt) => pt && pt !== "-");
  const chosen = (v) => (ports.includes(v.port) ? v.port : ports[0]);
  const byPort = new Map(ports.map((pt) => [pt, canShownWindow(pt)]));
  const history = { field: "format", equals: "history" };
  openExportDialog({
    kind: "can",
    watermark: canPaused ? canFrozenId : null,   // paused: never export past what is on screen
    // A port with no shown row while another has some exports an empty range, not everything.
    shown: [...byPort.values()].some(Boolean)
      ? (v) => byPort.get(chosen(v)) || { sinceId: canFrozenId } : canShownWindow(),
    options: [
      { name: "format", type: "select", label: "Source", value: "history",
        choices: [["history", "frame history (capture)"], ["snapshot", "table snapshot (on screen)"]] },
      ...(ports.length > 1
        ? [{ name: "port", type: "select", label: "Port", choices: ports, value: ports[0], enabledBy: history }]
        : []),
      // Follows the Port choice until edited (exportdlg.js buildOptions).
      { name: "ids", type: "text", label: "CAN ids", value: (v) => visibleCanIds(chosen(v) ?? null),
        placeholder: "100,7DF (empty for all)", enabledBy: history },
    ],
    build: (p, v) => {
      if (v.format === "snapshot") { exportCan(); return null; }
      p.set("format", "csv");
      if (ports.length) p.set("port", chosen(v));
      if (v.ids.trim()) p.set("id", v.ids.trim());
      return "/can/frames?" + p.toString();
    },
  });
}

// Reset the table to first-load state: the `clear` button, and a daemon DB reset (api.js
// resetForDbReset), where the old capture's rows must not keep ageing next to the new one.
let clearGen = 0;
export function canClearGen() { return clearGen; }   // a backfill in flight skips the rows a clear covered

function clearAllCan() {
  clearGen++;
  canRows.clear();
  canRowsVersion += 1;
  canCapWarned = false;
  // Clearing empties the table; it does not resume it (SPEC 9.1). The snapshot is emptied
  // with it, or a paused table would keep showing the capture that was just cleared.
  if (canPaused) {
    canFrozen = new Map();
    canFrozenVersion = canRowsVersion;
    canFrozenId = state.maxId;
  }
  // The age clock goes with the rows (state.js re-zeroes its own anchors in this same reset
  // path). Keeping it meant a new capture whose timestamps start lower than the old one's
  // never advanced the anchor, so canNow() stayed in the old capture's future and every
  // frame aged by that whole gap, permanently.
  tsAnchor = null;
  renderCan();
  freezeChanged();   // an empty table is no longer a live surface
}

function initCan() {
  $("canClear").addEventListener("click", clearAllCan);
  const filter = $("canIdFilter");
  filter.addEventListener("input", () => setCanFilter(filter.value));
  $("canExport").addEventListener("click", openCanExport);
  $("canPause").addEventListener("click", () => setCanPaused(!canPaused));
  $("canFilterClear").addEventListener("click", clearPaneFilter);
  $("canHeadEmpty").textContent = "no frames yet";
  $("canHeadEmpty").title = CAN_EMPTY_TITLE;
  renderCan();   // a board that never sends a frame still gets the folded empty section
  // Tick on a timer so ages advance even when no new frames arrive: a full render only when a
  // frame landed (canDirty), otherwise just the age cells. Skipped entirely in a hidden tab or
  // when the CAN view is hidden (frames still ingest and set canDirty; switching back to a CAN
  // view repaints once via setView, and a tab returning to visible repaints via app.js's
  // visibilitychange handler).
  setInterval(() => {
    if (document.hidden || !canVisible() || canPaused) return;   // frozen: nothing moves
    if (canDirty || canLit) renderCan();
    else ageCan();
  }, 1000);
}

export { canIngest, renderCan, canRows, clearAllCan, initCan, csvField, setCanPaused, setCanFilter,
         noteDaemonNow };
