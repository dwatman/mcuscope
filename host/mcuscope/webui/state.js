// Shared primitives + mutable state for the web UI modules. Imported by every other
// module; imports nothing itself so it is the dependency-graph leaf.

const $ = (id) => document.getElementById(id);

// ---- API helpers (same-origin, root-relative) --------------------------------------

async function api(method, path, body) {
  const opt = { method, cache: "no-store" };
  if (body !== undefined) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const msg = (data && data.error) ? data.error : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return data;
}

const root = document.documentElement;
const sidebar = $("sidebar");

// Mutable scalars shared across modules (explicit object; never implicit globals).
export const state = { timeMode: "host", anchorTs: null, anchorTick: null, maxId: 0, knownAliases: [] };

export const buffer = [];          // shared client-side ring buffer feeding every pane
const BUFFER_MAX = 5000;   // shared backlog kept in memory

// Cross-module callbacks wired in main.js to break import cycles (see there).
export const hooks = { reapplyCursor: () => {}, liveChanged: () => {} };

const PORT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b", "#ef7a5e", "#6fb2ff"];
const portColorCache = new Map();
function portColor(alias) {
  const s = alias || "";
  let c = portColorCache.get(s);
  if (c) return c;
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  c = PORT_COLORS[h % PORT_COLORS.length];
  portColorCache.set(s, c);
  return c;
}

function pad2(n) { return String(n).padStart(2, "0"); }

// Extract the MCU tick (ms) a line carries, or null. Only CAN/plot events have one;
// !can and !p use a decimal tick, !ps a hex tick after the sid.
function lineTick(row) {
  if (row.chan !== "event") return null;
  const r = row.raw;
  const p = r.split(/\s+/);
  if (r.startsWith("!can ") || r.startsWith("!p ")) return /^\d+$/.test(p[1]) ? +p[1] : null;
  if (r.startsWith("!ps ")) return /^[0-9a-fA-F]+$/.test(p[2]) ? parseInt(p[2], 16) : null;
  return null;
}

// Add a row to the shared buffer and advance the id/relative-time/tick anchors.
function pushBuffer(row) {
  if (state.anchorTs === null) state.anchorTs = row.ts;
  if (state.anchorTick === null) { const t = lineTick(row); if (t !== null) state.anchorTick = t; }
  buffer.push(row);
  if (row.id > state.maxId) state.maxId = row.id;
  if (buffer.length > BUFFER_MAX) buffer.shift();
}

// Nearest sample x to a target value (data x is sorted ascending); snapping the cursor onto an
// actual sample keeps it from jittering between two neighbours as it re-applies.
function nearestX(xs, xval) {
  if (!xs || !xs.length) return null;
  if (xval <= xs[0]) return xs[0];
  const last = xs.length - 1;
  if (xval >= xs[last]) return xs[last];
  let lo = 0, hi = last;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < xval) lo = mid + 1; else hi = mid - 1;
  }
  const a = xs[lo - 1], b = xs[lo];
  return (xval - a) <= (b - xval) ? a : b;
}

const PLOT_CAP = 100000;                       // points kept per channel (SPEC ~100k)
const PLOT_WINDOWS = [[5, "5s"], [30, "30s"], [300, "5m"]];
const PLOT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b",
                     "#ef7a5e", "#6fb2ff", "#d888c0", "#c7d05b"];
// One shared colour store keyed by channel/lane name (names are globally unique per SPEC 2.5),
// used by BOTH the analog charts and the digital lanes. Effective colour = saved override, else
// the palette slot for that index.
const COLOR_KEY = "mcuscope.colors";
function loadColors() { try { return JSON.parse(localStorage.getItem(COLOR_KEY) || "{}"); } catch { return {}; } }
const savedColors = loadColors();
function saveColor(name, color) {
  savedColors[name] = color;
  try { localStorage.setItem(COLOR_KEY, JSON.stringify(savedColors)); } catch { /* private mode */ }
}
function colorFor(name, i) { return savedColors[name] || PLOT_COLORS[i % PLOT_COLORS.length]; }

// Shared window selector (5s/30s/5m) for both the analog chart heads and the digital head.
// `current` is the selected seconds; `onSelect(secs)` fires on click and the group repaints its
// own "on" state, so the two heads no longer carry duplicate copies of this loop.
function buildWindowButtons(current, onSelect) {
  const win = document.createElement("div");
  win.className = "plot-win";
  for (const [secs, label] of PLOT_WINDOWS) {
    const b = document.createElement("button");
    b.textContent = label;
    if (secs === current) b.classList.add("on");
    b.addEventListener("click", () => {
      win.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      onSelect(secs);
    });
    win.appendChild(b);
  }
  return win;
}

// Trigger a browser download of GET /plot/export for the given channels/window/format.
function downloadCsv(names, lastMs, format, filename) {
  if (!names.length) return;
  const params = new URLSearchParams({ names: names.join(","), last_ms: String(lastMs), format });
  const a = document.createElement("a");
  a.href = "/plot/export?" + params.toString();
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
}

export { $, api, root, sidebar, pad2, lineTick, pushBuffer, nearestX, portColor,
         BUFFER_MAX, PLOT_CAP, PLOT_WINDOWS, buildWindowButtons, downloadCsv, saveColor, colorFor };
