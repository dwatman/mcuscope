// ---- what the sidebar layout and the chart titles remember per browser ----------------
//
// DOM-free, so the read-side validation is testable: localStorage is hand-editable and
// outlives the window size it was saved under. app.js and plots.js do the DOM half.

export const LAYOUT_KEY = "mcuscope.layout";
export const TITLES_KEY = "mcuscope.plotTitles";
export const SIDE_W_DEFAULT = 360;
const SIDE_W_MIN = 260;
const TERMINAL_MIN = 326;          // the terminal's 320 px column plus the 6 px divider
const EXPANDED_SHARE = 0.6;        // the expand toggle's share of the workspace
const CAN_CAP_MIN = 5, CAN_CAP_MAX = 95;   // percent of the sidebar body
export const TITLE_MAX = 32;

// {sideW: px or null, expanded, hidden, canCap: percent or null}; anything unreadable or out
// of range falls back to its default, field by field.
export function parseLayout(raw) {
  const out = { sideW: null, expanded: false, hidden: false, canCap: null };
  let v;
  try { v = JSON.parse(raw); } catch { return out; }
  if (!v || typeof v !== "object") return out;
  if (Number.isFinite(v.sideW) && v.sideW > 0) out.sideW = v.sideW;
  out.expanded = v.expanded === true;
  out.hidden = v.hidden === true;
  if (Number.isFinite(v.canCap) && v.canCap >= CAN_CAP_MIN && v.canCap <= CAN_CAP_MAX) out.canCap = v.canCap;
  return out;
}

// The widest the sidebar may be in a workspace `wsWidth` px wide, leaving the terminal its column.
export function clampSideW(w, wsWidth) {
  return Math.round(Math.max(SIDE_W_MIN, Math.min(w, wsWidth - TERMINAL_MIN)));
}

// Arrow keys on the divider: the sidebar sits right of it, so Left widens it. Returns the new
// width from `current`, clamped like a drag, or null for a key that is not a resize.
const KEY_STEP = 20;
export function nudgeSideW(current, key, shift, wsWidth) {
  const dir = { ArrowLeft: 1, ArrowRight: -1 }[key];
  if (!dir) return null;
  return clampSideW(current + dir * KEY_STEP * (shift ? 5 : 1), wsWidth);
}

// The sidebar width a layout asks for in this workspace, or null for the stylesheet default.
// A width saved in a wider window is clamped to this one; expanded is a share, not a width,
// so it follows the window. A workspace not laid out yet (0) applies the stored width as is.
export function sideWidthFor(layout, wsWidth) {
  if (layout.expanded && wsWidth > 0) return clampSideW(wsWidth * EXPANDED_SHARE, wsWidth);
  if (layout.sideW === null) return null;
  return wsWidth > 0 ? clampSideW(layout.sideW, wsWidth) : Math.round(layout.sideW);
}

// A chart title as typed: trimmed and bounded; empty or whitespace means "no custom title".
export function cleanTitle(text) {
  const t = String(text == null ? "" : text).trim().slice(0, TITLE_MAX).trim();
  return t || null;
}

// Stored titles, keyed by chart key. Null-prototyped: the keys hold device-supplied names.
export function parseTitles(raw) {
  const out = Object.create(null);
  let v;
  try { v = JSON.parse(raw); } catch { return out; }
  if (!v || typeof v !== "object" || Array.isArray(v)) return out;
  for (const k of Object.keys(v)) {
    const t = typeof v[k] === "string" ? cleanTitle(v[k]) : null;
    if (t) out[k] = t;
  }
  return out;
}

// A widget whose head shows less than this is as good as unseen.
const FOLD_PEEK = 24;

// The items ({name, top}, tops in the scroller's coordinates) that start below its visible
// bottom, in document order.
export function belowFold(items, viewBottom) {
  return items.filter((it) => it.top > viewBottom - FOLD_PEEK);
}
