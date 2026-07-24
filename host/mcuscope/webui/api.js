import { $, api, state, buffer, BUFFER_MAX, pushBuffer, getToken, promptForToken } from "./state.js";
import { canIngest } from "./can.js";
import { plotIngest } from "./plots.js";
import { panes, matches, rebuild, render, updateJump, scheduleFlush } from "./terminal.js";

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

// A live row (from /ws or the post-backfill drain): add it to the shared buffer + CAN/plot
// models, then fan it out to the panes' queues. The caller has already deduped it by id.
function routeLiveRow(row) {
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
}

function handleWsRow(row) {
  if (!row || typeof row.id !== "number") return;
  if (row.id < lastWsId && row.id <= state.maxId) resetForDbReset();   // daemon DB reset: ids went backward
  if (row.id > lastWsId) lastWsId = row.id;
  if (row.id <= state.maxId) return;   // already have it (backfill overlap / duplicate late response)
  routeLiveRow(row);
}

// Fill the gap between what we already have and the live stream. On the first connect state.maxId is 0,
// so seed the newest 200 rows (recent history, not the oldest ever captured); on a reconnect pull
// everything captured since the watermark. Rows already in the buffer are deduped by id.
async function runBackfill() {
  try {
    // Newest rows first, then reversed to oldest-first so the buffer/CAN/plot models seed in
    // capture order. On a reconnect, since_id fills the gap starting at the watermark; capping at
    // BUFFER_MAX keeps continuity with the live edge (any older overflow would be evicted anyway).
    const path = state.maxId > 0
      ? `/lines?since_id=${state.maxId}&order=desc&limit=${BUFFER_MAX}`
      : "/lines?order=desc&limit=200";
    const body = await api("GET", path);
    const rows = (body.lines || []).slice().reverse();
    for (const row of rows) {
      if (!row || typeof row.id !== "number" || row.id <= state.maxId) continue;
      pushBuffer(row); canIngest(row); plotIngest(row);
      if (row.id > lastWsId) lastWsId = row.id;
    }
  } catch { /* daemon may be down; the next reconnect drives another backfill */ }
  panes.forEach(rebuild);
}

// While a backfill runs after (re)connect, live /ws rows are queued in `staging` rather than
// processed, so nothing arriving between the /lines snapshot and the subscription is lost. After
// the backfill they are merged in id order and deduped by the state.maxId watermark the backfill set.
let staging = null;
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
  sock.onopen = () => {
    setStreamOnline(true);
    wsReconnectDelay = WS_RECONNECT_MIN_MS;   // connection succeeded: reset the backoff
    staging = [];                          // hold live rows until the backfill has merged
    runBackfill().then(drainStaging);
  };
  sock.onmessage = (ev) => {
    let row;
    try { row = JSON.parse(ev.data); } catch { return; }
    if (staging) { staging.push(row); return; }   // queued for the post-backfill merge
    handleWsRow(row);
  };
  sock.onclose = (ev) => {
    if (curSock === sock) curSock = null;
    setStreamOnline(false); staging = null;
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
function drainStaging() {
  const q = staging || [];
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
