// MCUscope web UI (SPEC 9.1). Vanilla JS, no build step, no network fetches
// beyond this daemon. All API calls are root-relative so the page works unchanged
// whether it is served from 127.0.0.1 or across the LAN (bind mcuscoped to 0.0.0.0).
//
// Build progress: status/setup bar is live. Terminal, CAN table and command box
// are wired in later steps.

"use strict";

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

// ---- theme -------------------------------------------------------------------------

const root = document.documentElement;
(function initTheme() {
  // SPEC 9.1: dark by default. Honour a saved choice; otherwise force dark rather than
  // following the OS, so a first-time visitor on a light-mode OS still gets the dark UI.
  const saved = localStorage.getItem("theme");
  root.setAttribute("data-theme", saved === "light" ? "light" : "dark");
})();
$("themeBtn").addEventListener("click", () => {
  const cur = root.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
});

// ---- status / setup bar ------------------------------------------------------------

function fmtUptime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400); sec %= 86400;
  const h = Math.floor(sec / 3600); sec %= 3600;
  const m = Math.floor(sec / 60); const s = sec % 60;
  if (d) return `up ${d}d${h}h`;
  if (h) return `up ${h}h${m}m`;
  if (m) return `up ${m}m${s}s`;
  return `up ${s}s`;
}

// Uptime is ticked locally every second from the last poll, so the clock reads
// smoothly without polling /status once a second. uptimeBase is the server uptime
// at the moment (uptimeAt) it was fetched.
let uptimeBase = null;
let uptimeAt = 0;

function tickUptime() {
  if (uptimeBase === null) return;
  $("daemonUptime").textContent = fmtUptime(uptimeBase + (Date.now() - uptimeAt) / 1000);
}

function setDaemonOnline(online) {
  $("daemonDot").className = "dot " + (online ? "" : "crit");
  if (!online) {
    uptimeBase = null;
    $("daemonVer").textContent = "daemon unreachable";
    $("daemonHost").textContent = location.host;
    $("daemonUptime").textContent = "";
  }
}

function renderDaemon(s) {
  $("daemonVer").textContent = "mcuscoped " + s.version;
  $("daemonHost").textContent = location.host;
  uptimeBase = s.uptime_s;
  uptimeAt = Date.now();
  tickUptime();
}

function renderPorts(ports) {
  const host = $("ports");
  host.textContent = "";
  for (const pt of ports) {
    const chip = document.createElement("div");
    chip.className = "chip" + (pt.connected ? "" : " disc");

    const dot = document.createElement("span");
    dot.className = "dot" + (pt.connected ? "" : " off");
    chip.appendChild(dot);

    const alias = document.createElement("span");
    alias.className = "alias";
    alias.textContent = pt.alias;
    chip.appendChild(alias);

    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${pt.device} @${pt.baud}`;
    chip.appendChild(meta);

    const x = document.createElement("button");
    x.className = "x";
    x.title = `Detach ${pt.alias}`;
    x.textContent = "×";
    x.addEventListener("click", () => detachPort(pt.alias));
    chip.appendChild(x);

    host.appendChild(chip);
  }
}

async function refreshStatus() {
  try {
    const s = await api("GET", "/status");
    renderDaemon(s);
    renderPorts(s.ports || []);
    setKnownPorts((s.ports || []).map((p) => p.alias));
    setDaemonOnline(true);
  } catch {
    setDaemonOnline(false);
  }
}

async function detachPort(alias) {
  try {
    await api("DELETE", "/ports/" + encodeURIComponent(alias));
  } catch (e) {
    // Surface the failure without a modal: flash the daemon chip red briefly.
    console.warn("detach failed:", e.message);
  }
  refreshStatus();
}

// ---- attach dialog -----------------------------------------------------------------

const dlg = $("attachDlg");

async function openAttach() {
  $("dlgErr").textContent = "";
  $("aliasInput").value = "";
  await populateDevices();
  syncBaudCustom();
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
}

function closeAttach() {
  if (typeof dlg.close === "function") dlg.close();
  else dlg.removeAttribute("open");
}

async function populateDevices() {
  const sel = $("devSel");
  sel.textContent = "";
  let devices = [];
  try {
    const body = await api("GET", "/devices");
    devices = body.devices || [];
  } catch (e) {
    $("dlgErr").textContent = "could not list devices: " + e.message;
  }
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.by_id || d.device;
    const desc = d.description || d.vid_pid || "";
    opt.textContent = desc ? `${d.device}  -  ${desc}` : d.device;
    sel.appendChild(opt);
  }
  const sim = document.createElement("option");
  sim.value = "socket://127.0.0.1:9900";
  sim.textContent = "socket://127.0.0.1:9900  -  simulator (tcp)";
  sel.appendChild(sim);
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "custom...";
  sel.appendChild(custom);
  syncDevCustom();
}

function syncDevCustom() {
  $("devCustom").style.display = $("devSel").value === "custom" ? "" : "none";
}
function syncBaudCustom() {
  $("baudCustom").style.display = $("baudSel").value === "custom" ? "" : "none";
}

function chosenDevice() {
  const v = $("devSel").value;
  return v === "custom" ? $("devCustom").value.trim() : v;
}
function chosenBaud() {
  const v = $("baudSel").value;
  return v === "custom" ? parseInt($("baudCustom").value, 10) : parseInt(v, 10);
}

async function submitAttach() {
  const device = chosenDevice();
  const baud = chosenBaud();
  const alias = $("aliasInput").value.trim();
  if (!alias) { $("dlgErr").textContent = "alias is required"; return; }
  if (!device) { $("dlgErr").textContent = "device is required"; return; }
  if (!Number.isFinite(baud) || baud <= 0) { $("dlgErr").textContent = "baud must be a positive number"; return; }
  try {
    await api("POST", "/ports", { alias, device, baud });
    closeAttach();
    refreshStatus();
  } catch (e) {
    $("dlgErr").textContent = e.message;
  }
}

$("attachBtn").addEventListener("click", openAttach);
$("dlgCancel").addEventListener("click", closeAttach);
$("dlgClose").addEventListener("click", closeAttach);
$("dlgAttach").addEventListener("click", submitAttach);
$("devSel").addEventListener("change", syncDevCustom);
$("baudSel").addEventListener("change", syncBaudCustom);
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeAttach(); });

// ---- sidebar: section switch, collapse, resize (layout, no API) --------------------

const sidebar = $("sidebar");
const ws = $("workspace");

function setView(v) {
  sidebar.setAttribute("data-view", v);
  document.querySelectorAll("#sideSeg button").forEach((x) => x.classList.toggle("on", x.dataset.view === v));
  // Plot charts sized to a hidden (0-width) container need a resize once shown.
  if (v !== "can" && typeof resizePlots === "function") requestAnimationFrame(resizePlots);
}
document.querySelectorAll("#sideSeg button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

$("collapseBtn").addEventListener("click", () => ws.classList.add("collapsed"));
$("reopenBtn").addEventListener("click", () => ws.classList.remove("collapsed"));
// Expand: widen the sidebar so the charts get more room; a second click restores it.
let sideExpanded = false;
$("popoutBtn").addEventListener("click", () => {
  ws.classList.remove("collapsed");
  sideExpanded = !sideExpanded;
  ws.style.setProperty("--side-w", sideExpanded ? Math.round(ws.clientWidth * 0.6) + "px" : "360px");
  $("popoutBtn").innerHTML = sideExpanded ? "&#8596; restore" : "&#8596; expand";
  requestAnimationFrame(resizePlots);
});

const resizer = $("resizer");
let dragging = false;
// Leave room for the terminal's 320px min column and the 6px divider, or the grid
// overflows the viewport and the page scrolls sideways.
const clampW = (x) => Math.max(260, Math.min(x, ws.clientWidth - 326));
resizer.addEventListener("pointerdown", (e) => {
  dragging = true; resizer.classList.add("drag"); resizer.setPointerCapture(e.pointerId);
});
resizer.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const w = clampW(ws.getBoundingClientRect().right - e.clientX);
  ws.style.setProperty("--side-w", w + "px");
  resizePlots();
});
resizer.addEventListener("pointerup", (e) => {
  dragging = false; resizer.classList.remove("drag");
  try { resizer.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
});
resizer.addEventListener("dblclick", () => ws.style.setProperty("--side-w", "360px"));

// ---- terminal: shared line buffer + dynamically added, per-pane filtered views -----
//
// One WebSocket (all ports) and one client-side ring buffer feed every pane; a pane is
// just a filtered projection of that buffer, so adding a pane costs nothing on the wire.
//
// Rendering is batched: incoming lines are queued and flushed once per animation frame
// (one reflow per pane per frame), and paused panes append nothing at all - the buffer
// keeps filling but the frozen pane does no DOM work, so CPU drops to idle when paused.

const ALL_CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];
const TAG = { debug: "dbg", cmd: "cmd", resp: "resp", event: "evt", marker: "mrk", sys: "sys" };
const BUFFER_MAX = 5000;   // shared backlog kept in memory
const VIEW_MAX = 5000;     // DOM lines kept per pane
const MAX_PANES = 5;       // enough for real use; one socket feeds them all
const REGEX_DEBOUNCE_MS = 200;
const FLUSH_MS = 33;       // ~30 fps: batch appends into one render per frame per pane
const LINE_H = 18;         // fixed row height (must match .ln height in style.css)
const OVERSCAN = 8;        // rows rendered above/below the viewport for smooth scrolling
const PORT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b", "#ef7a5e", "#6fb2ff"];

const buffer = [];
let maxId = 0;
const panes = [];
let knownAliases = [];
let timeMode = "host"; // "host" | "tick" | "rel": timestamp base shared by panes and plots
let anchorTs = null;   // shared relative-time zero (first line the UI ever buffered)
let anchorTick = null; // shared tick zero (first tick-bearing line) so tick + rel zero together

function pushBuffer(row) {
  if (anchorTs === null) anchorTs = row.ts;
  if (anchorTick === null) { const t = lineTick(row); if (t !== null) anchorTick = t; }
  buffer.push(row);
  if (row.id > maxId) maxId = row.id;
  if (buffer.length > BUFFER_MAX) buffer.shift();
}

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

function fmtTs(row) {
  if (timeMode === "rel") {
    const base = anchorTs == null ? row.ts : anchorTs;
    return (row.ts - base).toFixed(3) + "s";   // sign only when negative
  }
  if (timeMode === "tick") {
    const t = lineTick(row);
    return t == null ? "-" : String(t - (anchorTick == null ? 0 : anchorTick));
  }
  const d = new Date(row.ts * 1000);
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${ms}`;
}

function matches(pane, row) {
  if (pane.port !== "all" && row.port !== pane.port) return false;
  if (!pane.channels.has(row.chan)) return false;
  if (pane.regex && !pane.regex.test(row.raw)) return false;
  return true;
}

function buildLine(pane, row) {
  const chan = row.chan || "debug";
  const d = document.createElement("div");
  d.__row = row;   // let a hover drive the plot cursor to this line's time (see initTerminal)
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = fmtTs(row);

  if (chan === "marker") {
    d.className = "ln marker";
    const div = document.createElement("span");
    div.className = "divider";
    div.textContent = "marker: " + row.raw;
    d.append(ts, div);
    return d;
  }

  const isErr = chan === "resp" && /\bERR\b/.test(row.raw);
  d.className = "ln " + chan + (isErr ? " err" : "");
  d.appendChild(ts);
  if (pane.port === "all") {
    const pt = document.createElement("span");
    pt.className = "port-tag";
    pt.textContent = row.port || "-";
    pt.style.color = portColor(row.port);
    d.appendChild(pt);
  }
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = TAG[chan] || chan;
  d.appendChild(tag);
  const msg = document.createElement("span");
  msg.className = "msg";
  msg.textContent = row.raw;
  d.appendChild(msg);
  return d;
}

function updateShown(pane) {
  pane.shownEl.textContent = pane.rows.length + " lines";
}

function updateJump(pane) {
  pane.jumpBtn.textContent = pane.pending > 0 ? `↓ ${pane.pending} new` : "↓ latest";
}

// Virtualized render: only the ~visible screenful of rows ever exists in the DOM.
// paddingTop / paddingBottom stand in for the off-screen rows so the scrollbar is exact
// and scrolling is smooth. Render cost is bounded (one screenful) no matter how many
// thousands of lines are buffered - this is what keeps CPU low.
function render(pane) {
  const sc = pane.scrollEl;
  const total = pane.rows.length;
  const viewH = sc.clientHeight || 300;
  const visCount = Math.ceil(viewH / LINE_H) + OVERSCAN * 2;
  let first;
  if (pane.autoscroll) {
    first = Math.max(0, total - visCount);
  } else {
    const maxFirst = Math.max(0, total - visCount);
    first = Math.min(Math.max(0, Math.floor(sc.scrollTop / LINE_H) - OVERSCAN), maxFirst);
  }
  const last = Math.min(total, first + visCount);
  pane.winFirst = first;
  pane.winLast = last;

  const frag = document.createDocumentFragment();
  for (let i = first; i < last; i++) frag.appendChild(buildLine(pane, pane.rows[i]));
  pane.vlist.style.paddingTop = (first * LINE_H) + "px";
  pane.vlist.style.paddingBottom = ((total - last) * LINE_H) + "px";
  pane.vlist.replaceChildren(frag);
  updateShown(pane);
  if (pane.autoscroll) { pane.selfScroll = true; sc.scrollTop = 1e9; }
}

// Coalesce scroll-driven re-virtualization into one render per frame per pane.
let renderScheduled = false;
const renderQueue = new Set();
function scheduleRender(pane) {
  renderQueue.add(pane);
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    const q = [...renderQueue];
    renderQueue.clear();
    for (const p of q) render(p);
  });
}

function setAutoscroll(pane, on) {
  pane.autoscroll = on;
  pane.pending = 0;
  pane.pill.textContent = on ? "live" : "paused";
  pane.pill.className = "pill " + (on ? "live" : "paused");
  if (on) {
    pane.jumpBtn.classList.remove("show");
    rebuild(pane);             // fold in whatever arrived while frozen, then snap to the latest
  } else {
    updateJump(pane);
    pane.jumpBtn.classList.add("show");
  }
  updateShared();
}

function updateShared() {
  const anyLive = panes.some((p) => p.autoscroll)
    || [...charts.values()].some((c) => !c.paused)
    || (digitalLanes.size > 0 && !digitalPaused);
  $("pauseAllBtn").textContent = anyLive ? "pause all" : "resume all";
}

// Recompute a pane's line set from the shared buffer (its filter changed). Preserves the
// pane's live/paused state - re-filtering never resumes a paused pane.
function rebuild(pane) {
  pane.rows = buffer.filter((row) => row.id > pane.clearId && matches(pane, row));
  if (pane.rows.length > VIEW_MAX) pane.rows.splice(0, pane.rows.length - VIEW_MAX);
  pane.pending = 0;   // the backlog is now folded into rows; reset the "N new" counter
  // Changing the row set resizes the scroll content, so the browser may clamp scrollTop and
  // fire a scroll event; mark it ours so the handler does not auto-resume a paused pane.
  pane.selfScroll = true;
  updateJump(pane);
  render(pane);
}

// A throttled flush drains each pane's queue. Live panes append the new lines and re-render
// their (bounded) visible window. Paused panes are frozen: the view and scrollbar stay put,
// the lines are only counted for the "N new" jump button (they remain in the shared buffer,
// so resuming rebuilds the full set), and no DOM work happens at all while paused.
let flushTimer = null;
function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flush, FLUSH_MS);
}
function flush() {
  flushTimer = null;
  for (const pane of panes) {
    if (!pane.queue.length) continue;
    if (!pane.autoscroll) {
      pane.pending += pane.queue.length;   // frozen: count only, view untouched
      pane.queue.length = 0;
      updateJump(pane);
      continue;
    }
    for (const r of pane.queue) pane.rows.push(r);
    pane.queue.length = 0;
    if (pane.rows.length > VIEW_MAX) pane.rows.splice(0, pane.rows.length - VIEW_MAX);
    render(pane);
  }
}

function applyRegex(pane, src) {
  pane.regexSrc = src;
  if (!src) { pane.regex = null; pane.matchInput.classList.remove("invalid"); return; }
  try { pane.regex = new RegExp(src); pane.matchInput.classList.remove("invalid"); }
  catch { pane.regex = null; pane.matchInput.classList.add("invalid"); }
}

function populatePortSelect(pane) {
  const sel = pane.portSel;
  const cur = pane.port;
  const opts = ["all", ...knownAliases];
  if (cur !== "all" && !opts.includes(cur)) opts.push(cur);
  sel.textContent = "";
  for (const a of opts) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = a === "all" ? "all ports" : a;
    sel.appendChild(o);
  }
  sel.value = cur;
}

function setKnownPorts(aliases) {
  knownAliases = aliases;
  panes.forEach(populatePortSelect);
  populateCmdPort();
}

function updatePaneButtons() {
  const only = panes.length <= 1;
  panes.forEach((p) => { p.el.querySelector(".closepane").disabled = only; });
  $("addPaneBtn").disabled = panes.length >= MAX_PANES;
  $("paneCount").textContent = `${panes.length} / ${MAX_PANES}`;
}

function createPane(cfg) {
  const el = $("paneTpl").content.firstElementChild.cloneNode(true);
  const scrollEl = el.querySelector(".scrollback");
  const vlist = document.createElement("div");
  vlist.className = "vlist";
  scrollEl.appendChild(vlist);
  const pane = {
    el,
    scrollEl,
    vlist,
    portSel: el.querySelector(".port-sel"),
    matchInput: el.querySelector(".match"),
    pill: el.querySelector(".pill"),
    jumpBtn: el.querySelector(".jump"),
    shownEl: el.querySelector(".shown"),
    port: cfg.port || "all",
    channels: new Set(cfg.channels && cfg.channels.length ? cfg.channels : ALL_CHANS),
    regex: null,
    regexSrc: "",
    autoscroll: true,
    regexTimer: null,
    rows: [],         // this pane's filtered lines (data, not DOM); virtualized on render
    queue: [],        // rows waiting for the next flush
    pending: 0,       // matching rows seen while paused (shown on the jump button)
    winFirst: 0,      // index range currently rendered into the DOM
    winLast: 0,
    clearId: 0,       // "cleared" boundary: rebuild ignores buffered lines up to this id
    selfScroll: false,
  };

  el.querySelectorAll(".chk").forEach((chk) => {
    const ch = chk.dataset.ch;
    if (!pane.channels.has(ch)) chk.classList.add("off");
    chk.addEventListener("click", () => {
      if (pane.channels.has(ch)) { pane.channels.delete(ch); chk.classList.add("off"); }
      else { pane.channels.add(ch); chk.classList.remove("off"); }
      rebuild(pane); persistState();
    });
  });

  pane.matchInput.value = cfg.regex || "";
  applyRegex(pane, cfg.regex || "");
  pane.matchInput.addEventListener("input", () => {
    applyRegex(pane, pane.matchInput.value);   // validity feedback is immediate
    clearTimeout(pane.regexTimer);             // re-render is debounced
    pane.regexTimer = setTimeout(() => { rebuild(pane); persistState(); }, REGEX_DEBOUNCE_MS);
  });
  el.querySelector(".match-clear").addEventListener("click", () => {
    if (!pane.matchInput.value) return;
    pane.matchInput.value = "";
    applyRegex(pane, "");
    clearTimeout(pane.regexTimer);
    rebuild(pane); persistState();
    pane.matchInput.focus();
  });

  el.querySelector(".clear").addEventListener("click", () => {
    // Emptying the pane collapses its content, so the browser clamps scrollTop to 0 and fires
    // a scroll event; selfScroll marks it as ours so the handler does not auto-resume a paused pane.
    pane.clearId = maxId; pane.rows = []; pane.queue.length = 0; pane.pending = 0;
    pane.selfScroll = true; render(pane); updateJump(pane);
  });
  el.querySelector(".closepane").addEventListener("click", () => closePane(pane));

  pane.portSel.addEventListener("change", () => {
    pane.port = pane.portSel.value; rebuild(pane); persistState();
  });

  pane.scrollEl.addEventListener("scroll", () => {
    if (pane.selfScroll) { pane.selfScroll = false; return; }
    const sc = pane.scrollEl;
    const atBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < LINE_H;
    if (atBottom && !pane.autoscroll) setAutoscroll(pane, true);
    else if (!atBottom && pane.autoscroll) setAutoscroll(pane, false);
    else if (!pane.autoscroll) scheduleRender(pane);   // re-virtualize the visible window
  });

  pane.scrollEl.addEventListener("mousemove", paneMouseMove);
  pane.scrollEl.addEventListener("mouseleave", paneMouseLeave);

  pane.pill.addEventListener("click", () => setAutoscroll(pane, !pane.autoscroll));
  pane.jumpBtn.addEventListener("click", () => setAutoscroll(pane, true));

  return pane;
}

function addPane(cfg) {
  if (panes.length >= MAX_PANES) return;
  const pane = createPane(cfg || {});
  panes.push(pane);
  $("terminalArea").appendChild(pane.el);
  populatePortSelect(pane);
  rebuild(pane);
  updatePaneButtons();
  persistState();
}

function closePane(pane) {
  if (panes.length <= 1) return;
  const i = panes.indexOf(pane);
  if (i < 0) return;
  panes.splice(i, 1);
  pane.el.remove();
  updatePaneButtons();
  persistState();
}

function persistState() {
  const st = {
    timeMode,
    panes: panes.map((p) => ({ port: p.port, channels: [...p.channels], regex: p.regexSrc })),
  };
  try { localStorage.setItem("termState", JSON.stringify(st)); } catch { /* private mode */ }
}

function syncTimeSeg() {
  document.querySelectorAll("#timeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.time === timeMode));
  const lbl = $("plotXLabel");
  if (lbl) lbl.textContent = { host: "x: host", tick: "x: tick (ms)", rel: "x: rel (s)" }[timeMode];
}

// One time base for everything: re-render the panes' timestamp column and repaint the plot
// x axis. The relative-time zero (anchorTs) is shared, so rel mode lines up across both.
function setTimeMode(mode) {
  timeMode = mode;
  syncTimeSeg();
  panes.forEach((p) => render(p));
  for (const chart of charts.values()) chart.dirty = true;
  markDigitalDirty();
  persistState();
}

function loadState() {
  let st = null;
  try { st = JSON.parse(localStorage.getItem("termState")); } catch { /* ignore */ }
  if (st && typeof st.timeMode === "string") timeMode = st.timeMode;
  else if (st && st.rel === true) timeMode = "rel";   // migrate the old boolean
  let cfgs = st && Array.isArray(st.panes) ? st.panes : null;
  if (!cfgs || !cfgs.length) cfgs = [{ port: "all", channels: ALL_CHANS, regex: "" }];
  for (const c of cfgs) addPane(c);
  syncTimeSeg();
}

async function backfill() {
  try {
    // Newest 200 by id (order=desc), reversed to oldest-first so the buffer, maxId and the
    // CAN table's EWMA/age all seed from recent history - not the oldest rows ever captured.
    const body = await api("GET", "/lines?order=desc&limit=200");
    for (const row of (body.lines || []).reverse()) { pushBuffer(row); canIngest(row); plotIngest(row); }
  } catch { /* daemon may be down; ws will catch up */ }
  panes.forEach(rebuild);
}

let wsReconnect = null;
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let sock;
  try { sock = new WebSocket(`${proto}://${location.host}/ws`); }
  catch { scheduleWsReconnect(); return; }
  sock.onmessage = (ev) => {
    let row;
    try { row = JSON.parse(ev.data); } catch { return; }
    if (!row || typeof row.id !== "number" || row.id <= maxId) return;
    pushBuffer(row);
    canIngest(row);
    plotIngest(row);
    let need = false;
    for (const p of panes) {
      if (!matches(p, row)) continue;
      p.queue.push(row);   // flush() appends to rows; live panes render, paused panes count
      need = true;
    }
    if (need) scheduleFlush();
  };
  sock.onclose = scheduleWsReconnect;
  sock.onerror = () => { try { sock.close(); } catch { /* already closing */ } };
}
function scheduleWsReconnect() {
  if (wsReconnect) return;
  wsReconnect = setTimeout(() => { wsReconnect = null; connectWs(); }, 1000);
}

function initTerminal() {
  $("addPaneBtn").addEventListener("click", () => {
    const last = panes[panes.length - 1];
    addPane(last ? { port: last.port, channels: [...last.channels], regex: last.regexSrc } : {});
  });
  document.querySelectorAll("#timeSeg button").forEach((b) =>
    b.addEventListener("click", () => setTimeMode(b.dataset.time)));
  $("pauseAllBtn").addEventListener("click", () => {
    // Pause everything (panes and plot charts) together, or resume everything, so the whole
    // UI freezes at one instant. Target = pause if anything is still live.
    const anyLive = panes.some((p) => p.autoscroll)
      || [...charts.values()].some((c) => !c.paused)
      || (digitalLanes.size > 0 && !digitalPaused);
    panes.forEach((p) => setAutoscroll(p, !anyLive));
    charts.forEach((c) => setChartPaused(c, anyLive));
    setDigitalPaused(anyLive);
  });
  $("clearAllBtn").addEventListener("click", () => {
    anchorTs = null; anchorTick = null;   // re-zero relative time and tick from here
    // selfScroll: the empty-pane scrollTop clamp must not auto-resume a paused pane (see per-pane clear).
    panes.forEach((p) => {
      p.clearId = maxId; p.rows = []; p.queue.length = 0; p.pending = 0;
      p.selfScroll = true; render(p); updateJump(p);
    });
  });
  window.addEventListener("resize", () => { panes.forEach(scheduleRender); resizePlots(); markDigitalDirty(); });
  loadState();
  updateShared();
  backfill().then(connectWs);
}

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

function initCan() {
  $("canReset").addEventListener("click", () => { canRows.clear(); renderCan(); });
  // Re-render on a timer so ages tick even when no new frames arrive; skip the work
  // entirely when the table is empty and nothing changed.
  setInterval(() => { if (canRows.size || canDirty) renderCan(); }, 500);
}

// ---- command bar: cmd/raw send + inline result + marker (SPEC 9.1) ------------------
//
// cmd mode routes through POST /cmd (seq + wait, timeout field) and renders the response
// inline with a distinct ok/err/timeout style; raw mode posts to POST /send. Up/down walk
// a localStorage-persisted history. The command and its response also stream back into the
// terminal panes over /ws, so this strip is just the immediate, focused acknowledgement.

let cmdMode = "cmd";        // "cmd" | "raw"
let cmdGen = 0;             // bumped per submit/dismiss; only the newest may write the strip
const cmdHistory = [];      // oldest-first; persisted in localStorage
let histIdx = -1;           // -1 = editing a fresh line, else index into cmdHistory
let histDraft = "";         // in-progress line stashed while browsing history

function loadCmdHistory() {
  try {
    const h = JSON.parse(localStorage.getItem("cmdHistory"));
    if (Array.isArray(h)) cmdHistory.push(...h.filter((x) => typeof x === "string"));
  } catch { /* ignore */ }
}
function saveCmdHistory() {
  try { localStorage.setItem("cmdHistory", JSON.stringify(cmdHistory.slice(-100))); }
  catch { /* private mode */ }
}

// null lets the daemon resolve the sole attached port (SPEC 4); an explicit alias targets it.
function cmdPortValue() {
  const v = $("cmdPort").value;
  return v && v !== "auto" ? v : null;
}

function populateCmdPort() {
  const sel = $("cmdPort");
  if (!sel) return;
  const cur = sel.value || "auto";
  const opts = ["auto", ...knownAliases];
  if (cur !== "auto" && !opts.includes(cur)) opts.push(cur);
  sel.textContent = "";
  for (const a of opts) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = a;
    sel.appendChild(o);
  }
  sel.value = opts.includes(cur) ? cur : "auto";
}

function setCmdMode(mode) {
  cmdMode = mode;
  document.querySelectorAll("#modeToggle button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("timeoutBox").classList.toggle("off", mode === "raw");   // timeout only applies to cmd
  $("prompt").textContent = mode === "raw" ? "$" : ">";
  $("cmdInput").focus();
}

// The result strip only occupies space while a result is showing: it auto-dismisses a
// few seconds after the final outcome (longer for errors), and a click hides it early.
// The response also lives in the terminal, so nothing is lost when it collapses.
let resultHideTimer = null;
function hideResult() {
  cmdGen++;   // invalidate any in-flight command so a late response can't reopen the strip
  clearTimeout(resultHideTimer);
  resultHideTimer = null;
  $("cmdResult").hidden = true;
}

function showResult(cls, code, query, detail, latency) {
  const box = $("cmdResult");
  box.className = "cmd-result " + cls;
  box.hidden = false;
  box.textContent = "";
  if (query) { const q = document.createElement("span"); q.className = "rq"; q.textContent = query; box.appendChild(q); }
  const c = document.createElement("span"); c.className = "rc"; c.textContent = code; box.appendChild(c);
  if (detail) { const d = document.createElement("span"); d.textContent = detail; box.appendChild(d); }
  if (latency != null) { const l = document.createElement("span"); l.className = "rlat"; l.textContent = "  " + latency.toFixed(1) + " ms"; box.appendChild(l); }

  clearTimeout(resultHideTimer);
  resultHideTimer = null;
  if (cls !== "pending") {   // pending is replaced by the final outcome; don't time it out
    resultHideTimer = setTimeout(hideResult, cls === "err" ? 9000 : 5000);
  }
}

async function submitCmd() {
  const input = $("cmdInput");
  const text = input.value.trim();
  if (!text) return;
  if (cmdHistory[cmdHistory.length - 1] !== text) { cmdHistory.push(text); saveCmdHistory(); }
  histIdx = -1; histDraft = "";
  input.value = "";
  const port = cmdPortValue();
  const prompt = cmdMode === "raw" ? "$ " : "> ";
  const gen = ++cmdGen;   // supersede any in-flight command; a stale response won't write below
  const report = (cls, code, detail, latency) => {
    if (gen === cmdGen) showResult(cls, code, prompt + text, detail, latency);
  };

  if (cmdMode === "raw") {
    try {
      await api("POST", "/send", { port, line: text });
      report("ok", "sent", "", null);
    } catch (e) {
      report("err", "error", e.message, null);
    }
    return;
  }

  let timeout = parseInt($("cmdTimeout").value, 10);
  if (!Number.isFinite(timeout) || timeout <= 0) timeout = 1000;
  report("pending", "...", "", null);
  try {
    const r = await api("POST", "/cmd", { port, cmd: text, timeout_ms: timeout });
    if (r.status === "ok") {
      report("ok", "ok", r.data || "", r.latency_ms);
    } else if (r.status === "err") {
      const nm = r.err_name ? `${r.err_name} (${r.err_code})` : `err ${r.err_code}`;
      report("err", "err", r.err_detail ? `${nm}: ${r.err_detail}` : nm, r.latency_ms);
    } else {
      report("wait", "timeout", `no response in ${timeout} ms`, null);
    }
  } catch (e) {
    report("err", "error", e.message, null);
  }
}

function historyPrev() {
  if (!cmdHistory.length) return;
  const input = $("cmdInput");
  if (histIdx === -1) { histDraft = input.value; histIdx = cmdHistory.length; }
  if (histIdx > 0) histIdx -= 1;
  input.value = cmdHistory[histIdx] || "";
  input.setSelectionRange(input.value.length, input.value.length);
}
function historyNext() {
  const input = $("cmdInput");
  if (histIdx === -1) return;
  histIdx += 1;
  if (histIdx >= cmdHistory.length) { histIdx = -1; input.value = histDraft; }
  else input.value = cmdHistory[histIdx];
  input.setSelectionRange(input.value.length, input.value.length);
}

async function submitMarker() {
  const input = $("markerInput");
  const text = input.value.trim();
  if (!text) return;
  try {
    await api("POST", "/marker", { port: cmdPortValue(), text });
    input.value = "";   // it lands as a divider line in the terminal via /ws
  } catch (e) {
    showResult("err", "error", "marker: " + text, e.message, null);
  }
}

function initCmdBar() {
  loadCmdHistory();
  populateCmdPort();
  document.querySelectorAll("#modeToggle button").forEach((b) =>
    b.addEventListener("click", () => setCmdMode(b.dataset.mode)));
  const input = $("cmdInput");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitCmd(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); historyPrev(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); historyNext(); }
  });
  $("cmdResult").addEventListener("click", hideResult);
  $("markerBtn").addEventListener("click", submitMarker);
  $("markerInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitMarker(); }
  });
}

// ---- realtime plots (sidebar): uPlot strip charts, one per stream (SPEC 9.2) --------
//
// Fed from the same rows as the terminal (backfill + the one /ws), decoded client-side
// the way the daemon decodes them: !pd caches a per-(port,sid) definition, !ps decodes
// against it, !p is ad-hoc. Each stream (sid) gets one chart, ad-hoc channels share one;
// every channel keeps a capped ring buffer, and a redraw timer repaints the visible
// window. X axis is host receive time by default, toggleable to the MCU tick.

const PLOT_CAP = 100000;                       // points kept per channel (SPEC ~100k)
const PLOT_REDRAW_MS = 200;                    // ~5 fps repaint of the visible window
const PLOT_WINDOWS = [[5, "5s"], [30, "30s"], [300, "5m"]];
const PLOT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b",
                     "#ef7a5e", "#6fb2ff", "#d888c0", "#c7d05b"];
// type -> [byte width, signed, is_float]; mirrors protocol._PLOT_TYPES.
const PLOT_TYPES = {
  u1: [1, false, false], s1: [1, true, false], u2: [2, false, false], s2: [2, true, false],
  u4: [4, false, false], s4: [4, true, false], f4: [4, false, true],
};
const PLOT_NAME_RE = /^[A-Za-z_][A-Za-z0-9_.]*$/;
// Enum/bits sigils in the unit slot (SPEC 2.5); mirrors protocol._ENUM_TYPES etc.
const ENUM_TYPES = new Set(["u1", "s1", "u2", "s2", "u4", "s4"]);
const BITS_TYPES = new Set(["u1", "u2", "u4"]);
const LABEL_RE = /^[A-Za-z0-9_.]{1,16}$/;

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
  return { sid: parts[1], channels };
}

function decodePlotField(hex, type) {
  const [w, signed, isFloat] = PLOT_TYPES[type];
  if (hex.length !== w * 2 || !/^[0-9a-fA-F]+$/.test(hex)) return null;
  const bytes = new Uint8Array(w);
  for (let i = 0; i < w; i++) bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  if (isFloat) return new DataView(bytes.buffer).getFloat32(0, false);   // big-endian
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
    const ch = unitFor && unitFor.channels.find((c) => c.name === name || (c.lanes || []).includes(name));
    if (ch && (ch.kind === "enum" || ch.kind === "bits")) digital.push([name, val, ch]);
    else analog.push([name, val]);
  }
  if (analog.length) addSample(ensureChart(key, sample.sid), analog, x, unitFor);
  if (digital.length) digitalIngest(sample.sid, digital, x);
}

// ---- digital / enum panel: canvas lanes below the analog charts ---------------------
//
// Enum and packed-bits channels do not belong on an auto-ranged y axis; they render as
// aligned logic-analyser lanes. Each lane keeps a transition-reduced ring (one vertex per
// value change, so a level held constant is a single segment), drawn to its own <canvas>
// at devicePixelRatio. bits draw as square waves with a faint high-fill; enums draw as an
// FPGA-style monochrome bus envelope with X-crossings and a right-clipped centred label.
// The panel shares the analog time base (host/tick/rel), window, and global pause.

const DLANE_H = 34;                 // must match .dlane { height } in style.css
const digitalLanes = new Map();     // name -> lane {name, kind, group, labels, color, xsHost, xsTick, vs, canvas, ...}
let digitalPaused = false;          // global freeze (mirrors the analog charts)
let digitalFrozen = null;           // {host, tick} right-edge captured at pause

function digitalIngest(sid, points, x) {
  showDigital();
  for (const [name, val, ch] of points) {
    let lane = digitalLanes.get(name);
    if (!lane) lane = addDigitalLane(name, ch);
    const n = lane.xsHost.length;
    // Transition reduction: store a vertex only when the value changes (plus the first sample).
    // vs[i] is held from its stored time xs[i] until the next vertex xs[i+1], and the draw
    // functions extend the first/last segment to the visible edges - so a repeat value adds
    // nothing and must NEVER overwrite the held level's recorded start time (doing so would
    // drag the segment forward and render it as a narrow right-shifted sliver).
    if (n === 0 || lane.vs[n - 1] !== val) {
      let hx = x.host;
      if (n && hx <= lane.xsHost[n - 1]) hx = lane.xsHost[n - 1] + 1e-4;   // keep x strictly increasing
      lane.xsHost.push(hx); lane.xsTick.push(x.tick); lane.vs.push(val);
      if (lane.vs.length > PLOT_CAP) { lane.xsHost.shift(); lane.xsTick.shift(); lane.vs.shift(); }
    }
    if (!digitalPaused) {   // paused: freeze the readout with the frozen window
      lane.dirty = true;
      lane.valEl.textContent = lane.kind === "enum" ? enumLabel(lane, val) : String(val);
    }
  }
}

function enumLabel(lane, v) {
  const hit = (lane.labels || []).find((p) => p[0] === v);
  return hit ? hit[1] : String(v);
}

function addDigitalLane(name, ch) {
  const isBit = ch.kind === "bits";
  const lane = {
    name, kind: ch.kind, group: isBit ? ch.name : null, labels: ch.labels || null,
    color: PLOT_COLORS[digitalLanes.size % PLOT_COLORS.length],
    xsHost: [], xsTick: [], vs: [], dirty: true, _sizedirty: false,
  };
  // Packed bit lanes are grouped under their parent byte name (once).
  if (isBit && ch.name && !document.getElementById("dgrp-" + ch.name)) {
    const grp = document.createElement("div");
    grp.className = "dgroup"; grp.id = "dgrp-" + ch.name;
    grp.textContent = ch.name + " (packed)";
    $("digitalLanes").appendChild(grp);
  }
  const row = document.createElement("div");
  row.className = "dlane";
  row.innerHTML = `<div class="gut"><span class="sw"></span><span class="nm${lane.group ? " sub" : ""}"></span><span class="val"></span></div>`;
  const cv = document.createElement("canvas");
  row.appendChild(cv);
  $("digitalLanes").appendChild(row);
  row.querySelector(".sw").style.background = lane.color;
  row.querySelector(".nm").textContent = name;
  lane.canvas = cv;
  lane.valEl = row.querySelector(".val");
  lane.swEl = row.querySelector(".sw");
  lane.nameEl = row.querySelector(".nm");
  digitalLanes.set(name, lane);
  updateDigitalCount();
  return lane;
}

function showDigital() { $("digitalHead").hidden = false; $("digitalLanes").hidden = false; }
function updateDigitalCount() {
  const n = digitalLanes.size;
  $("digitalCount").textContent = n ? `${n} lane${n === 1 ? "" : "s"}` : "";
}

// The digital panel reuses the analog charts' window (first chart wins; defaults to 30 s).
function currentWindowSec() { return charts.size ? [...charts.values()][0].window : 30; }

// Repaint dirty lanes on the shared PLOT_REDRAW_MS timer. A backing-store size mismatch
// (any width change: window/sidebar drag, popout, view switch) forces a redraw too, so the
// lanes track resizes without wiring every resize path.
function redrawDigital() {
  if (!digitalLanes.size) return;
  const winSec = currentWindowSec();
  const dpr = window.devicePixelRatio || 1;
  for (const lane of digitalLanes.values()) {
    const cw = lane.canvas.clientWidth;
    if (cw <= 0) continue;   // panel hidden; leave the lane dirty for when it is shown
    const sizeChanged = lane.canvas.width !== Math.round(cw * dpr);
    if (!lane.dirty && !lane._sizedirty && !sizeChanged) continue;
    drawDigitalLane(lane, winSec);
    lane.dirty = false;
  }
}

function drawDigitalLane(lane, winSec) {
  const cv = lane.canvas, dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = DLANE_H;
  if (w <= 0) return;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const xs = timeMode === "tick" ? lane.xsTick : lane.xsHost;   // rel shares the host array
  if (!xs.length) return;
  const span = (timeMode === "tick" ? winSec * 1000 : winSec) || 1;   // tick is in ms
  const xmax = (digitalPaused && digitalFrozen)
    ? (timeMode === "tick" ? digitalFrozen.tick : digitalFrozen.host)
    : xs[xs.length - 1];
  const xmin = xmax - span;
  const X = (t) => ((t - xmin) / span) * w;
  if (lane.kind === "bits") drawBits(g, lane, xs, X, w, h);
  else drawEnum(g, lane, xs, X, w, h);
}

// bits: a square wave. Each stored vertex is a value change; the level vs[i] holds from its
// sample to the next (or the right edge). The first level is extended to the left edge so a
// held signal reads across the whole lane. A faint fill sits under the high level.
function drawBits(g, lane, xs, X, w, h) {
  const yHi = 8, yLo = h - 8, n = xs.length;
  const y = (v) => (v ? yHi : yLo);
  g.fillStyle = lane.color + "22";
  for (let i = 0; i < n; i++) {
    if (!lane.vs[i]) continue;
    const x0 = Math.max(0, i === 0 ? 0 : X(xs[i]));
    const x1 = Math.min(w, i + 1 < n ? X(xs[i + 1]) : w);
    if (x1 > x0) g.fillRect(x0, yHi, x1 - x0, yLo - yHi);
  }
  g.strokeStyle = lane.color; g.lineWidth = 1.6;
  g.beginPath();
  g.moveTo(0, y(lane.vs[0]));
  for (let i = 0; i < n; i++) {
    const xEnd = i + 1 < n ? X(xs[i + 1]) : w;
    g.lineTo(xEnd, y(lane.vs[i]));                        // hold this level
    if (i + 1 < n) g.lineTo(xEnd, y(lane.vs[i + 1]));     // vertical edge to the next level
  }
  g.stroke();
}

// enum: a monochrome FPGA bus envelope (top/bottom rails joined by X-crossings at each
// transition), a whisper of fill, and the label centred and hard-clipped to the segment so
// it never spills past its crossings (a very narrow segment shows no text).
function drawEnum(g, lane, xs, X, w, h) {
  const yT = 6, yB = h - 6, ym = (yT + yB) / 2, xo = 5, n = xs.length;
  g.font = "10px ui-monospace, monospace";
  g.textBaseline = "middle"; g.textAlign = "center";
  for (let i = 0; i < n; i++) {
    const x0 = Math.max(0, i === 0 ? 0 : X(xs[i]));
    const x1 = Math.min(w, i + 1 < n ? X(xs[i + 1]) : w);
    if (x1 <= 0 || x0 >= w || x1 <= x0) continue;
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
      g.fillText(enumLabel(lane, lane.vs[i]), (x0 + x1) / 2, ym);
      g.restore();
    }
  }
}

// Force a repaint of every lane (time-mode change, resize) even when no new samples arrived.
function markDigitalDirty() {
  for (const l of digitalLanes.values()) l._sizedirty = true;
  redrawDigital();
  for (const l of digitalLanes.values()) l._sizedirty = false;
}

// Freeze/thaw the panel with the analog charts. On pause the right edge is pinned at the
// newest sample so the window stops advancing; samples keep buffering for the resume catch-up.
function setDigitalPaused(paused) {
  if (digitalPaused === paused) return;
  digitalPaused = paused;
  if (paused) {
    let mh = -Infinity, mt = -Infinity;
    for (const l of digitalLanes.values()) {
      const n = l.xsHost.length;
      if (n) { mh = Math.max(mh, l.xsHost[n - 1]); mt = Math.max(mt, l.xsTick[n - 1]); }
    }
    digitalFrozen = isFinite(mh) ? { host: mh, tick: mt } : null;
  } else {
    digitalFrozen = null;
  }
  for (const l of digitalLanes.values()) l.dirty = true;
  redrawDigital();
}

function unitOf(def, name) {
  if (!def) return null;
  const c = def.channels.find((ch) => ch.name === name);
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
  if (len > PLOT_CAP) {
    const drop = len - PLOT_CAP;
    chart.xsHost.splice(0, drop); chart.xsTick.splice(0, drop);
    for (const arr of chart.ys.values()) arr.splice(0, drop);
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
  const c = def.channels.find((ch) => ch.name === name);
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

  const win = document.createElement("div");
  win.className = "plot-win";
  for (const [secs, label] of PLOT_WINDOWS) {
    const b = document.createElement("button");
    b.textContent = label;
    if (secs === chart.window) b.classList.add("on");
    b.addEventListener("click", () => {
      chart.window = secs;
      win.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      chart.dirty = true;   // applies even while paused (redraw honours the freeze slice)
    });
    win.appendChild(b);
  }
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
    sw.className = "swatch"; sw.style.background = PLOT_COLORS[i % PLOT_COLORS.length];
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = chart.show.get(name); cb.style.display = "none";
    const txt = document.createElement("span"); txt.textContent = name;
    lab.append(cb, sw, txt);
    const unit = chart.unit.get(name);
    if (unit) { const u = document.createElement("span"); u.className = "unit"; u.textContent = unit; lab.appendChild(u); }
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
  $("plotCount").textContent = n ? `${n} channel${n === 1 ? "" : "s"}` : "";
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

function relBase() { return anchorTs == null ? 0 : anchorTs; }
function tickBase() { return anchorTick == null ? 0 : anchorTick; }

// Axis labels are bare numbers (no sign, no unit): the unit is shown once in the plots
// header (see syncTimeSeg). Relative modes are zeroed at the shared reset point.
function xAxisValues(u, splits) {
  return splits.map((v) => {
    if (timeMode === "tick") return String(Math.round(v - tickBase()));
    if (timeMode === "rel") return (v - relBase()).toFixed(1);
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
  if (timeMode === "tick") return Math.round(v - tickBase()) + " ms";
  if (timeMode === "rel") return (v - relBase()).toFixed(3) + " s";
  const d = new Date(v * 1000);
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${ms}`;
}

// Window the x axis to the last `window` (seconds for host/rel, ms for tick), anchored at
// the newest sample, so both live and frozen charts show a fixed-width strip.
function xRangeFor(chart) {
  return (u, dmin, dmax) => {
    if (!isFinite(dmax)) return [0, 1];
    const span = timeMode === "tick" ? chart.window * 1000 : chart.window;
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
      stroke: PLOT_COLORS[i % PLOT_COLORS.length],
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

function currentData(chart) {
  // host and rel share the host-time array (rel only shifts the display labels); tick uses
  // the MCU-tick array. Keeping data monotonic and shifting only labels avoids re-scaling.
  const xsAll = timeMode === "tick" ? chart.xsTick : chart.xsHost;
  if (chart.frozenLen === null) return [xsAll, ...chart.names.map((n) => chart.ys.get(n))];
  const n = chart.frozenLen;   // paused: show only up to the freeze point
  return [xsAll.slice(0, n), ...chart.names.map((nm) => chart.ys.get(nm).slice(0, n))];
}

// Repaint each chart's visible window. Paused charts are not skipped: they still honour
// user actions (window, x-axis, pause/resume) via the dirty flag, but currentData clamps
// them to the frozen slice so no new samples appear until resumed.
function redrawPlots() {
  const themeNow = root.getAttribute("data-theme") || "";
  for (const chart of charts.values()) {
    const w = chart.canvasEl.clientWidth;
    if (w <= 0) continue;   // section hidden or chart collapsed; nothing to draw
    const need = !chart.uplot
      || chart.uplot.series.length - 1 !== chart.names.length
      || themeNow !== plotTheme;
    if (need) { buildUplot(chart); continue; }
    if (chart.uplot.width !== w) chart.uplot.setSize({ width: w, height: 150 });
    if (chart.dirty) { chart.uplot.setData(currentData(chart)); chart.dirty = false; }
  }
  applyHoverCursor();   // re-apply the pinned log-hover cursor after the window pans
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

function resolveRowAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const ln = el && el.closest ? el.closest(".ln") : null;
  return ln && ln.__row ? ln.__row : null;
}

// The only writer of hoverRow. Gated on real pointer movement, so the synthetic mouseover/
// re-layout that fires when data scrolls under a still pointer can never re-point the line.
function paneMouseMove(e) {
  if (e.clientX === lastPx && e.clientY === lastPy) return;
  lastPx = e.clientX; lastPy = e.clientY;
  const row = resolveRowAt(e.clientX, e.clientY);
  if (row !== hoverRow) { hoverRow = row; applyHoverCursor(); }
}

function paneMouseLeave() {
  lastPx = lastPy = -1;
  hoverRow = null;
  clearHoverCursor();
}

function xForRow(row) {
  if (timeMode === "tick") { const t = lineTick(row); return t == null ? null : t; }
  return row.ts;   // host and rel are both drawn on the host-time array
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

// Idempotent projection of the single pinned row onto every chart. No hit-test and no hoverRow
// mutation, so the 200 ms redraw loop can re-apply it freely (the row/ts is fixed; only the
// window pans, moving valToPos smoothly with zero flicker).
function applyHoverCursor() {
  if (!hoverRow) { clearHoverCursor(); return; }
  const xval = xForRow(hoverRow);
  if (xval == null) { clearHoverCursor(); return; }
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
}

function clearHoverCursor() {
  for (const chart of charts.values()) {
    if (chart.uplot) chart.uplot.setCursor({ left: -10, top: -10 }, false, false);
  }
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
  updateShared();
}

function exportChart(chart) {
  const names = chart.names.filter((n) => chart.show.get(n));
  if (!names.length) return;
  const params = new URLSearchParams({
    names: names.join(","),
    last_ms: String(chart.window * 1000),
    format: chart.sid === null ? "long" : "wide",
  });
  const a = document.createElement("a");
  a.href = "/plot/export?" + params.toString();
  a.download = `plot-${chart.key}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
}

function initPlots() {
  // The time base is driven by the shared #timeSeg control (see setTimeMode).
  setInterval(() => { redrawPlots(); redrawDigital(); }, PLOT_REDRAW_MS);
}

// ---- boot --------------------------------------------------------------------------

initCmdBar();
initCan();
initPlots();
initTerminal();
refreshStatus();
setInterval(refreshStatus, 5000);   // port/version state changes rarely
setInterval(tickUptime, 1000);      // smooth local clock between polls
