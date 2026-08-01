import { $, api, state, buffer, BUFFER_MAX, pushBuffer, getToken, promptForToken,
         hooks } from "./state.js";
import { canIngest, clearAllCan } from "./can.js";
import { plotIngest, clearAllCharts } from "./plots.js";
import { clearAllDigital } from "./digital.js";
import { VIEW_MAX, panes, matches, rebuild, render, updateJump,
         scheduleFlush } from "./terminal.js";

// Stream (WebSocket) health, tracked independently of the 5s /status poll: a live capture
// stream can die while /status still answers, so the "live" pills must not keep reading green.
// When the socket is down/reconnecting we surface a chip and restyle the live pills (body class).
let streamOnline = false;
const STREAM_WARN_DEFAULT = "stream reconnecting...";

function setStreamOnline(online) {
  if (streamOnline === online) return;
  streamOnline = online;
  document.body.classList.toggle("stream-down", !online);
  const w = $("streamWarn");
  if (w) { w.hidden = online; if (online) w.textContent = STREAM_WARN_DEFAULT; }
}

// The token prompt was cancelled or exhausted its retries (see state.js promptForToken):
// stop reconnecting and say so in the existing stream-health chip, rather than looping.
function setAuthFailed() {
  streamOnline = false;
  document.body.classList.add("stream-down");
  const w = $("streamWarn");
  if (w) { w.textContent = "access token required (reload to retry)"; w.hidden = false; }
}

// ---- ingest rate + high-rate guard --------------------------------------------------
//
// Everything the daemon captures arrives here. At ordinary rates that costs nothing, but a
// saturated link can deliver thousands of rows a second, and the terminal panes are by far
// the most expensive consumer: every row is filter-tested against every pane and joins a
// render queue. Above HIGH_RATE_ON rows/s the panes stop being fed and the readout says so;
// CAN and plots keep updating, because those are bounded aggregations that do not grow with
// the line count. Coming back down uses a lower threshold so a burst cannot flap the mode,
// and the panes are rebuilt from the shared buffer so they catch up on what they missed.
const RATE_WINDOW_MS = 1000;
const HIGH_RATE_ON = 2000;
const HIGH_RATE_OFF = 800;
let rateCount = 0;
let rateStart = 0;
let lineRate = 0;
let highRate = false;

// The readout sits left of the port chips, so anything that changes its width moves them:
// at a few lines a second it appeared and vanished every second and the chips jittered with
// it. Its box is therefore reserved in CSS (fixed minimum width, tabular figures) and it
// keeps its space when empty, while the high-rate notice - which is far too long to fit in a
// reserved box - goes to its own badge downstream of the chips.
function renderRate() {
  const el = $("lineRate");
  if (!el) return;
  el.textContent = lineRate ? `${lineRate}/s` : "";
  el.classList.toggle("drop", highRate);
  el.title = highRate
    ? `${lineRate} lines/s: too fast to render, so the terminal panes are not being fed. `
      + "CAN and plots are still live, and the panes refill when the rate drops."
    : "Lines per second arriving on the live stream";
  const warn = $("rateWarn");
  if (!warn) return;
  warn.hidden = !highRate;
  warn.textContent = highRate ? `terminal paused: ${lineRate} lines/s` : "";
}

function setHighRate(on) {
  if (highRate === on) return;
  highRate = on;
  if (!on) panes.forEach(rebuild);   // refill from the shared buffer
  renderRate();
}

// The window closes on a timer, not on arrival: driving it from incoming rows would leave
// the guard latched on (and the readout stale) the moment the stream went quiet, which is
// exactly when it must let go.
function tickRate() {
  const now = performance.now();
  const dt = now - (rateStart || now);
  lineRate = dt > 0 ? Math.round((rateCount * 1000) / dt) : 0;
  rateCount = 0;
  rateStart = now;
  if (lineRate >= HIGH_RATE_ON) setHighRate(true);
  else if (lineRate <= HIGH_RATE_OFF) setHighRate(false);
  renderRate();
}
setInterval(tickRate, RATE_WINDOW_MS);

// A live row (from /ws or the post-backfill drain): add it to the shared buffer + CAN/plot
// models, then fan it out to the panes' queues. The caller has already deduped it by id.
function routeLiveRow(row) {
  pushBuffer(row);
  canIngest(row);
  plotIngest(row);
  if (highRate) return;   // panes are not fed while shedding; rebuild() catches them up
  let need = false;
  for (const p of panes) {
    if (!matches(p, row)) continue;
    // Browsers throttle a background tab's timers to about once a minute while rows keep
    // arriving, so an unbounded queue kept buffer-evicted rows alive with it.
    if (!p.autoscroll) {
      // A paused pane's queue is only ever counted into `pending` and thrown away, so
      // count it here and never retain the row at all. Queueing the objects to length-
      // count them later was the same unbounded retention, in the branch a VIEW_MAX trim
      // cannot touch (trimming would have corrupted the count).
      p.pending += 1;
      p.pendingDirty = true;
      need = true;
      continue;
    }
    // A live pane renders at most VIEW_MAX rows and flush() trims to exactly that, so
    // anything older than that is already certain to be discarded.
    p.queue.push(row);
    if (p.queue.length > VIEW_MAX) p.queue.splice(0, p.queue.length - VIEW_MAX);
    need = true;
  }
  if (need) scheduleFlush();
}

// Highest id ever seen on the wire. In normal operation the live stream's ids only climb, so an
// incoming id that drops below both this and the watermark means the capture DB was reset and its
// ids restarted low - which we must detect, or the `id <= state.maxId` guard would discard every row forever.
let lastWsId = 0;

// Wipe the stale watermark and pane buffers so a fresh (post-reset) low-id sequence is accepted
// again. Pane filters and live/paused state are kept; the relative-time/tick zeros re-anchor.
function resetForDbReset() {
  buffer.length = 0;
  state.maxId = 0;
  lastWsId = 0;
  state.anchorTs = null;
  state.anchorTick = null;
  for (const p of panes) {
    p.clearId = 0; p.rows = []; p.queue.length = 0; p.pending = 0;
    p.selfScroll = true; render(p); updateJump(p);
  }
  // The sidebar models are just as stale as the panes. Left alone, the CAN table kept ageing
  // rows from a capture that no longer exists and the charts/lanes kept plotting its samples,
  // with the new capture grafted onto the end and no visual break between the two - the exact
  // reading error a reset is meant to prevent. The cached !pd definitions are deliberately
  // kept: a target announces its streams once at boot, so dropping them would leave every
  // following !ps undecodable until the board happened to be reset too.
  clearAllCan();
  clearAllCharts();
  clearAllDigital();
  // Re-seed from the new capture. The backfill for this connection already ran, against
  // the old (high) watermark, and matched nothing in a DB whose ids restarted low - so
  // without this the terminal stayed empty and filled only from live traffic, where a
  // first-ever connect seeds 200 rows of history. maxId is 0 again now, so runBackfill
  // takes exactly that branch.
  runBackfill(wsGen);
}

function handleWsRow(row) {
  if (!row || typeof row.id !== "number") return;
  if (row.id < lastWsId && row.id <= state.maxId) resetForDbReset();   // daemon DB reset: ids went backward
  if (row.id > lastWsId) lastWsId = row.id;
  if (row.id <= state.maxId) return;   // already have it (backfill overlap / duplicate late response)
  routeLiveRow(row);
}

// How many !pd rows to pull when seeding, and how far back to let that search reach. A
// target announces one !pd per stream per rebroadcast, so 50 covers many streams over
// several bursts. The floor matters more: `match` is a regex scan, so a capture with no
// plot streams at all (a board that never emits !pd) would otherwise scan the whole table
// on every page load - measured at 170 ms over 169k lines and linear from there, against
// 25 ms once bounded. Anchoring the search this far below the seed window bounds it.
const PLOT_DEF_SEED = 50;
const PLOT_DEF_LOOKBACK = 20000;

// Seed the !pd definitions that the backfill window itself does not carry.
//
// A typed !ps sample is undecodable until its !pd has been seen, and a target rebroadcasts
// !pd only every few seconds while the 200-row seed spans about two. So on most first loads
// the seed held typed samples whose definition sat just outside it: measured on the sim,
// a load whose window caught no !pd decoded 0 of 122 typed samples, and the typed and
// digital charts came up empty while the ad-hoc chart - which carries its own names and
// needs no definition - was full.
//
// resetForDbReset already keeps cached definitions across a capture reset for this same
// reason; this covers the first load, where there is no cached definition to keep.
async function seedPlotDefs(gen, oldestSeededId) {
  try {
    const floor = Math.max(0, oldestSeededId - PLOT_DEF_LOOKBACK);
    const body = await api("GET", "/lines?match=" + encodeURIComponent("^!pd ")
      + `&order=desc&limit=${PLOT_DEF_SEED}&since_id=${floor}`);
    if (gen !== undefined && gen !== wsGen) return;
    // Oldest first, so that on the rare occasion a definition really did change, the
    // newest one is the one left in the cache.
    for (const row of (body.lines || []).slice().reverse()) {
      // plotIngest only. These are history rows the terminal may already hold, and
      // pushBuffer would both duplicate them in the panes and advance the state.maxId
      // watermark past rows this backfill has not merged yet.
      if (row && typeof row.id === "number") plotIngest(row);
    }
  } catch (e) {
    // Non-fatal: this only adds definitions the seed window did not already carry, so a
    // failure here must leave the backfill - and any !pd inside it - to proceed.
    console.error("plot definition seed failed:", e);
  }
}

// Fill the gap between what we already have and the live stream. On the first connect state.maxId is 0,
// so seed the newest 200 rows (recent history, not the oldest ever captured); on a reconnect pull
// everything captured since the watermark. Rows already in the buffer are deduped by id.
async function runBackfill(gen) {
  try {
    // Newest rows first, then reversed to oldest-first so the buffer/CAN/plot models seed in
    // capture order. On a reconnect, since_id fills the gap starting at the watermark; capping at
    // BUFFER_MAX keeps continuity with the live edge (any older overflow would be evicted anyway).
    const path = state.maxId > 0
      ? `/lines?since_id=${state.maxId}&order=desc&limit=${BUFFER_MAX}`
      : "/lines?order=desc&limit=200";
    const body = await api("GET", path);
    // A backfill belonging to a superseded connection must not land: its rows are stale
    // and pushing them would advance state.maxId past what the current connection has
    // actually merged, so the live backfill's own rows would then be dropped by the
    // watermark guard - a permanent hole with nothing to show for it.
    if (gen !== undefined && gen !== wsGen) return;
    const rows = (body.lines || []).slice().reverse();
    // Definitions first, anchored to this window, so the typed samples below decode. No
    // rows means nothing to decode, and no reason to run the scan at all.
    if (rows.length && typeof rows[0].id === "number") {
      await seedPlotDefs(gen, rows[0].id);
      if (gen !== undefined && gen !== wsGen) return;   // re-check: the seed above awaited
    }
    for (const row of rows) {
      if (!row || typeof row.id !== "number" || row.id <= state.maxId) continue;
      pushBuffer(row); canIngest(row); plotIngest(row);
      if (row.id > lastWsId) lastWsId = row.id;
    }
  } catch (e) {
    // The socket is already open by the time this runs, so a failure here leaves a
    // live-looking UI with an empty scrollback. Silence made that indistinguishable from
    // a genuinely idle target.
    hooks.reportError("backfill failed: " + e.message);
  }
  panes.forEach(rebuild);
}

// While a backfill runs after (re)connect, live /ws rows are queued in `staging` rather than
// processed, so nothing arriving between the /lines snapshot and the subscription is lost. After
// the backfill they are merged in id order and deduped by the state.maxId watermark the backfill set.
// `staging` is {gen, rows}: one generation per handshake. It used to be a bare array
// shared by every socket, so a second connectWs (token save, auth-close retry) before the
// first backfill resolved had backfill A drain socket B's array and null out `staging`;
// backfill B then landed after the watermark had advanced and every one of its rows was
// dropped by the id guard, losing exactly the rows the staging mechanism exists to keep.
let staging = null;
let wsGen = 0;
let wsReconnect = null;
const WS_RECONNECT_MIN_MS = 1000;
const WS_RECONNECT_MAX_MS = 15000;
let wsReconnectDelay = WS_RECONNECT_MIN_MS;   // doubles on each failed attempt, capped, reset on open

// Browsers cannot set headers on a WS handshake, so a configured token rides as a query
// param instead (SPEC: GET /ws?token=). Built with URL/searchParams so it composes cleanly
// with any other query params (e.g. ?port=) rather than string-concatenating one in.
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const u = new URL(`${proto}://${location.host}/ws`);
  const token = getToken();
  if (token) u.searchParams.set("token", token);
  return u.toString();
}

let curSock = null;   // the one live socket; a deliberate reconnect closes it first

function connectWs() {
  if (curSock) {   // never run two streams at once (e.g. reconnectStream while healthy)
    const old = curSock;
    curSock = null;
    old.onclose = null;   // this close is intentional; don't let it schedule a reconnect
    try { old.close(); } catch { /* already closing */ }
  }
  const usedToken = getToken();   // remember which token this handshake carried (see handleWsAuthClose)
  let sock;
  try { sock = new WebSocket(wsUrl()); }
  catch { setStreamOnline(false); scheduleWsReconnect(); return; }
  curSock = sock;
  const gen = ++wsGen;   // this handshake's generation; see `staging`
  sock.onopen = () => {
    if (gen !== wsGen) return;               // superseded before it even opened
    setStreamOnline(true);
    wsReconnectDelay = WS_RECONNECT_MIN_MS;   // connection succeeded: reset the backoff
    staging = { gen, rows: [] };            // hold live rows until the backfill has merged
    // Drain unconditionally: a backfill that rejects must not strand `staging`, or every
    // later frame is queued into it instead of rendered and the UI freezes while still
    // looking live (the rate counter runs before the staging check).
    runBackfill(gen)
      .catch((e) => { console.error("backfill failed:", e); })
      .then(() => drainStaging(gen));
  };
  // Each frame carries an ARRAY of rows (SPEC 3.4): the daemon coalesces a burst into one
  // message rather than one frame per line. A bare object is still accepted so a page left
  // open across a daemon downgrade keeps working.
  sock.onmessage = (ev) => {
    if (gen !== wsGen) return;   // frame from a socket we have already replaced
    let rows;
    try { rows = JSON.parse(ev.data); } catch { return; }
    if (!Array.isArray(rows)) rows = [rows];
    rateCount += rows.length;   // the window is closed by tickRate, below
    // post-backfill merge, but only into this generation's staging area
    if (staging && staging.gen === gen) { for (const row of rows) staging.rows.push(row); return; }
    // Per-row guard: one malformed row must not cost the rest of the frame. Without it a
    // single throw out of the decoder escaped onmessage and silently dropped every later
    // row in that frame from the buffer, terminal, CAN table and plots - and recurred on
    // every frame carrying the offending line.
    for (const row of rows) {
      try { handleWsRow(row); } catch (err) { console.error("row dropped:", err, row); }
    }
  };
  sock.onclose = (ev) => {
    if (curSock === sock) curSock = null;
    setStreamOnline(false);
    if (staging && staging.gen === gen) staging = null;
    if (ev && ev.code === 1008) { handleWsAuthClose(usedToken); return; }   // missing/invalid token
    scheduleWsReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch { /* already closing */ } };
}

// The daemon closed the handshake for auth (code 1008): prompt for a token (shares its retry
// budget with the HTTP 401 path in state.js) and reconnect immediately on success: no backoff
// delay, since this is a credentials problem, not a connectivity one. `usedToken` is what this
// handshake actually carried, so a concurrent /status 401 that already obtained a token short-
// circuits the prompt here too (see promptForToken). On cancel/give up, promptForToken has
// already fired hooks.authFailed and we simply stop reconnecting.
function handleWsAuthClose(usedToken) {
  const t = promptForToken(usedToken);
  if (!t) return;
  wsReconnectDelay = WS_RECONNECT_MIN_MS;
  connectWs();
}

// Merge rows that arrived during the backfill: id-sorted so ordering is preserved, then each is
// deduped by the watermark (rows the backfill already covered are dropped inside handleWsRow).
function drainStaging(gen) {
  if (!staging || (gen !== undefined && staging.gen !== gen)) return;   // not ours to drain
  const q = staging.rows;
  staging = null;
  q.sort((a, b) => ((a && a.id) || 0) - ((b && b.id) || 0));
  for (const row of q) handleWsRow(row);
}

function scheduleWsReconnect() {
  if (wsReconnect) return;
  const delay = wsReconnectDelay;
  wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_MS);
  wsReconnect = setTimeout(() => { wsReconnect = null; connectWs(); }, delay);
}

// Reconnect the stream now with the current token (Settings > Access token save/clear):
// cancel any pending backoff and open a fresh handshake, replacing a live socket if any.
function reconnectStream() {
  clearTimeout(wsReconnect);
  wsReconnect = null;
  wsReconnectDelay = WS_RECONNECT_MIN_MS;
  const w = $("streamWarn");
  if (w) w.textContent = STREAM_WARN_DEFAULT;   // drop a stale "token required" message
  connectWs();
}

export { connectWs, setAuthFailed, reconnectStream };
