// MCUscope web UI (SPEC 9.1). Vanilla JS, no build step, no network fetches
// beyond this daemon. All API calls are root-relative so the page works unchanged
// whether it is served from 127.0.0.1 or across the LAN (bind mcuscoped to 0.0.0.0).
//
// Build progress: status/setup bar is live. Terminal, CAN table and command box
// are wired in later steps.

import { $, sidebar, state, hooks } from "./state.js";
import { initTheme } from "./theme.js";
import { refreshStatus, tickUptime, initStatusbar, flashDaemonError } from "./statusbar.js";
import { initSettings } from "./settings.js";
import { connectWs, setAuthFailed } from "./api.js";
import { canRows, renderCan, initCan } from "./can.js";
import { initCmdBar } from "./cmdbar.js";
import { initPlots, resizePlots, applyHoverCursor } from "./plots.js";
import { markDigitalDirty } from "./digital.js";
import { initTerminal, updateShared } from "./terminal.js";

// ---- cross-module hook wiring (breaks the plots<->digital and *->terminal cycles) ----
hooks.reapplyCursor = applyHoverCursor;   // digital panel hover re-projects the shared cursor
hooks.liveChanged = updateShared;         // chart/digital pause toggles refresh the pause-all label
hooks.authFailed = setAuthFailed;         // token prompt cancelled/exhausted: say so in the stream chip
hooks.reportError = flashDaemonError;     // e.g. a failed CSV export: flash the daemon chip with the reason

initTheme();
initStatusbar();

// ---- sidebar: section switch, collapse, resize (layout, no API) --------------------

const ws = $("workspace");

function setView(v) {
  sidebar.setAttribute("data-view", v);
  document.querySelectorAll("#sideSeg button").forEach((x) => x.classList.toggle("on", x.dataset.view === v));
  // Plot charts sized to a hidden (0-width) container need a resize once shown.
  if (v !== "can") requestAnimationFrame(resizePlots);
  // The CAN timer skips work while hidden; repaint once on return so ages are current.
  if ((v === "can" || v === "both") && canRows.size) renderCan();
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
  $("popoutBtn").textContent = sideExpanded ? "↔ restore" : "↔ expand";
  requestAnimationFrame(resizePlots);
});

// Coalesce drag-driven resizes into one redraw per frame: the CSS var (--side-w/--can-h) is
// written immediately for smooth visual feedback, but the expensive uPlot.setSize + lane
// repaint is deferred to the next animation frame, so a 120 Hz pointer stream costs one redraw
// per displayed frame instead of one per event.
let resizeRaf = 0;
function scheduleResizeRedraw() {
  if (resizeRaf) return;
  resizeRaf = requestAnimationFrame(() => { resizeRaf = 0; resizePlots(); markDigitalDirty(); });
}

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
  scheduleResizeRedraw();
});
resizer.addEventListener("pointerup", (e) => {
  dragging = false; resizer.classList.remove("drag");
  try { resizer.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
});
resizer.addEventListener("dblclick", () => ws.style.setProperty("--side-w", "360px"));

// In "both" mode a horizontal divider resizes CAN vs Plots (mirrors #resizer). The element
// is display:none outside both mode, so these handlers are inert there and attach freely.
const canPlotDivider = $("canPlotDivider");
const sideBody = document.querySelector(".side-body");
let cpDragging = false;
canPlotDivider.addEventListener("pointerdown", (e) => {
  cpDragging = true; canPlotDivider.classList.add("drag"); canPlotDivider.setPointerCapture(e.pointerId);
});
canPlotDivider.addEventListener("pointermove", (e) => {
  if (!cpDragging) return;
  const rect = sideBody.getBoundingClientRect();
  const h = Math.max(40, Math.min(e.clientY - rect.top, rect.height - 80));
  sidebar.style.setProperty("--can-h", h + "px");
  scheduleResizeRedraw();
});
canPlotDivider.addEventListener("pointerup", (e) => {
  cpDragging = false; canPlotDivider.classList.remove("drag");
  try { canPlotDivider.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
});
canPlotDivider.addEventListener("dblclick", () => {
  sidebar.style.setProperty("--can-h", "45%");
  resizePlots(); markDigitalDirty();
});

// ---- boot --------------------------------------------------------------------------

initCmdBar();
initCan();
initPlots();
initTerminal();
initSettings();
// Open the socket first and queue live rows, then backfill and merge, so lines arriving
// between the /lines snapshot and the subscription are not lost (see api.js).
connectWs();
refreshStatus();
setInterval(refreshStatus, 5000);   // port/version state changes rarely
setInterval(tickUptime, 1000);      // smooth local clock between polls
