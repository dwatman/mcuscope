// ---- the remembered export range, and the query params it becomes --------------------
//
// One range is shared by every panel's export dialog, so picking a session once covers the
// terminal, plot, digital and CAN exports that follow. DOM-free (like timewindow.js) because
// the interesting parts - what a mode sends, and the watermark rule below - are otherwise
// only reachable through a dialog the test stub cannot lay out.

const KEY = "mcuscope.exportRange";
export const MODES = ["session", "clock", "shown"];

// `session: null` means "the open session if there is one", which is what the daemon does
// when no session param is sent; a ref is only sent once the user picks one.
export function defaultRange() {
  return { mode: "session", session: null, fromTs: null, toTs: null };
}

export function reset() { return defaultRange(); }

function num(v) { return typeof v === "number" && Number.isFinite(v) ? v : null; }

// localStorage is hand-editable and survives a version change, so anything unrecognised
// falls back whole rather than per field: a half-valid range would export a window the user
// never chose.
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

// The daemon params for this range. `watermark` is the calling surface's frozen line id
// (null while live) and `shownLastMs` the span it displays.
//
// id_to rides along in EVERY mode, not just "shown": a paused surface must never export past
// what it shows (freeze.js, SPEC 9.1), and the daemon intersects every bound it is given, so
// a session or clock range narrowed by the watermark is still that range.
export function params(range, { watermark = null, shownLastMs = null } = {}) {
  const p = new URLSearchParams();
  if (range.mode === "session") {
    if (range.session != null) p.set("session", String(range.session));
  } else if (range.mode === "clock") {
    if (range.fromTs != null) p.set("since_ts", String(range.fromTs));
    if (range.toTs != null) p.set("until_ts", String(range.toTs));
  } else if (range.mode === "shown" && shownLastMs != null) {
    p.set("last_ms", String(Math.round(shownLastMs)));
  }
  if (watermark != null) p.set("id_to", String(watermark));
  return p;
}
