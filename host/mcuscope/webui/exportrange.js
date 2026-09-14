// ---- the remembered export range, and the query params it becomes --------------------
//
// One range is shared by every panel's export dialog, so picking a session once covers the
// terminal, plot, digital and CAN exports that follow. DOM-free (like timewindow.js) because
// the interesting parts - what a mode sends, and the watermark rule below - are otherwise
// only reachable through a dialog the test stub cannot lay out.

const KEY = "mcuscope.exportRange";
export const MODES = ["session", "clock", "shown"];

// `session: null` sends no session param, which the daemon reads as the whole capture; the
// dialog returns to the open session by preselecting it in its list (exportdlg.js fillSessions).
export function defaultRange() {
  return { mode: "session", session: null, fromTs: null, toTs: null };
}

export function reset() { return defaultRange(); }

function num(v) { return typeof v === "number" && Number.isFinite(v) ? v : null; }

// localStorage is hand-editable and survives a version change. An unrecognised MODE falls back
// whole, because nothing else in the record means anything without it; the BOUNDS fall back per
// field, each to null, which is "no bound" and the same thing a fresh range sends.
export function validate(o) {
  if (!o || typeof o !== "object" || !MODES.includes(o.mode)) return defaultRange();
  const session = typeof o.session === "string" || typeof o.session === "number" ? String(o.session) : null;
  return { mode: o.mode, session, fromTs: num(o.fromTs), toTs: num(o.toTs) };
}

export function loadRange() {
  try { return validate(JSON.parse(localStorage.getItem(KEY))); } catch { return defaultRange(); }
}

export function saveRange(range) {
  try { localStorage.setItem(KEY, JSON.stringify(validate(range))); } catch { /* private mode */ }
}

// Clock bounds the wrong way round select nothing; the dialog refuses them inline rather
// than sending a request the daemon would 400.
export function inverted(range) {
  return range.mode === "clock" && range.fromTs != null && range.toTs != null && range.toTs < range.fromTs;
}

// How far below the first shown row since_ts sits: the daemon's lower bound is exclusive
// (`ts > since_ts`), and a row at exactly the edge is on screen.
export const SHOWN_EDGE_S = 1e-6;

// The daemon params for this range. `watermark` is the calling surface's frozen line id
// (null while live) and `shown` the host-time window it draws, {fromTs, toTs} inclusive, or
// null. The window goes as absolute edges: a duration is measured back from the id_to row,
// which can be much later than the surface's own newest sample or row.
//
// id_to rides along in EVERY mode, not just "shown": a paused surface must never export past
// what it shows (freeze.js, SPEC 9.1), and the daemon intersects every bound it is given, so
// a session or clock range narrowed by the watermark is still that range.
export function params(range, { watermark = null, shown = null } = {}) {
  const p = new URLSearchParams();
  if (range.mode === "session") {
    if (range.session != null) p.set("session", String(range.session));
  } else if (range.mode === "clock") {
    if (range.fromTs != null) p.set("since_ts", String(range.fromTs));
    if (range.toTs != null) p.set("until_ts", String(range.toTs));
  } else if (range.mode === "shown" && shown != null) {
    p.set("since_ts", String(shown.fromTs - SHOWN_EDGE_S));
    p.set("until_ts", String(shown.toTs));
  }
  if (watermark != null) p.set("id_to", String(watermark));
  return p;
}
