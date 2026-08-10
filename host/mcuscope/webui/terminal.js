import { $, state, buffer, portColor, pad2, lineTick } from "./state.js";
import { ALL_CHANS, REGEX_BUDGET_MS, newPaneModel } from "./pane.js";
import { anyLive, bornPaused, freezeChanged, onFreezeChanged, pauseAll, pauseAllLabel,
         registerSurface } from "./freeze.js";
import { charts, scheduleResizeRedraw, onResizeRedraw, paneMouseMove, paneMouseLeave,
         clearAllCharts } from "./plots.js";
import { markDigitalDirty, clearAllDigital } from "./digital.js";
import { populateCmdPort } from "./cmdbar.js";

// ---- terminal: shared line buffer + dynamically added, per-pane filtered views -----
//
// One WebSocket (all ports) and one client-side ring buffer feed every pane; a pane is
// just a filtered projection of that buffer, so adding a pane costs nothing on the wire.
//
// Rendering is batched: incoming lines are queued and flushed once per animation frame
// (one reflow per pane per frame), and paused panes append nothing at all - the buffer
// keeps filling but the frozen pane does no DOM work, so CPU drops to idle when paused.

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
  if (pane.regex && !regexTest(pane, row.raw)) return false;
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
    // A firmware marker's raw line is stored whole ("!m @123 boot done"), so strip the
    // wire prefix here; its tick already shows in the timestamp column via lineTick.
    div.textContent = "marker: " + row.raw.replace(/^!m\s+(@\d+\s+)?/, "");
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
  // Rows are a fixed 18px (LINE_H) because the virtualizer computes scroll offsets from it, so
  // wrapping is not available and a 255-byte line loses its tail off the right edge - roughly
  // 30 characters survive at the 320px minimum pane width. The tooltip is the only escape that
  // costs the virtualizer nothing (a per-row horizontal scroller cannot fit in 18px, and making
  // the list itself scroll sideways would make the scroll extent jump, since only a screenful
  // of rows is measured at a time). CSS adds an ellipsis so a clipped line is visibly clipped.
  msg.title = row.raw;
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
//
// `shift` (flush only) allows the append-only path below: everything else - a scroll jump, a
// rebuild, a time-mode change - rebuilds the whole window, because the rows it already holds
// may need different markup.
function render(pane, shift = false) {
  const sc = pane.scrollEl;
  const total = pane.rows.length;
  // Cached: this read is interleaved with the writes below, so it forces a layout per pane
  // per flush. Invalidated (set to 0) wherever the pane can change height.
  if (!pane.viewH) pane.viewH = sc.clientHeight || 300;
  const visCount = Math.ceil(pane.viewH / LINE_H) + OVERSCAN * 2;
  let first;
  if (pane.autoscroll) {
    first = Math.max(0, total - visCount);
  } else {
    const maxFirst = Math.max(0, total - visCount);
    first = Math.min(Math.max(0, Math.floor(sc.scrollTop / LINE_H) - OVERSCAN), maxFirst);
  }
  const last = Math.min(total, first + visCount);

  if (!(shift && shiftWindow(pane, first, last))) {
    const frag = document.createDocumentFragment();
    const els = [];
    for (let i = first; i < last; i++) {
      const el = buildLine(pane, pane.rows[i]);
      els.push(el);
      frag.appendChild(el);
    }
    pane.vlist.replaceChildren(frag);
    pane.domEls = els;
  }
  pane.winFirst = first;
  pane.winLast = last;
  pane.vlist.style.paddingTop = (first * LINE_H) + "px";
  pane.vlist.style.paddingBottom = ((total - last) * LINE_H) + "px";
  updateShown(pane);
  if (pane.autoscroll) { pane.selfScroll = true; sc.scrollTop = 1e9; }
}

// The autoscroll case: the window slid forward over rows the DOM already holds, so drop the
// rows that scrolled off the top and append the new ones instead of rebuilding a screenful of
// elements 30 times a second. Returns false - and the caller rebuilds - unless every retained
// element still stands for exactly the row now at its index, checked by identity rather than
// trusted from bookkeeping (a VIEW_MAX trim renumbers the whole array).
function shiftWindow(pane, first, last) {
  const els = pane.domEls;
  if (!pane.autoscroll || !els || !els.length) return false;
  if (first < pane.winFirst || last < pane.winLast) return false;   // jumped back: rebuild
  if (els.length !== pane.winLast - pane.winFirst) return false;
  const shift = first - pane.winFirst;
  if (shift >= els.length) return false;                            // no overlap left to reuse
  for (let i = 0; i + shift < els.length; i++) {
    if (els[i + shift].__row !== pane.rows[first + i]) return false;
  }
  for (let i = 0; i < shift; i++) els[i].remove();
  const kept = els.slice(shift);
  const frag = document.createDocumentFragment();
  for (let i = pane.winLast; i < last; i++) {
    const el = buildLine(pane, pane.rows[i]);
    kept.push(el);
    frag.appendChild(el);
  }
  pane.vlist.appendChild(frag);
  pane.domEls = kept;
  return true;
}

// Coalesce scroll-driven re-virtualization into one render per frame per pane.
let renderScheduled = false;
const renderQueue = new Set();
// Every path that changes pane geometry goes through scheduleResizeRedraw (window resize,
// sidebar and CAN/plot divider drags), so the cached scrollback height is dropped there
// rather than on window.resize alone.
onResizeRedraw(() => panes.forEach((p) => { p.viewH = 0; scheduleRender(p); }));

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
    pane.frozenRows = null;
    pane.jumpBtn.classList.remove("show");
    rebuild(pane);             // fold in whatever arrived while frozen, then snap to the latest
  } else {
    pane.frozenId = state.maxId;   // the pane freezes here; rebuild may not reach past it
    // Snapshot what the freeze covers: the shared buffer is a ring, so the rows behind
    // frozenId eventually rotate out and a rebuild would find nothing left to show. Row
    // objects are shared, so this is a list of references.
    pane.frozenRows = buffer.filter((row) => row.id > pane.clearId);
    updateJump(pane);
    pane.jumpBtn.classList.add("show");
  }
  freezeChanged();   // as the other two surfaces do: this also ends the pause-all latch
}

// The panes as one freeze surface. plots.js and digital.js register their own, so nothing
// here needs to know their internals - and the polarity is converted once, here, rather
// than at the fan-out where two of the three setters disagreed with the third.
registerSurface("panes", {
  isLive: () => panes.some((p) => p.autoscroll),
  setPaused: (paused) => panes.forEach((p) => setAutoscroll(p, !paused)),
  // Panes export individually; the group's bound is the earliest freeze among the paused.
  watermark: () => {
    const frozen = panes.filter((p) => !p.autoscroll).map((p) => p.frozenId);
    return frozen.length ? Math.min(...frozen) : null;
  },
});

function updateShared() {
  $("pauseAllBtn").textContent = pauseAllLabel();
}

onFreezeChanged(updateShared);

// Recompute a pane's line set from the shared buffer (its filter changed). Preserves the
// pane's live/paused state - re-filtering never resumes a paused pane.
//
// A paused pane is frozen at the rows it held when it was paused (pane.frozenId), so
// re-filtering still applies while paused but the row set can never grow. Without that bound
// the two sibling callers that rebuild every pane unconditionally - the end of every
// runBackfill (so every WS open and reconnect) and the high-rate release - folded in
// everything that had arrived since, i.e. the pane un-paused itself while the pill still read
// "paused". plots.js snapshots into chart.frozen for the same reason.
// A frozen pane's "N new" backlog, re-derived from the shared buffer instead of trusted as a
// running total. Two things put that total wrong, and both end in a rebuild:
//  - a filter change: the increments were counted against the OLD filter, while everything
//    else about the pane is re-filtered here;
//  - the high-rate shed: the panes are not fed at all above HIGH_RATE_ON (api.js), so nothing
//    is counted for them either, and the count stayed short for the rest of the session.
// The buffer is a ring, so a backlog past BUFFER_MAX reads as BUFFER_MAX; the pane could not
// have shown more than that on resume either, since resuming rebuilds from this same buffer.
function countPending(pane) {
  let n = 0;
  for (const row of buffer) {
    if (row.id > pane.frozenId && row.id > pane.clearId && matches(pane, row)) n += 1;
  }
  return n;
}

function rebuild(pane) {
  const top = pane.autoscroll ? Infinity : pane.frozenId;
  // A frozen pane re-filters its own snapshot: the shared buffer has moved on past the freeze.
  const src = pane.autoscroll ? buffer : (pane.frozenRows || buffer);
  refillRegexBudget(pane);
  const hadRegex = pane.regex !== null;
  const select = () =>
    src.filter((row) => row.id > pane.clearId && row.id <= top && matches(pane, row));
  pane.rows = select();
  // The budget dropped the pattern part-way through the pass above, leaving a half-filtered
  // set; re-derive once (now pattern-free, so cheap) to match what the input box says.
  if (hadRegex && pane.regex === null) pane.rows = select();
  if (pane.rows.length > VIEW_MAX) pane.rows.splice(0, pane.rows.length - VIEW_MAX);
  // The backlog is now folded into rows, so reset the "N new" counter and drop anything still
  // queued: those rows are already in the shared buffer, so the next flush would append them twice.
  if (pane.autoscroll) pane.pending = 0;
  else pane.pending = countPending(pane);   // a frozen pane's backlog stands, but is re-derived
  pane.queue.length = 0;
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
  if (document.hidden) return;   // see the visibilitychange handler in initTerminal
  for (const pane of panes) {
    if (!pane.autoscroll) {
      // Frozen: the view is untouched and `pending` was already counted up as the rows
      // arrived (routeLiveRow), so there is nothing here but the jump button to refresh.
      if (!pane.pendingDirty) continue;
      pane.pendingDirty = false;
      pane.queue.length = 0;   // a pane paused mid-flight can still hold live-path rows
      updateJump(pane);
      continue;
    }
    if (!pane.queue.length) continue;
    for (const r of pane.queue) pane.rows.push(r);
    pane.queue.length = 0;
    if (pane.rows.length > VIEW_MAX) pane.rows.splice(0, pane.rows.length - VIEW_MAX);
    render(pane, true);   // append-only where the window merely slid forward
  }
}

// Cap the pattern length (mirrors the daemon's MAX_MATCH_LEN) so a pathological pattern can
// not be constructed, and flag an invalid/too-long pattern visibly (red .invalid box + a
// tooltip) instead of silently dropping to an unfiltered view.
const MAX_MATCH_LEN = 200;

// The length cap is only half of what the daemon does with this grammar: it also passes
// `timeout=` to `regex`, because length bounds nothing that matters - "(a+)+$" is 6
// characters and took 36 s against one 29-character line here, per keystroke, in the very box
// that would undo it. JavaScript has no regex timeout, so budget the wall clock around the
// matching instead: past the budget the pattern is dropped, the box turns red and says so
// (the view below it is unfiltered, and must never read as filtered), and that source is
// never run again. One bounded hiccup instead of an unrecoverable freeze.
const SLOW_MSG = `pattern dropped: it spent over ${REGEX_BUDGET_MS} ms matching (catastrophic `
  + "backtracking). The lines below are UNFILTERED - edit the pattern to filter again.";

// One filtering episode's budget: a whole rebuild is one episode, and so is a single live row
// (api.js), so an ordinary pattern's per-row cost can never accumulate into a false drop.
function refillRegexBudget(pane) { pane.regexBudget = REGEX_BUDGET_MS; }

function markInvalid(pane, why) {
  pane.matchInput.classList.add("invalid");
  pane.matchInput.title = why;
}

function regexTest(pane, text) {
  const t0 = performance.now();
  const ok = pane.regex.test(text);
  pane.regexBudget -= performance.now() - t0;
  if (pane.regexBudget < 0) {
    pane.regexSlow = pane.regexSrc;   // remember it, so no later call re-runs this source
    pane.regex = null;
    markInvalid(pane, SLOW_MSG);
  }
  return ok;
}

function applyRegex(pane, src) {
  pane.regexSrc = src;
  const inp = pane.matchInput;
  if (!src) { pane.regex = null; inp.classList.remove("invalid"); inp.title = "Client-side regex filter"; return; }
  if (src.length > MAX_MATCH_LEN) {
    pane.regex = null;
    markInvalid(pane, `pattern too long (max ${MAX_MATCH_LEN} chars)`);
    return;
  }
  if (src === pane.regexSlow) { pane.regex = null; markInvalid(pane, SLOW_MSG); return; }
  try {
    pane.regex = new RegExp(src);
    refillRegexBudget(pane);
    inp.classList.remove("invalid");
    inp.title = "Client-side regex filter";
  } catch (e) {
    pane.regex = null;
    markInvalid(pane, "invalid pattern: " + e.message);
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

// Called on every /status poll (5 s). Rebuilding a <select>'s options closes it, so doing this
// unconditionally shut an open port dropdown under the user's cursor twice a minute; the alias
// set almost never changes, so compare first and only touch the DOM when it really did.
function setKnownPorts(aliases) {
  const prev = state.knownAliases;
  const same = aliases.length === prev.length && aliases.every((a, i) => a === prev[i]);
  state.knownAliases = aliases;
  if (same) return;
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
  const pane = newPaneModel(cfg, {
    el,
    scrollEl,
    vlist,
    portSel: el.querySelector(".port-sel"),
    matchInput: el.querySelector(".match"),
    pill: el.querySelector(".pill"),
    jumpBtn: el.querySelector(".jump"),
    shownEl: el.querySelector(".shown"),
  });

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
  // A pane added while the UI is frozen joins the freeze; otherwise the shared label has to be
  // recomputed anyway, since a new live pane makes "resume all" a lie.
  if (bornPaused()) setAutoscroll(pane, false);
  else updateShared();
  updatePaneButtons();
  persistState();
}

function closePane(pane) {
  if (panes.length <= 1) return;
  const i = panes.indexOf(pane);
  if (i < 0) return;
  panes.splice(i, 1);
  // Cancel the debounced regex rebuild, or a pane closed within the debounce window
  // fires rebuild() on a pane that is no longer in `panes` and whose element is detached.
  clearTimeout(pane.regexTimer);
  pane.el.remove();
  updateShared();   // closing the last live pane changes what the shared button should read
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
    // Freeze the whole UI at one instant, or thaw it. Target = pause if anything is live.
    pauseAll(anyLive());
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
  window.addEventListener("resize", () => {
    scheduleResizeRedraw();   // rAF-coalesced, as the divider drags are
  });
  // A hidden tab renders nothing: rows keep queueing (bounded by VIEW_MAX) and the flush
  // below folds them in once the tab is looked at again.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) flush(); });
  loadState();
  updateShared();
}

export { VIEW_MAX, REGEX_BUDGET_MS,
         panes, matches, rebuild, render, updateJump, scheduleFlush, refillRegexBudget,
         applyRegex, setAutoscroll,
         setKnownPorts, updateShared, initTerminal };
