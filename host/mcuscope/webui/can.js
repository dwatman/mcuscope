import { $, sidebar, portColor } from "./state.js";

// ---- CAN table (sidebar): latest-per-id view built from !can events -----------------
//
// Classic CAN-tool view: one row per (port, id), showing the latest payload plus a
// running message count, an EWMA of the inter-arrival period, and the age since the
// frame was last seen. Fed from the same rows as the terminal (backfill + one /ws), so
// it costs nothing extra on the wire; a small timer re-renders to keep ages ticking.

const CAN_ALPHA = 0.3;         // EWMA weight on the newest inter-arrival sample
const CAN_STALE_S = 3;         // age past which a row is dimmed as "stale"
const canRows = new Map();     // key -> {port, id, ext, rtr, dlc, hex, count, period, lastTs}
let canDirty = false;

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
  if (!/^(0x)?[0-9a-fA-F]+$/.test(p[3])) return null;   // whole-token hex, like parse_hex_int
  const id = parseInt(p[3], 16);
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

function canIngest(row) {
  if (row.chan !== "event" || !/^!can\b/.test(row.raw)) return;
  const f = parseCanEvent(row.raw);
  if (!f) return;
  const port = row.port || "-";
  const key = port + "|" + (f.ext ? "x" : "s") + f.id;
  let e = canRows.get(key);
  if (!e) {
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

function renderCan() {
  canDirty = false;
  const wrap = $("canWrap");
  const rows = [...canRows.values()];
  $("canCount").textContent = rows.length ? `${rows.length} id${rows.length === 1 ? "" : "s"}` : "";
  if (!rows.length) {
    const e = document.createElement("div");
    e.className = "empty-state";
    e.textContent = "No CAN frames seen yet. !can events populate this live.";
    wrap.replaceChildren(e);
    return;
  }
  const multi = new Set(rows.map((r) => r.port)).size > 1;
  rows.sort((a, b) => (a.port < b.port ? -1 : a.port > b.port ? 1 : a.id - b.id));
  const now = Date.now() / 1000;

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

  const tbody = document.createElement("tbody");
  for (const e of rows) {
    const tr = document.createElement("tr");
    if (multi) {
      const pt = cell("l dim");
      pt.textContent = e.port;
      pt.style.color = portColor(e.port);
      tr.appendChild(pt);
    }
    const idc = cell("l");
    const idspan = document.createElement("span");
    idspan.className = "id";
    idspan.textContent = fmtCanId(e);
    idc.appendChild(idspan);
    if (e.ext) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "ext"; idc.appendChild(f); }
    if (e.rtr) { const f = document.createElement("span"); f.className = "flag"; f.textContent = "rtr"; idc.appendChild(f); }
    tr.appendChild(idc);

    tr.appendChild(cell("", String(e.dlc)));
    tr.appendChild(cell("l data", fmtCanData(e)));
    tr.appendChild(cell("dim", String(e.count)));
    tr.appendChild(cell("dim", fmtCanPeriod(e.period)));
    const age = e.lastTs == null ? 0 : now - e.lastTs;
    tr.appendChild(cell(age < CAN_STALE_S ? "age-fresh" : "age-stale", fmtCanAge(age)));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.replaceChildren(table);
}

function canVisible() {
  const v = sidebar.getAttribute("data-view");
  return v === "can" || v === "both";
}

function initCan() {
  $("canReset").addEventListener("click", () => { canRows.clear(); renderCan(); });
  // Re-render on a timer so ages tick even when no new frames arrive; skip the work entirely
  // when the table is empty/unchanged, or when the CAN view is hidden (frames still ingest and
  // set canDirty; switching back to a CAN view repaints once via setView).
  setInterval(() => { if (canVisible() && (canRows.size || canDirty)) renderCan(); }, 500);
}

export { canIngest, renderCan, canRows, initCan };
