import { $, sidebar, portColor } from "./state.js";

// ---- CAN table (sidebar): latest-per-id view built from !can events -----------------
//
// Classic CAN-tool view: one row per (port, id), showing the latest payload plus a
// running message count, an EWMA of the inter-arrival period, and the age since the
// frame was last seen. Fed from the same rows as the terminal (backfill + one /ws), so
// it costs nothing extra on the wire; a small timer re-renders to keep ages ticking.

const CAN_ALPHA = 0.3;         // EWMA weight on the newest inter-arrival sample
const CAN_STALE_S = 3;         // age past which a row is dimmed as "stale"
const MAX_CAN_IDS = 256;       // cap on distinct (port, id) rows, so a device emitting rotating
                               // or garbage CAN ids cannot grow the table/heap forever
const canRows = new Map();     // key -> {port, id, ext, rtr, dlc, hex, count, period, lastTs}
let canDirty = false;
let canCapWarned = false;

// Mirror of protocol.parse_can_event: decode an `!can <tick> <flags> <id> <data|->`
// body, returning null on anything malformed (matching the daemon's tolerant handling).
function parseCanEvent(raw) {
  // Tokenize like Python str.split(): collapse whitespace runs, strip ends (protocol.py).
  const p = raw.trim().split(/\s+/);
  if (p.length !== 5 || p[0] !== "!can") return null;
  if (!/^\d+$/.test(p[1]) || +p[1] > 0xFFFFFFFF) return null;   // tick wraps at 2^32
  const flags = p[2];
  let ext = false, rtr = false;
  if (flags !== "-") {
    if (!/^[xr]+$/.test(flags)) return null;
    ext = flags.includes("x"); rtr = flags.includes("r");
  }
  if (!/^(0x)?[0-9a-fA-F]{1,16}$/.test(p[3])) return null;   // whole-token hex, like parse_hex_int
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
  return { id, ext, rtr, dlc, hex };
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
  if (!tsAnchor) return Date.now() / 1000;   // nothing seen yet; nothing to age either
  return tsAnchor.ts + (performance.now() - tsAnchor.at) / 1000;
}

function canIngest(row) {
  if (typeof row.ts === "number" && (!tsAnchor || row.ts > tsAnchor.ts)) {
    tsAnchor = { ts: row.ts, at: performance.now() };
  }
  if (row.chan !== "event" || !/^!can\b/.test(row.raw)) return;
  const f = parseCanEvent(row.raw);
  if (!f) return;
  const port = row.port || "-";
  const key = port + "|" + (f.ext ? "x" : "s") + f.id;
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
      if (!canCapWarned) {
        canCapWarned = true;
        console.warn(`can: id cap (${MAX_CAN_IDS}) reached, evicting least-recently-seen rows`);
      }
    }
    e = { port, id: f.id, count: 0, period: null, lastTs: null };
    canRows.set(key, e);
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

// Built table kept between ticks: {sig, cells: Map(key -> row refs)}. A tick only rewrites the
// text of cells whose value changed (usually just the age column); the table DOM is rebuilt only
// when the row set, order, or the single/multi-port column layout changes.
let canView = null;

function renderCan() {
  canDirty = false;
  const wrap = $("canWrap");
  const entries = [...canRows.entries()];
  let countText = entries.length ? `${entries.length} id${entries.length === 1 ? "" : "s"}` : "";
  if (canCapWarned) countText += ` (limit ${MAX_CAN_IDS})`;
  $("canCount").textContent = countText;
  if (!entries.length) {
    canView = null;
    const e = document.createElement("div");
    e.className = "empty-state";
    e.textContent = "No CAN frames seen yet. !can events populate this live.";
    wrap.replaceChildren(e);
    return;
  }
  const multi = new Set(entries.map(([, r]) => r.port)).size > 1;
  entries.sort(([, a], [, b]) => (a.port < b.port ? -1 : a.port > b.port ? 1 : a.id - b.id));
  const sig = (multi ? "m|" : "s|") + entries.map(([k]) => k).join(",");
  if (!canView || canView.sig !== sig) buildCanTable(wrap, entries, multi, sig);
  const now = canNow();
  for (const [key, e] of entries) updateCanRow(canView.cells.get(key), e, now);
}

function buildCanTable(wrap, entries, multi, sig) {
  const table = document.createElement("table");
  table.className = "can";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  const cols = multi ? ["port", "id", "dlc", "data", "count", "ms", "age"]
                     : ["id", "dlc", "data", "count", "ms", "age"];
  for (const c of cols) {
    const th = document.createElement("th");
    if (c === "port" || c === "id" || c === "data") th.className = "l";
    th.textContent = c;
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const cells = new Map();
  const tbody = document.createElement("tbody");
  for (const [key, e] of entries) {
    const tr = document.createElement("tr");
    if (multi) {
      const pt = cell("l dim");
      pt.textContent = e.port;
      pt.style.color = portColor(e.port);
      tr.appendChild(pt);
    }
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
  canView = { sig, cells };
}

function fillCanId(idc, e) {
  idc.textContent = "";
  const idspan = document.createElement("span");
  idspan.className = "id";
  idspan.textContent = fmtCanId(e);
  idc.appendChild(idspan);
  if (e.ext) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "ext"; idc.appendChild(f); }
  if (e.rtr) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "rtr"; idc.appendChild(f); }
}

function updateCanRow(r, e, now) {
  if (!r) return;
  const L = r.last;
  if (L.ext !== e.ext || L.rtr !== e.rtr) {   // rtr can flip per frame; redo the id cell's flags
    fillCanId(r.idc, e);
    L.ext = e.ext; L.rtr = e.rtr;
  }
  if (L.dlc !== e.dlc) { r.dlc.textContent = String(e.dlc); L.dlc = e.dlc; }
  const data = fmtCanData(e);
  if (L.data !== data) { r.data.textContent = data; L.data = data; }
  if (L.count !== e.count) { r.count.textContent = String(e.count); L.count = e.count; }
  const period = fmtCanPeriod(e.period);
  if (L.period !== period) { r.period.textContent = period; L.period = period; }
  const age = e.lastTs == null ? 0 : now - e.lastTs;
  const ageText = fmtCanAge(age);
  if (L.age !== ageText) { r.age.textContent = ageText; L.age = ageText; }
  const ageCls = age < CAN_STALE_S ? "age-fresh" : "age-stale";
  if (L.ageCls !== ageCls) { r.age.className = ageCls; L.ageCls = ageCls; }
}

function canVisible() {
  const v = sidebar.getAttribute("data-view");
  return v === "can" || v === "both";
}

// Export the table as CSV: one row per (port, id) with the latest payload and stats, matching
// what is on screen. Client-side only (this view is a client-side model, unlike /plot/export).
function csvField(s) {
  s = String(s);
  if (/^[=+\-@]/.test(s)) s = "'" + s;   // neutralize spreadsheet formula injection (matches the daemon's CSV export)
  if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function exportCan() {
  if (!canRows.size) return;
  const rows = [...canRows.values()]
    .sort((a, b) => (a.port < b.port ? -1 : a.port > b.port ? 1 : a.id - b.id));
  const now = canNow();
  const lines = ["port,id,ext,rtr,dlc,data,count,period_ms,age_s"];
  for (const e of rows) {
    lines.push([
      csvField(e.port), fmtCanId(e), e.ext ? 1 : 0, e.rtr ? 1 : 0, e.dlc, e.hex || "",
      e.count, e.period == null ? "" : e.period.toFixed(1),
      e.lastTs == null ? "" : (now - e.lastTs).toFixed(2),
    ].join(","));
  }
  const url = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "can.csv";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// Reset the table to first-load state: the "reset" button, and a daemon DB reset (api.js
// resetForDbReset), where the old capture's rows must not keep ageing next to the new one.
function clearAllCan() {
  canRows.clear();
  canCapWarned = false;
  renderCan();
}

function initCan() {
  $("canReset").addEventListener("click", clearAllCan);
  $("canExport").addEventListener("click", exportCan);
  // Re-render on a timer so ages tick even when no new frames arrive; skip the work entirely
  // in a hidden tab, when the table is empty/unchanged, or when the CAN view is hidden (frames
  // still ingest and set canDirty; switching back to a CAN view repaints once via setView, and
  // a tab returning to visible repaints via app.js's visibilitychange handler).
  setInterval(() => {
    if (!document.hidden && canVisible() && (canRows.size || canDirty)) renderCan();
  }, 500);
}

export { canIngest, renderCan, canRows, clearAllCan, initCan };
