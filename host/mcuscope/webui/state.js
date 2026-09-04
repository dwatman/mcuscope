// Shared primitives + mutable state for the web UI modules. Imported by every other
// module; imports nothing itself so it is the dependency-graph leaf.

const $ = (id) => document.getElementById(id);

// ---- access token (optional server.token, see SPEC daemon auth) --------------------
//
// A configured token gates every non-loopback API call and the WS handshake. The token
// itself is opaque to this module: it is just carried on requests and, on a 401/WS 1008,
// the user is prompted for it (window.prompt is enough here, no dedicated UI). The prompt
// is capped so a wrong token cannot loop forever; hooks.authFailed (wired by app.js) then
// surfaces the failure in the stream-health indicator.
const TOKEN_KEY = "mcuscope.token";
let authToken = null;
try { authToken = localStorage.getItem(TOKEN_KEY) || null; } catch { /* private mode */ }

function setToken(t) {
  authToken = t || null;
  try {
    if (authToken) localStorage.setItem(TOKEN_KEY, authToken);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode */ }
}

const TOKEN_PROMPT_MAX = 3;   // initial + up to 2 retries
let tokenPromptCount = 0;
let tokenGaveUp = false;

// Re-arm the prompt budget after the user supplies a token through the Settings dialog,
// so a previous cancel/give-up does not leave the page permanently unauthenticated.
function resetTokenPrompt() {
  tokenPromptCount = 0;
  tokenGaveUp = false;
}

// Ask the user for a token, remember it, and return it - or null if the user cancelled or
// the retry budget is spent (in which case authFailed fires exactly once). Shared by the
// HTTP 401 path (authFetch, below) and the WS 1008 path (api.js), so the two never double
// the prompt budget.
//
// `failedToken` is the token the caller's failed request actually carried (possibly null).
// The HTTP 401 fetch and the WS handshake can both fail at once for the very same missing
// token; without this check each would prompt in turn. If the stored token has already
// moved on from `failedToken` - because the other path just supplied one - that newer token
// is returned immediately, no prompt and no budget spent; the caller simply retries with it.
function promptForToken(failedToken) {
  if (authToken !== failedToken) return authToken;
  if (tokenGaveUp) return null;
  if (tokenPromptCount >= TOKEN_PROMPT_MAX) {
    tokenGaveUp = true;
    hooks.authFailed();
    return null;
  }
  tokenPromptCount++;
  const t = window.prompt("This daemon requires an access token");
  if (!t) {
    tokenGaveUp = true;
    hooks.authFailed();
    return null;
  }
  setToken(t);
  return t;
}

// ---- API helpers (same-origin, root-relative) --------------------------------------

// fetch() with the Authorization header attached (when a token is set) and the shared
// 401-prompt-retry loop, so every caller (JSON api() calls, the CSV export blob fetch)
// gets the same auth behaviour instead of reimplementing it.
async function authFetch(path, opt) {
  opt = opt || {};
  let used = authToken;
  if (used) opt.headers = { ...opt.headers, Authorization: "Bearer " + used };
  let r = await fetch(path, opt);
  // A missing/invalid token: prompt once, retry with the freshly entered token, and if that
  // is also rejected let the loop continue up to the shared prompt budget in promptForToken.
  // Passing `used` lets a concurrent 401/1008 for the same missing token short-circuit here
  // instead of prompting twice (see promptForToken).
  while (r.status === 401) {
    const t = promptForToken(used);
    if (!t) break;
    used = t;
    opt.headers = { ...opt.headers, Authorization: "Bearer " + t };
    r = await fetch(path, opt);
  }
  return r;
}

async function api(method, path, body, signal) {
  const opt = { method, cache: "no-store" };
  if (signal) opt.signal = signal;   // caller-supplied AbortSignal (e.g. a client-side timeout)
  if (body !== undefined) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await authFetch(path, opt);
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
const BUFFER_SLACK = 512;  // overshoot tolerated before trimming (see pushBuffer)

// Cross-module callbacks wired in main.js to break import cycles (see there).
// plotSampleTick is set by plots.js itself (this module is the dependency-graph leaf and
// cannot import it back); until then no !ps line decodes, which is the safe answer.
export const hooks = { reapplyCursor: () => {}, authFailed: () => {},
                        reportError: () => {}, plotSampleTick: () => null };

// Bounds the daemon enforces on the values these dialogs send, mirrored client-side so the
// refusal is the dialog's own wording rather than a 422 after a round trip. Kept here, the
// shared leaf, so settings.js and statusbar.js cannot drift from each other.
// Mirrors server.MAX_BAUD, server.MAX_TIMEOUT_MS and ConfigStorageBody.max_db_bytes.
export const MAX_BAUD = 100_000_000;
export const MAX_TIMEOUT_MS = 300_000;
export const MAX_DB_BYTES = 2 ** 42;

const PORT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b", "#ef7a5e", "#6fb2ff"];
// Wire-keyed like canRows (256) and digitalLanes (64), and capped for the same reason: a
// capture carrying rotating aliases must not grow it forever. Past the cap the colour is
// still returned, just recomputed - it is a memo, not state.
const PORT_COLOR_MAX = 64;
const portColorCache = new Map();
function portColor(alias) {
  const s = alias || "";
  let c = portColorCache.get(s);
  if (c) return c;
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  c = PORT_COLORS[h % PORT_COLORS.length];
  if (portColorCache.size < PORT_COLOR_MAX) portColorCache.set(s, c);
  return c;
}

// The aliases of a capture that no longer exists are as stale as its rows (see api.js
// resetForDbReset, which drops every other wire-keyed model).
export function clearPortColors() { portColorCache.clear(); }

function pad2(n) { return String(n).padStart(2, "0"); }

// A form field's value as a whole number, or NaN for anything else.
//
// parseInt() takes whatever digits it finds at the front and stops, which is the wrong
// grammar for a field with a range: "1e9" parses as **1** and "12abc" as 12, so a value the
// user can see is wrong passes every bounds check downstream. `1e9` typed into the settings
// port box saved port 1. Number() rejects both, and `<input type=number>` accepts exponent
// notation, so this is reachable without pasting anything odd. An empty field stays NaN
// rather than becoming Number("") === 0, or a blank size cap would read as "unlimited".
function intField(text) {
  const s = String(text ?? "").trim();
  if (!s) return NaN;
  const n = Number(s);
  return Number.isInteger(n) ? n : NaN;
}

// Extract the MCU tick (ms) a line carries, or null. Only CAN/plot events and firmware
// markers have one; !can and !p use a decimal tick, !ps a hex tick after the sid, and
// !m an optional "@<tick>" (host-side markers from `mcu mark` carry none).
// The tick must fit the SPEC 2.5 32-bit range. can.js and plots.js already bound theirs;
// this one did not, and it is the value that sets the sticky global state.anchorTick, so
// a single corrupt line ("!can 99999999999999999999 - 100 -") shifted every terminal
// timestamp and every chart x-axis for the rest of the session, recoverable only by
// "clear all".
function inTickRange(t) { return Number.isFinite(t) && t >= 0 && t <= 0xFFFFFFFF; }

// Mirror of protocol.is_decimal_token, exported so the four decoders that need it share one
// implementation. JS \d is ASCII-only, so the character set matches; the length cap is
// protocol.MAX_DECIMAL_DIGITS. The cap is not decoration: a zero-padded token passes a
// numeric range check ("0"x21 is 0), so it is the ONLY clause rejecting it - and every miss
// in these mirrors so far has been a bound dropped beside the character-set check it sits by.
const MAX_DECIMAL_DIGITS = 20;
function isDecimalToken(s) { return /^\d+$/.test(s) && s.length <= MAX_DECIMAL_DIGITS; }

// Memoized per row object: tick mode re-derives this for every visible row on every render
// (~30 rows at 30 fps), and a !ps line pays a full decodePlotSample each time. Rows are
// immutable, and resetForDbReset discards them wholesale.
//
// A null from a !ps line is NOT cached: that line is undecodable only until its !pd arrives,
// so caching the miss would leave it reading "-" for the life of the page.
function lineTick(row) {
  if (row.__tick !== undefined) return row.__tick;
  const t = computeTick(row);
  if (t !== null || !row.raw.startsWith("!ps ")) row.__tick = t;
  return t;
}

function computeTick(row) {
  const r = row.raw;
  if (row.chan === "marker") {
    const m = /^!m\s+@(\d+)\s+\S/.exec(r);
    if (!m || !isDecimalToken(m[1])) return null;
    const t = +m[1];
    return inTickRange(t) ? t : null;
  }
  if (row.chan !== "event") return null;
  const p = r.trim().split(/\s+/);   // trim as plots.js does, so the token counts agree
  if (/^!can[1-9]? /.test(r) || r.startsWith("!p ")) {
    if (!isDecimalToken(p[1])) return null;
    const t = +p[1];
    return inTickRange(t) ? t : null;
  }
  if (r.startsWith("!ps ")) {
    // Delegated, not mirrored. A hand-written copy of decodePlotSample's gate lost a clause
    // twice: first the arity and sid checks (so "!ps 0 ABCD" set the sticky anchor), then
    // the value count and field decodability (so "!ps 0 3E9 0064" against a two-field
    // definition still did). Both times the copy was faithful about the clauses next to the
    // missing one, which is why reading it looked right. plots.js owns the decoder and
    // publishes the answer through the hooks seam this module already uses, so there is now
    // one implementation and nothing left to drift.
    const t = hooks.plotSampleTick(row.port || "-", r);
    return t !== null && inTickRange(t) ? t : null;
  }
  return null;
}

// Add a row to the shared buffer and advance the id/relative-time/tick anchors.
function pushBuffer(row) {
  if (state.anchorTs === null) state.anchorTs = row.ts;
  if (state.anchorTick === null) { const t = lineTick(row); if (t !== null) state.anchorTick = t; }
  buffer.push(row);
  if (row.id > state.maxId) state.maxId = row.id;
  // Trim in blocks, not one row per push: Array.shift is O(buffer length), so shifting
  // per row makes browser-side ingest cost grow with the backlog. Letting the buffer run
  // up to BUFFER_SLACK over the cap makes the trim amortized-constant instead; nothing
  // depends on the exact length (panes re-filter by id).
  if (buffer.length > BUFFER_MAX + BUFFER_SLACK) buffer.splice(0, buffer.length - BUFFER_MAX);
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
// Overshoot tolerated before a plot/digital ring is trimmed, for the same reason as
// BUFFER_SLACK above: splice(0,1) and shift() are O(length), so evicting one point per
// arriving sample once a ring is at cap costs O(PLOT_CAP) per sample. Measured at cap,
// that was ~0.5 ms/sample for a 3-channel chart and ~1.8 ms/sample for 8 digital lanes,
// on the same thread that renders the terminal. Trimming in blocks makes it amortized
// constant (~200x cheaper); nothing depends on the exact ring length.
const PLOT_SLACK = 4096;
// Extract a filename from a Content-Disposition header (RFC 6266 attachment; filename=...);
// falls back to the caller's default when absent or unparsable.
function filenameFromDisposition(header, fallback) {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) { try { return decodeURIComponent(star[1]); } catch { /* fall through */ } }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : fallback;
}

// Hand a Blob to the browser to save as `name`, via a short-lived object URL and a
// synthetic anchor click. Shared by the API downloads below and can.js's client-side export.
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// Trigger a browser download of GET /plot/export for the given channels/window/format. Goes
// through authFetch (not a plain <a> navigation) so a configured token rides the Authorization
// header instead of appearing in the URL / server logs; the response body becomes a Blob and
// is downloaded via saveBlob.
async function downloadPath(path, fallbackName, label) {
  try {
    const r = await authFetch(path, { cache: "no-store" });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const data = await r.json(); if (data && data.error) msg = data.error; } catch { /* not JSON */ }
      throw new Error(msg);
    }
    const blob = await r.blob();
    saveBlob(blob, filenameFromDisposition(r.headers.get("Content-Disposition"), fallbackName));
  } catch (e) {
    hooks.reportError(`${label} failed: ${e.message}`);
  }
}

// `idTo` (optional) is an inclusive upper bound on line id: the daemon then measures `lastMs`
// back from that line instead of from now, so a paused surface exports what it shows.
function downloadCsv(names, lastMs, format, filename, idTo) {
  if (!names.length) return;
  const params = new URLSearchParams({ names: names.join(","), last_ms: String(lastMs), format });
  if (idTo != null) params.set("id_to", String(idTo));
  return downloadPath("/plot/export?" + params.toString(), filename || "plot.csv", "csv export");
}

// Token accessor (not a live binding: a getter keeps `authToken` a private module var while
// still letting api.js read the current value, e.g. to build the WS URL).
function getToken() { return authToken; }

// ---- outgoing line ending (SPEC 3.4 `eol`) ------------------------------------------
//
// A browser-side preference, not a config key: it says what THIS page appends to what it
// sends, leaving the port's own setting alone. "" is "use the port default", and is sent
// by omitting the field entirely, so a browser that never touched the setting posts the
// same body it always did. Validated on read as well as on write, because localStorage is
// hand-editable and an unknown value must not reach the daemon as a 422.
const EOL_KEY = "mcuscope.eol";
const EOL_CHOICES = ["none", "lf", "crlf"];
let sendEol = "";
try {
  const saved = localStorage.getItem(EOL_KEY);
  if (EOL_CHOICES.includes(saved)) sendEol = saved;
} catch { /* private mode */ }

function getEol() { return sendEol; }

function setEol(value) {
  sendEol = EOL_CHOICES.includes(value) ? value : "";
  try {
    if (sendEol) localStorage.setItem(EOL_KEY, sendEol);
    else localStorage.removeItem(EOL_KEY);
  } catch { /* private mode */ }
}

// Spread into a /send or /cmd body: `{...eolField()}` adds nothing on "port default".
function eolField() { return sendEol ? { eol: sendEol } : {}; }

export { $, api, root, sidebar, pad2, intField, lineTick, isDecimalToken, pushBuffer,
         nearestX, portColor,
         BUFFER_MAX, PLOT_CAP, PLOT_SLACK, downloadCsv, downloadPath, saveBlob,
         getToken, setToken, promptForToken, resetTokenPrompt,
         getEol, setEol, eolField, EOL_CHOICES };

