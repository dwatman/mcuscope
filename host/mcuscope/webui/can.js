import { $, sidebar, state, portColor, isDecimalToken, saveBlob } from "./state.js";
import { openExportDialog } from "./exportdlg.js";
import { freezeChanged, registerSurface } from "./freeze.js";
import { makeSpanButton } from "./digital.js";

// ---- CAN table (sidebar): latest-per-id view built from !can events -----------------
//
// Classic CAN-tool view: one row per (port, bus, id), showing the latest payload plus a
// running message count, an EWMA of the inter-arrival period, and the age since the
// frame was last seen. Fed from the same rows as the terminal (backfill + one /ws), so
// it costs nothing extra on the wire; a small timer re-renders to keep ages ticking.
// Rows are grouped by (port, bus) under a clickable divider once there is more than one
// group (SPEC 9.1); a single group is the plain table.

const CAN_ALPHA = 0.3;         // EWMA weight on the newest inter-arrival sample
const CAN_STALE_S = 3;         // age past which a row is dimmed as "stale"
const MAX_CAN_IDS = 256;       // cap on distinct (port, bus, id) rows, so a device emitting
                               // rotating or garbage CAN ids cannot grow the table/heap forever
const COLLAPSED_KEY = "canCollapsed";   // localStorage: JSON array of collapsed group labels
const canRows = new Map();     // key -> {port, bus, id, ext, rtr, dlc, hex, count, period, lastTs}
// Bumped wherever the ROW SET changes (insert, eviction, clear). The table DOM depends on
// nothing else, so a tick compares this instead of rebuilding a key-list signature.
let canRowsVersion = 0;
let canDirty = false;
let canCapWarned = false;

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
  // Tokenize like Python str.split(): collapse whitespace runs, strip ends (protocol.py).
  const p = raw.trim().split(/\s+/);
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
  return { bus, id, ext, rtr, dlc, hex };
}

// "Now" for the age column, in the DAEMON's clock rather than the browser's. Every row.ts comes
// from the daemon, so on a remote view (SPEC 9.1 allows binding 0.0.0.0 and watching from another
// machine) Date.now() is off by whatever the two clocks disagree by - which rendered as ages like
// "-30000ms", still coloured age-fresh. Anchor on the newest row.ts seen, the way
// digitalRightEdge() does for the plots, and let locally measured elapsed time carry it forward so
// ages keep ticking while the bus is quiet. Every row is offered here, not just !can ones, so a
// silent bus on a chatty link still ages.
let tsAnchor = null;   // {ts: newest daemon timestamp seen, at: performance.now() when it arrived}

function canNow() {
  if (canPaused && canFrozenNow != null) return canFrozenNow;   // frozen: the ages stand still
  if (!tsAnchor) return Date.now() / 1000;   // nothing seen yet; nothing to age either
  return tsAnchor.ts + (performance.now() - tsAnchor.at) / 1000;
}

function canIngest(row) {
  if (typeof row.ts === "number" && (!tsAnchor || row.ts > tsAnchor.ts)) {
    tsAnchor = { ts: row.ts, at: performance.now() };
  }
  if (row.chan !== "event" || !row.raw.startsWith("!can")) return;
  const f = parseCanEvent(row.raw);
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
    e = { port, bus: f.bus, id: f.id, count: 0, period: null, lastTs: null };
    canRows.set(key, e);
    canRowsVersion += 1;
  }
  if (e.lastTs !== null) {
    const dt = (row.ts - e.lastTs) * 1000;   // inter-arrival in ms
    if (dt >= 0) e.period = e.period === null ? dt : CAN_ALPHA * dt + (1 - CAN_ALPHA) * e.period;
  }
  e.ext = f.ext; e.rtr = f.rtr; e.dlc = f.dlc; e.hex = f.hex;
  e.lastTs = row.ts;
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

// Paint the data cell as one span per byte, highlighting the ones that moved. "Which byte
// moved when I pressed the button" is the question the latest-per-id view exists to answer,
// and the whole payload as a single text node cannot answer it.
function fillCanData(td, e, prevHex) {
  td.textContent = "";
  if (e.rtr || !e.hex) { td.textContent = fmtCanData(e); return; }
  const flags = changedBytes(prevHex, e.hex);
  for (let i = 0; i < e.hex.length / 2; i++) {
    const b = document.createElement("span");
    b.className = flags[i] ? "byte chg" : "byte";
    b.textContent = (i ? " " : "") + e.hex.slice(i * 2, i * 2 + 2);
    td.appendChild(b);
  }
}

// The pane regex that selects exactly this id's frames, in parseCanEvent's own grammar so the
// filter and the decoder cannot drift: `!can` for bus 1, `!can<n>` otherwise, then the tick
// and flag tokens, then the id. Leading zeros are optional because the table shows the id
// zero-padded (fmtCanId) while the wire form may not be; the hex digits themselves are the
// daemon's own upper case.
export function canFilterPattern(e) {
  const bus = e.bus === 1 ? "" : String(e.bus);
  const id = fmtCanId(e).replace(/^0+(?=.)/, "");
  return `^!can${bus} \\d+ \\S+ (?:0[xX])?0*${id} `;
}

function fmtCanPeriod(ms) {
  if (ms == null) return "-";
  if (ms < 10) return ms.toFixed(1);
  if (ms < 10000) return String(Math.round(ms));
  return (ms / 1000).toFixed(1) + "s";
}

function fmtCanAge(sec) {
  if (sec < 1) return Math.round(sec * 1000) + "ms";
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
  canRowsVersion += 1;   // the visible row set changed; rebuild the table
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
  if (!rows.size) {
    canView = null;
    $("canCount").textContent = "";
    const e = document.createElement("div");
    e.className = "empty-state";
    e.textContent = "No CAN frames seen yet. !can events populate this live.";
    wrap.replaceChildren(e);
    return;
  }
  const version = canPaused ? canFrozenVersion : canRowsVersion;
  if (!canView || canView.version !== version) {
    const entries = [...rows.entries()];
    let countText = `${entries.length} id${entries.length === 1 ? "" : "s"}`;
    if (canCapWarned) countText += ` (limit ${MAX_CAN_IDS})`;
    $("canCount").textContent = countText;
    const multi = new Set(entries.map(([, r]) => groupLabel(r))).size > 1;
    entries.sort(([, a], [, b]) => byPortBusId(a, b));
    buildCanTable(wrap, entries, multi, version);
  }
  const now = canNow();
  for (const [key, e] of rows) updateCanRow(canView.cells.get(key), e, now);
}

function buildCanTable(wrap, entries, multi, version) {
  const table = document.createElement("table");
  table.className = "can";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  const cols = ["id", "dlc", "data", "count", "ms", "age"];
  for (const c of cols) {
    const th = document.createElement("th");
    if (c === "id" || c === "data") th.className = "l";
    th.textContent = c;
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
    const r = { idc, dlc: cell(""), data: cell("l data"), count: cell("dim"),
                period: cell("dim"), age: cell(""), last: {} };
    fillCanId(idc, e);
    r.last.ext = e.ext; r.last.rtr = e.rtr;
    tr.append(idc, r.dlc, r.data, r.count, r.period, r.age);
    tbody.appendChild(tr);
    cells.set(key, r);
  }
  table.appendChild(tbody);
  wrap.replaceChildren(table);
  canView = { version, cells };
}

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
  const btn = $("canFilterClear");
  if (btn) btn.hidden = true;
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
  if (!r) return;
  const L = r.last;
  const flags = L.ext !== e.ext || L.rtr !== e.rtr;   // rtr can flip per frame; redo the id cell
  if (flags) {
    fillCanId(r.idc, e);
    L.ext = e.ext; L.rtr = e.rtr;
  }
  if (L.dlc !== e.dlc) { r.dlc.textContent = String(e.dlc); L.dlc = e.dlc; }
  if (flags || L.hex !== e.hex) {
    fillCanData(r.data, e, L.hex);
    L.hex = e.hex;
    L.hilite = true;
  } else if (L.hilite) {
    // The payload stood still this tick: drop the highlight so it marks the last move on a
    // quiet id rather than sticking there for the life of the page.
    L.hilite = false;
    for (const b of r.data.children) b.className = "byte";
  }
  if (L.count !== e.count) { r.count.textContent = String(e.count); L.count = e.count; }
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
  const ageCls = age < CAN_STALE_S ? "age-fresh" : "age-stale";
  if (L.ageCls !== ageCls) { r.age.className = ageCls; L.ageCls = ageCls; }
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
  watermark: () => (canPaused ? canFrozenId : null),
});

function canVisible() {
  const v = sidebar.getAttribute("data-view");
  return v === "can" || v === "both";
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

function exportCan() {
  if (!canModel().size) return;
  const rows = [...canModel().values()].sort(byPortBusId);
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

// The ids on screen, as `/can/frames?id=` takes them (bare hex). Prefilled rather than
// imposed: the field is editable, and emptying it exports every id in the range.
function visibleCanIds() {
  return [...new Set([...canModel().values()].sort(byPortBusId).map(fmtCanId))].join(",");
}

// The span the frozen table covers: from the oldest row's last frame to the freeze, which is
// what "shown window" means for a latest-per-id view. Null while live, since the table then
// has no window of its own - it shows whatever has ever arrived.
function canShownLastMs() {
  if (!canPaused || !canFrozen || !canFrozen.size) return null;
  const seen = [...canFrozen.values()].map((e) => e.lastTs).filter((t) => t != null);
  if (!seen.length) return null;
  return Math.max(1, Math.round((canFrozenNow - Math.min(...seen)) * 1000));
}

// Two different things share this button: the frame HISTORY from the capture (the daemon
// streams it over the chosen range), and a snapshot of this table, which is a client-side
// latest-per-id model the daemon has no equivalent of.
function openCanExport() {
  openExportDialog({
    kind: "can",
    watermark: canPaused ? canFrozenId : null,   // paused: never export past what is on screen
    shownLastMs: canShownLastMs(),
    options: [
      { name: "format", type: "select", label: "What", choices: ["history", "snapshot"],
        value: "history" },
      { name: "ids", type: "text", label: "CAN ids", value: visibleCanIds(),
        placeholder: "100,7DF (empty for all)" },
    ],
    build: (p, v) => {
      if (v.format === "snapshot") { exportCan(); return null; }
      p.set("format", "csv");
      if (v.ids.trim()) p.set("id", v.ids.trim());
      return "/can/frames?" + p.toString();
    },
  });
}

// Reset the table to first-load state: the "reset" button, and a daemon DB reset (api.js
// resetForDbReset), where the old capture's rows must not keep ageing next to the new one.
function clearAllCan() {
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
}

function initCan() {
  $("canReset").addEventListener("click", clearAllCan);
  $("canExport").addEventListener("click", openCanExport);
  $("canPause").addEventListener("click", () => setCanPaused(!canPaused));
  $("canFilterClear").addEventListener("click", clearPaneFilter);
  // Tick on a timer so ages advance even when no new frames arrive: a full render only when a
  // frame landed (canDirty), otherwise just the age cells. Skipped entirely in a hidden tab or
  // when the CAN view is hidden (frames still ingest and set canDirty; switching back to a CAN
  // view repaints once via setView, and a tab returning to visible repaints via app.js's
  // visibilitychange handler).
  setInterval(() => {
    if (document.hidden || !canVisible() || canPaused) return;   // frozen: nothing moves
    if (canDirty) renderCan();
    else ageCan();
  }, 1000);
}

export { canIngest, renderCan, canRows, clearAllCan, initCan, csvField, setCanPaused };
