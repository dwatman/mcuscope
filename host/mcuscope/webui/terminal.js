import { $, state, buffer, portColor, pad2, lineTick } from "./state.js";
import { charts, setChartPaused, resizePlots, paneMouseMove, paneMouseLeave,
         clearAllCharts } from "./plots.js";
import { markDigitalDirty, setDigitalPaused, isDigitalPaused, digitalLanes,
         clearAllDigital } from "./digital.js";
import { populateCmdPort } from "./cmdbar.js";

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
const VIEW_MAX = 5000;     // DOM lines kept per pane
const MAX_PANES = 5;       // enough for real use; one socket feeds them all
const REGEX_DEBOUNCE_MS = 200;
const FLUSH_MS = 33;       // ~30 fps: batch appends into one render per frame per pane
const LINE_H = 18;         // fixed row height (must match .ln height in style.css)
const OVERSCAN = 8;        // rows rendered above/below the viewport for smooth scrolling
const panes = [];
function fmtTs(row) {
  if (state.timeMode === "rel") {
    const base = state.anchorTs == null ? row.ts : state.anchorTs;
    return (row.ts - base).toFixed(3) + "s";   // sign only when negative
  }
  if (state.timeMode === "tick") {
    const t = lineTick(row);
    return t == null ? "-" : String(t - (state.anchorTick == null ? 0 : state.anchorTick));
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

// True while anything the pause-all button governs - a terminal pane, an analog chart, or the
// digital panel - is still live. One definition so the button label and what it toggles agree.
function anyLive() {
  return panes.some((p) => p.autoscroll)
    || [...charts.values()].some((c) => !c.paused)
    || (digitalLanes.size > 0 && !isDigitalPaused());
}

function updateShared() {
  $("pauseAllBtn").textContent = anyLive() ? "pause all" : "resume all";
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

// Cap the pattern length (mirrors the daemon's MAX_MATCH_LEN) so a pathological pattern can
// not be constructed, and flag an invalid/too-long pattern visibly (red .invalid box + a
// tooltip) instead of silently dropping to an unfiltered view.
const MAX_MATCH_LEN = 200;
function applyRegex(pane, src) {
  pane.regexSrc = src;
  const inp = pane.matchInput;
  if (!src) { pane.regex = null; inp.classList.remove("invalid"); inp.title = "Client-side regex filter"; return; }
  if (src.length > MAX_MATCH_LEN) {
    pane.regex = null;
    inp.classList.add("invalid");
    inp.title = `pattern too long (max ${MAX_MATCH_LEN} chars)`;
    return;
  }
  try {
    pane.regex = new RegExp(src);
    inp.classList.remove("invalid");
    inp.title = "Client-side regex filter";
  } catch (e) {
    pane.regex = null;
    inp.classList.add("invalid");
    inp.title = "invalid pattern: " + e.message;
  }
}

function populatePortSelect(pane) {
  const sel = pane.portSel;
  const cur = pane.port;
  const opts = ["all", ...state.knownAliases];
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
  state.knownAliases = aliases;
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
    const on0 = pane.channels.has(ch);
    if (!on0) chk.classList.add("off");
    chk.setAttribute("aria-pressed", on0 ? "true" : "false");
    // .chk is a <button> so Enter/Space toggle it natively (keyboard-operable, SPEC 9.1 a11y).
    chk.addEventListener("click", () => {
      const on = !pane.channels.has(ch);
      if (on) pane.channels.add(ch); else pane.channels.delete(ch);
      chk.classList.toggle("off", !on);
      chk.setAttribute("aria-pressed", on ? "true" : "false");
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
    pane.clearId = state.maxId; pane.rows = []; pane.queue.length = 0; pane.pending = 0;
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
    timeMode: state.timeMode,
    panes: panes.map((p) => ({ port: p.port, channels: [...p.channels], regex: p.regexSrc })),
  };
  try { localStorage.setItem("termState", JSON.stringify(st)); } catch { /* private mode */ }
}

function syncTimeSeg() {
  document.querySelectorAll("#timeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.time === state.timeMode));
  const lbl = $("plotXLabel");
  if (lbl) lbl.textContent = { host: "x: host", tick: "x: tick (ms)", rel: "x: rel (s)" }[state.timeMode];
}

// One time base for everything: re-render the panes' timestamp column and repaint the plot
// x axis. The relative-time zero (state.anchorTs) is shared, so rel mode lines up across both.
function setTimeMode(mode) {
  state.timeMode = mode;
  syncTimeSeg();
  panes.forEach((p) => render(p));
  for (const chart of charts.values()) chart.dirty = true;
  markDigitalDirty();
  persistState();
}

function loadState() {
  let st = null;
  try { st = JSON.parse(localStorage.getItem("termState")); } catch { /* ignore */ }
  if (st && typeof st.timeMode === "string") state.timeMode = st.timeMode;
  else if (st && st.rel === true) state.timeMode = "rel";   // migrate the old boolean
  let cfgs = st && Array.isArray(st.panes) ? st.panes : null;
  if (!cfgs || !cfgs.length) cfgs = [{ port: "all", channels: ALL_CHANS, regex: "" }];
  for (const c of cfgs) addPane(c);
  syncTimeSeg();
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
    const live = anyLive();
    panes.forEach((p) => setAutoscroll(p, !live));
    charts.forEach((c) => setChartPaused(c, live));
    setDigitalPaused(live);
  });
  $("clearAllBtn").addEventListener("click", () => {
    state.anchorTs = null; state.anchorTick = null;   // re-zero relative time and tick from here
    // selfScroll: the empty-pane scrollTop clamp must not auto-resume a paused pane (see per-pane clear).
    panes.forEach((p) => {
      p.clearId = state.maxId; p.rows = []; p.queue.length = 0; p.pending = 0;
      p.selfScroll = true; render(p); updateJump(p);
    });
    clearAllCharts();     // destroy the analog charts (plots.js)
    clearAllDigital();    // clear + reset the digital panel (digital.js)
    updateShared();
  });
  window.addEventListener("resize", () => { panes.forEach(scheduleRender); resizePlots(); markDigitalDirty(); });
  loadState();
  updateShared();
}

export { panes, matches, rebuild, render, updateJump, scheduleFlush,
         setKnownPorts, updateShared, initTerminal };
