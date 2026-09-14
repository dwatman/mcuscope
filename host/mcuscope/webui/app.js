// MCUscope web UI (SPEC 9.1). Vanilla JS, no build step, no network fetches
// beyond this daemon. All API calls are root-relative so the page works unchanged
// whether it is served from 127.0.0.1 or across the LAN (bind mcuscoped to 0.0.0.0).
//
// Build progress: status/setup bar is live. Terminal, CAN table and command box
// are wired in later steps.

import { $, sidebar, state, hooks } from "./state.js";
import { initTheme } from "./theme.js";
import { refreshStatus, initStatusbar, flashDaemonError } from "./statusbar.js";
import { initSettings } from "./settings.js";
import { connectWs, setAuthFailed } from "./api.js";
import { canRows, renderCan, initCan, setPaneFilter } from "./can.js";
import { initCmdBar } from "./cmdbar.js";
import { initPlots, resizePlots, scheduleResizeRedraw, applyHoverCursor } from "./plots.js";
// Namespace import: the CAN id filter below is optional wiring, and a named import of an
// export terminal.js does not have would fail the whole module graph at link time.
import * as terminal from "./terminal.js";
import { initExportDialog } from "./exportdlg.js";
import { LAYOUT_KEY, SIDE_W_DEFAULT, clampSideW, parseLayout, sideWidthFor } from "./layout.js";

// ---- cross-module hook wiring (breaks the plots<->digital and *->terminal cycles) ----
hooks.reapplyCursor = applyHoverCursor;   // digital panel hover re-projects the shared cursor
hooks.authFailed = setAuthFailed;         // token prompt cancelled/exhausted: say so in the stream chip
hooks.reportError = flashDaemonError;     // e.g. a failed CSV export: flash the chip, reason in the strip
// Clicking a CAN id narrows a terminal pane to that id's raw frames. The hook goes this way
// round so the CAN table stays out of terminal.js's import graph.
if (typeof terminal.filterPaneTo === "function") setPaneFilter(terminal.filterPaneTo);

initTheme();
initStatusbar();

// ---- sidebar: section switch, collapse, resize (layout, no API) --------------------

const ws = $("workspace");

function setView(v) {
  sidebar.setAttribute("data-view", v);
  document.querySelectorAll("#sideSeg button").forEach((x) => {
    const on = x.dataset.view === v;
    x.classList.toggle("on", on);
    x.setAttribute("aria-checked", on ? "true" : "false");   // the group is a radiogroup
  });
  // Plot charts sized to a hidden (0-width) container need a resize once shown.
  if (v !== "can") requestAnimationFrame(resizePlots);
  // The CAN timer skips work while hidden; repaint once on return so ages are current.
  if ((v === "can" || v === "both") && canRows.size) renderCan();
}
document.querySelectorAll("#sideSeg button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

// The sidebar's width, expand, hide and CAN cap are remembered per browser, like the pane
// layout; layout.js validates what comes back, since localStorage is hand-editable and may
// have been written in a wider window.
const layout = (() => {
  try { return parseLayout(localStorage.getItem(LAYOUT_KEY)); } catch { return parseLayout(null); }
})();
function saveLayout() {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* private mode */ }
}
function applySideWidth() {
  const w = sideWidthFor(layout, ws.clientWidth);
  ws.style.setProperty("--side-w", (w === null ? SIDE_W_DEFAULT : w) + "px");
  $("popoutBtn").textContent = layout.expanded ? "↔ restore" : "↔ expand";
}
if (layout.hidden) ws.classList.add("collapsed");
applySideWidth();
if (layout.canCap !== null) sidebar.style.setProperty("--can-h", layout.canCap + "%");

$("collapseBtn").addEventListener("click", () => {
  ws.classList.add("collapsed");
  layout.hidden = true;
  saveLayout();
});
$("reopenBtn").addEventListener("click", () => {
  ws.classList.remove("collapsed");
  layout.hidden = false;
  saveLayout();
});
// Expand: widen the sidebar so the charts get more room; a second click restores the width
// it had before (the dragged one, else the default).
$("popoutBtn").addEventListener("click", () => {
  ws.classList.remove("collapsed");
  layout.hidden = false;
  layout.expanded = !layout.expanded;
  applySideWidth();
  saveLayout();
  requestAnimationFrame(resizePlots);
});

// The CSS var (--side-w/--can-h) is written immediately for smooth visual feedback while
// dragging; the expensive uPlot.setSize + lane repaint is coalesced to one per frame by
// scheduleResizeRedraw (plots.js), which the window resize handler shares.

const resizer = $("resizer");
let dragging = false;
let dragW = null;   // the width the drag last set, saved when it ends
resizer.addEventListener("pointerdown", (e) => {
  dragging = true; resizer.classList.add("drag"); resizer.setPointerCapture(e.pointerId);
});
resizer.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  // Leave room for the terminal's 320px min column and the 6px divider, or the grid
  // overflows the viewport and the page scrolls sideways.
  dragW = clampSideW(ws.getBoundingClientRect().right - e.clientX, ws.clientWidth);
  ws.style.setProperty("--side-w", dragW + "px");
  scheduleResizeRedraw();
});
resizer.addEventListener("pointerup", (e) => {
  resizer.classList.remove("drag");
  try { resizer.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
  if (dragging && dragW !== null) {
    // A dragged width is an explicit choice, so it ends the expanded state.
    layout.sideW = dragW;
    layout.expanded = false;
    applySideWidth();
    saveLayout();
  }
  dragging = false; dragW = null;
});
resizer.addEventListener("dblclick", () => {
  layout.sideW = null;
  layout.expanded = false;
  applySideWidth();
  saveLayout();
  scheduleResizeRedraw();
});

// In "both" mode a horizontal divider resizes CAN vs Plots (mirrors #resizer). The element
// is display:none outside both mode, so these handlers are inert there and attach freely.
const canPlotDivider = $("canPlotDivider");
const sideBody = document.querySelector(".side-body");
let cpDragging = false;
let cpCap = null;   // the cap the drag last set, as a percent of the sidebar body
canPlotDivider.addEventListener("pointerdown", (e) => {
  cpDragging = true; canPlotDivider.classList.add("drag"); canPlotDivider.setPointerCapture(e.pointerId);
});
canPlotDivider.addEventListener("pointermove", (e) => {
  if (!cpDragging) return;
  const rect = sideBody.getBoundingClientRect();
  const h = Math.max(40, Math.min(e.clientY - rect.top, rect.height - 80));
  // A percent, not pixels, so the cap still means the same split in a shorter window.
  cpCap = rect.height > 0 ? Math.min(95, Math.max(5, Math.round((h / rect.height) * 1000) / 10)) : null;
  sidebar.style.setProperty("--can-h", h + "px");
  scheduleResizeRedraw();
});
canPlotDivider.addEventListener("pointerup", (e) => {
  canPlotDivider.classList.remove("drag");
  try { canPlotDivider.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
  if (cpDragging && cpCap !== null) {
    layout.canCap = cpCap;
    sidebar.style.setProperty("--can-h", cpCap + "%");
    saveLayout();
  }
  cpDragging = false; cpCap = null;
});
canPlotDivider.addEventListener("dblclick", () => {
  layout.canCap = null;
  sidebar.style.setProperty("--can-h", "45%");
  saveLayout();
  scheduleResizeRedraw();
});

// ---- boot --------------------------------------------------------------------------

initCmdBar();
initCan();
initPlots();
terminal.initTerminal();
initSettings();
initExportDialog();
// Open the socket first and queue live rows, then backfill and merge, so lines arriving
// between the /lines snapshot and the subscription are not lost (see api.js).
connectWs();
refreshStatus();
// Both polls idle in a hidden tab (nobody is looking at the status bar); the visibilitychange
// handler below refreshes immediately on return so the bar never shows stale state.
setInterval(() => { if (!document.hidden) refreshStatus(); }, 5000);   // port/version state changes rarely
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  refreshStatus();
  // The CAN timer also idles while hidden; repaint once so ages/counts are current.
  const v = sidebar.getAttribute("data-view");
  if ((v === "can" || v === "both") && canRows.size) renderCan();
});
