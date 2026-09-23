import { $, api, state, buffer, BUFFER_MAX, BUFFER_SLACK, pushBuffer, tickAnchors, getToken,
         clearPortColors, hooks } from "./state.js";
import { canIngest, clearAllCan, canClearGen } from "./can.js";
import { plotIngest, plotSeed, plotSeedGen, clearAllCharts, breakCharts } from "./plots.js";
import { groupWindow } from "./chrome.js";
import { clearAllDigital, breakLanes } from "./digital.js";
import { VIEW_MAX, panes, matches, rebuild, render, updateJump,
         scheduleFlush, refillRegexBudget, resetHistory } from "./terminal.js";
import { gapRow } from "./pane.js";

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
let shownRate = null;      // what renderRate last wrote; nothing below changes without these two
let shownHigh = null;

function renderRate() {
  const el = $("lineRate");
  if (!el) return;
  if (lineRate === shownRate && highRate === shownHigh) return;
  shownRate = lineRate; shownHigh = highRate;
  // "rx": the total across all ports, told apart from the per-port figure on each port chip.
  el.textContent = lineRate ? `rx ${lineRate}/s` : "";
  el.classList.toggle("drop", highRate);
  el.title = highRate
    ? `${lineRate} lines/s: too fast to render, so the terminal panes are not being fed. `
      + "CAN and plots are still live, and the panes refill when the rate drops."
    : "Lines per second arriving on the live stream, total across all ports";
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
// Nobody is reading the readout in a hidden tab, and rows keep arriving; skip the work and
// re-open the window on return, so the first visible tick measures a real second (the other
// tickers in app.js/plots.js idle the same way).
setInterval(() => { if (!document.hidden) tickRate(); }, RATE_WINDOW_MS);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  rateCount = 0;
  rateStart = performance.now();
});

// A live row (from /ws or the post-backfill drain): add it to the shared buffer + CAN/plot
// models, then fan it out to the panes' queues. The caller has already deduped it by id.
// `canCleared`/`chartsCleared`: a staged row a clear covers (see feedStaged), gated as the
// backfill gates its rows.
function routeLiveRow(row, canCleared = false, chartsCleared = false) {
  pushRow(row, chartsCleared);
  if (!canCleared) canIngest(row);
  if (!chartsCleared || isPlotDef(row)) plotIngest(row);
  feedPanes(row);
}

function feedPanes(row) {
  // Panes are not fed while shedding: no filter test, no queue, and no `pending` increment
  // either. The counts are not lost with the work: setHighRate(false) rebuilds every pane, and
  // a frozen pane's "N new" is re-derived from the shared buffer there (terminal.js rebuild),
  // so the jump button ends the episode reading what actually arrived.
  if (highRate) return;
  let need = false;
  for (const p of panes) {
    // A live row always sits above clearId; a staged row a clear covers does not (feedStaged).
    if (row.id <= p.clearId) continue;
    refillRegexBudget(p);   // one row is one filtering episode (see terminal.js)
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
    // anything older than that is already certain to be discarded. Trimmed in blocks, as
    // pushBuffer is: a hidden tab never flushes, so a per-row trim is O(VIEW_MAX) per row.
    p.queue.push(row);
    if (p.queue.length > VIEW_MAX + BUFFER_SLACK) p.queue.splice(0, p.queue.length - VIEW_MAX);
    need = true;
  }
  if (need) scheduleFlush();
}

// pushBuffer, for a row that may sit under a clear-all (`covered`): clear-all re-zeroes relative
// time and tick at the click (SPEC 9.1), so a row it covers must not become that zero.
function pushRow(row, covered) {
  const { anchorTs, anchorTick } = state;
  pushBuffer(row);
  if (covered) { state.anchorTs = anchorTs; state.anchorTick = anchorTick; }
}

// The capture identity the daemon last reported (SPEC 3.4). A capture is one id space: while
// it holds, live ids only climb and `id <= state.maxId` is an exact duplicate test. When it
// changes, every id we hold names nothing in the stream now arriving, and keeping them would
// discard the whole new capture as duplicates for the life of the page.
//
// This is a fact from the daemon, replacing two generations of inferring it from id
// arithmetic: "ids went backward" mistook the ordinary backfill/live overlap for a reset and
// wiped a page load one to three times over, and the timestamp arm added to cover a silent
// target still could not see a restored backup whose ids happen to sit higher.
let captureId = null;

function noteCapture(id) {
  if (typeof id !== "string" || id === captureId) return;
  const first = captureId === null;   // a fresh page holds nothing to throw away
  captureId = id;
  if (!first) resetForDbReset();
}

// Wipe the stale watermark and pane buffers so a fresh (post-reset) low-id sequence is accepted
// again. Pane filters and live/paused state are kept; the relative-time/tick zeros re-anchor.
function resetForDbReset() {
  state.captureGen++;
  buffer.length = 0;
  state.maxId = 0;
  canFloor = 0; chartFloor = 0;   // ids of the old capture (see dropStaging)
  state.anchorTs = null;
  state.anchorTick = null;
  tickAnchors.clear();   // their ids name lines of the old capture
  for (const p of panes) {
    // frozenId too: the new capture's ids restart low, so a paused pane's old freeze point
    // would sit above them and let a later rebuild fold the new capture in.
    // frozenRows too: that snapshot holds rows from a capture that no longer exists.
    // clearGen too, for symmetry with the CAN and chart tokens clearAll* moves below: a
    // backfill in flight across a reset must read every surface as cleared, not two of three.
    p.clearId = 0; p.clearGen += 1;
    p.frozenId = 0; p.frozenRows = null; p.rows = []; p.queue.length = 0; p.pending = 0;
    resetHistory(p);   // a history page in flight belongs to the old capture
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
  clearPortColors();
  // Re-seed from the new capture. The backfill for this connection already ran, against
  // the old (high) watermark, and matched nothing in a DB whose ids restarted low - so
  // without this the terminal stayed empty and filled only from live traffic, where a
  // first-ever connect seeds 200 rows of history. maxId is 0 again now, so runBackfill
  // takes exactly that branch.
  //
  // Under the same staging discipline the first connect has, and for the same reason: the
  // token is delivered mid-frame, so the new capture's live rows arrive while the re-seed's
  // /lines fetch is still in flight. Left unstaged they advance state.maxId first, and every
  // history row the fetch returns is then dropped by the `row.id <= state.maxId` guard - the
  // re-seed reads as empty and the terminal shows live traffic only.
  const gen = wsGen;
  armStaging(gen);
  runBackfill(gen)
    .catch((e) => { console.error("re-seed backfill failed:", e); })
    .then(() => drainStaging(gen));
}

function handleWsRow(row, canCleared, chartsCleared) {
  if (!row || typeof row.id !== "number") {
    // A frame carries control objects as well as lines, told apart by having no id
    // (SPEC 3.4): the capture identity, and the count of rows shed ahead of the next one.
    if (row) {
      noteCapture(row.capture);
      if (Number.isInteger(row.gap) && row.gap > 0) pendingGap += row.gap;
    }
    return;
  }
  if (row.id <= state.maxId) return;   // already have it (backfill overlap / duplicate late response)
  if (pendingGap) markShed(row, chartsCleared);
  routeLiveRow(row, canCleared, chartsCleared);
}

// Rows the daemon shed for this socket ({gap: n}, SPEC 3.4) and not yet marked. The page
// subscribes to every port and the notice names none, so the hole is marked on all of them:
// a divider ahead of `row` in the panes, and a break in every chart and lane, so no trace is
// drawn held across rows it never saw. The shed rows stay in the capture, and exports read them.
let pendingGap = 0;

function markShed(row, chartsCleared) {
  // A notice staged while the backfill ran can name rows the backfill then fetched: those are
  // no hole, and a hole is at most the ids between the newest row held and this one.
  const n = Math.min(pendingGap, row.id - 1 - state.maxId);
  pendingGap = 0;
  if (n <= 0) return;
  const g = gapRow(row, n, "shed by the live stream");
  pushRow(g, chartsCleared);
  breakCharts();
  breakLanes();
  feedPanes(g);
}

// How far back the !pd search reaches, and its page size. The floor bounds the work: `match`
// is a regex scan, so a capture with no plot streams at all (a board that never emits !pd)
// would otherwise scan the whole table on every page load - measured at 170 ms over 169k
// lines and linear from there, against 25 ms once bounded. Every !pd in that lookback is
// read, not a newest-N: with many boards, one board's rebroadcasts crowded another's only
// definition out of a 50-row cap (REVIEW class 57). One page covers any realistic capture.
const PLOT_DEF_PAGE = 1000;
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
    // Up to the window's oldest row: the window's own !pd rows arrive with it, in order.
    const rows = [];
    let since = Math.max(0, oldestSeededId - PLOT_DEF_LOOKBACK);
    for (;;) {
      const body = await api("GET", "/lines?match=" + encodeURIComponent("^!pd ")
        + `&order=asc&limit=${PLOT_DEF_PAGE}&since_id=${since}&id_to=${oldestSeededId}`);
      if (gen !== undefined && gen !== wsGen) return;
      const page = (body && body.lines) || [];
      rows.push(...page);
      const last = page.length ? page[page.length - 1].id : null;
      if (!body.truncated || typeof last !== "number" || last <= since) break;
      since = last;
    }
    // Oldest first, so that on the rare occasion a definition really did change, the
    // newest one is the one left in the cache.
    let bad = null;
    for (const row of rows) {
      // plotIngest only. These are history rows the terminal may already hold, and
      // pushBuffer would both duplicate them in the panes and advance the state.maxId
      // watermark past rows this backfill has not merged yet.
      // Per row, as the live path is: one malformed definition must not abandon the rest.
      if (row && typeof row.id === "number") {
        try { plotIngest(row); } catch (err) { bad = err; }
      }
    }
    if (bad) console.error("definition seed: some rows were dropped, last error:", bad);
  } catch (e) {
    // Non-fatal: this only adds definitions the seed window did not already carry, so a
    // failure here must leave the backfill - and any !pd inside it - to proceed.
    console.error("plot definition seed failed:", e);
  }
}

// ---- plot history seed ---------------------------------------------------------------
//
// What a fresh page shows on the charts. Channels were discovered from live traffic alone,
// so after a reload the charts sat empty until new samples arrived, and a stream that had
// stopped emitting never appeared at all - with the daemon holding its whole history the
// entire time. SPEC 9.2 names /plot/channels as the discovery source; /plot/series carries
// the samples, one channel per call.
//
// The bounds, because a capture can hold many channels and a great many points:
//  - SEED_CHANNELS: most recently active first, so a device rotating channel names seeds
//    the ones being watched. One request per channel, so this also caps the request fan-out.
//  - SEED_POINTS: newest points per channel. A chart is a few hundred pixels wide, so past
//    this the extra samples are already below one per pixel. A stream fast enough to exceed
//    it inside the window seeds only the newest part of that window, and fills in live.
//  - No `decimate`: it is min/max per channel, so each channel comes back on a DIFFERENT
//    set of lines, and a chart keeps ONE x array for all of its channels - every
//    disagreement would become a null, which a stepped path with spanGaps:false draws as
//    isolated dots. It is no safer on the digital lanes: min/max over an enum code is
//    meaningless, and over a 0/1 lane it moves the transition times.
const SEED_CHANNELS = 32;
const SEED_POINTS = 2000;
const SEED_MAX_MS = 3600000;

// The window to ask a channel for, in ms back from the anchor line's timestamp: the group
// span (chrome.js groupWindow), which is what the charts a capture reset rebuilds show. `last_ms`
// is measured from the newest end, so a channel that stopped emitting needs its own silence
// added or its window comes back empty - which is the half of this defect where a stopped
// channel never appeared at all. The cap is what bounds the query when `last_ts` does not
// (an idle channel's window still scans only the points it has, which is why it can be this
// generous). Whole ms: the daemon takes an int.
function seedLastMs(lastTs, anchorTs) {
  const idle = Number.isFinite(lastTs) && Number.isFinite(anchorTs)
    ? Math.max(0, anchorTs - lastTs) * 1000 : 0;
  return Math.min(Math.round(idle) + groupWindow() * 1000, SEED_MAX_MS);
}

// The channels to seed, one entry per (port, name). An unfiltered /plot/channels names only
// the port of each name's newest sample, so with two boards declaring one name the other
// board's history would never be asked for (SPEC 9.2). The response's `ports` lists every
// board with stored points, attached or not, including one shadowed on every name.
async function seedChannelList() {
  const valid = (body) => ((body && body.channels) || []).filter((c) => c && typeof c.name === "string");
  const list = await api("GET", "/plot/channels");
  const channels = valid(list);
  const listed = Array.isArray(list && list.ports) ? list.ports : [];
  const ports = new Set([...listed, ...channels.map((c) => c.port)]
    .filter((p) => typeof p === "string" && p));
  if (ports.size < 2) return channels;
  try {
    const perPort = await Promise.all([...ports].map((port) =>
      api("GET", "/plot/channels?" + new URLSearchParams({ port }).toString())));
    return perPort.flatMap(valid);
  } catch (e) {
    console.error("per-port plot channel list failed; seeding each name's newest port:", e);
    return channels;
  }
}

// Seed the charts and digital lanes from stored history, over the window the UI comes up
// showing. `gen` is checked the same way seedPlotDefs checks it, and again after the awaits.
//
// `anchor` is the newest row the backfill just fetched. Its id goes out as `id_to` on every
// request, which pins all of them to the same line: the daemon then measures each window
// back from that line's timestamp (store._window_floor) instead of from the instant each
// request happens to reach it. Without it the channels of one chart disagreed by a sample
// about each edge of the window, and a chart keeps one x array for all of its channels, so
// every disagreement became a null gap in a trace. It also keeps the browser's clock out of
// the arithmetic entirely: both timestamps below are the daemon's own.
//
// `cleared` is the caller's plotSeedGen() from before its own first await: a clear-all during
// the /lines fetch or the definition seed covers this history too (SPEC 9.2). `rows` is the
// backfill, whose `!pd` rows give the seeded chips their field order (plots.js plotSeed).
async function seedPlotHistory(gen, anchor, cleared, rows) {
  try {
    const listed = await seedChannelList();
    if (gen !== undefined && gen !== wsGen) return;
    const channels = listed
      .sort((a, b) => (b.last_ts || 0) - (a.last_ts || 0))
      .slice(0, SEED_CHANNELS);
    if (!channels.length) return;
    // Together rather than in sequence: one channel per request is the endpoint's shape, and
    // the drain of live rows waiting in `staging` is held up until this resolves. Per-request
    // catch, not Promise.all: one channel's failure must not discard every other channel's
    // history and leave the charts empty.
    const entries = await Promise.all(channels.map(async (channel) => {
      const q = new URLSearchParams({
        name: channel.name,
        last_ms: String(seedLastMs(channel.last_ts, anchor.ts)),
        id_to: String(anchor.id),
        limit: String(SEED_POINTS),
      });
      // Channel names are unique only within a port (SPEC 9.2), so without this two boards
      // declaring "temp" seed one channel carrying both boards' samples.
      if (channel.port) q.set("port", channel.port);
      try {
        const body = await api("GET", "/plot/series?" + q.toString());
        return { channel, points: (body && body.points) || [] };
      } catch (e) {
        console.error(`plot history seed failed for ${channel.name}:`, e);
        return { channel, points: [] };
      }
    }));
    if ((gen !== undefined && gen !== wsGen) || cleared !== plotSeedGen()) return;
    plotSeed(entries, rows);
  } catch (e) {
    // Non-fatal, exactly as the definition seed above: this only adds history the live
    // stream would eventually redraw anyway, so a failure must leave the backfill running.
    console.error("plot history seed failed:", e);
  }
}

// ---- reconnect backfill paging -------------------------------------------------------
//
// /lines clamps `limit` to 1000 rows server-side (store.query_lines) and reports the clamp
// as `truncated` in the envelope. The reconnect fetch asked for BUFFER_MAX and read the
// answer as if it had been served whole, so a gap wider than 1000 lines came back as its
// newest 1000 only: the pane showed the pre-gap buffer, then the live edge, with the middle
// silently absent and nothing on screen saying so.
//
// So page it. Each page is another `order=desc` window anchored to the same `since_id`
// watermark, with `id_to` walking down to just below the oldest row already held, until the
// gap closes or BUFFER_MAX rows have been collected. Past that cap the shared buffer would
// evict the oldest of them anyway, so a wider absence is a genuine long disconnection and is
// reported as a divider row rather than fetched (see gapRow).
const LINES_LIMIT_MAX = 1000;   // mirrors the clamp in store.query_lines
// One slot short of BUFFER_MAX, because a divider is itself one of the rows a pane holds:
// filled to exactly BUFFER_MAX, the buffer/VIEW_MAX trims take the OLDEST row first, and the
// oldest row is the divider - so the one case that needs the notice most would drop it.
const BACKFILL_MAX = BUFFER_MAX - 1;

// The smallest line id in one page, or null if it carries none. A min scan rather than the
// last element: a malformed row must not decide where the next page starts.
function oldestId(lines) {
  let min = null;
  for (const r of lines) {
    if (!r || typeof r.id !== "number") continue;
    if (min === null || r.id < min) min = r.id;
  }
  return min;
}

// What the paging does once a page has come back. Pure and exported, so the decision can be
// driven from a test without a fetch: `sinceId` is the watermark the whole backfill is
// anchored to, `collected` the row count so far (this page included), `limit` what this page
// asked for, `pageLen`/`truncated` what it answered with, and `oldest` the smallest id in it.
//
// Returns `{idTo}` naming the next page's upper bound, or `{idTo: null, gap}` when the
// backfill is finished - `gap` being the ids left unfetched between the watermark and the
// oldest row held, which is the exact missing line count while a capture's ids are contiguous.
function planBackfillStep({ sinceId, collected, limit, pageLen, truncated, oldest }) {
  if (!Number.isFinite(oldest)) return { idTo: null, gap: 0 };   // nothing older exists
  const gap = oldest - sinceId - 1;
  if (gap <= 0) return { idTo: null, gap: 0 };                    // the watermark is reached
  // Served in full (the envelope's own flag, or - for a daemon that omits it - a page that
  // did not reach the limit it asked for): the ids in between are simply not in the table,
  // pruned or belonging to another window, so there is nothing to fetch and nothing missing.
  if (!truncated && pageLen < limit) return { idTo: null, gap: 0 };
  if (collected >= BACKFILL_MAX) return { idTo: null, gap };      // a genuine long absence
  return { idTo: oldest - 1, gap: 0 };
}

function backfillPath(sinceId, idTo, limit) {
  const p = `/lines?since_id=${sinceId}&order=desc&limit=${limit}`;
  return idTo === null ? p : p + `&id_to=${idTo}`;
}

// Fetch everything captured since `sinceId`, one clamped page at a time. Resolves to
// `{rows, gap}` with the rows in capture order, or null when a newer handshake has
// superseded this one mid-paging (the caller must then land nothing at all).
async function fetchSince(gen, sinceId) {
  const pages = [];
  let collected = 0;
  let idTo = null;
  let gap = 0;
  for (;;) {
    const limit = Math.min(LINES_LIMIT_MAX, BACKFILL_MAX - collected);
    const body = await api("GET", backfillPath(sinceId, idTo, limit));
    // A backfill belonging to a superseded connection must not land: its rows are stale
    // and pushing them would advance state.maxId past what the current connection has
    // actually merged, so the live backfill's own rows would then be dropped by the
    // watermark guard - a permanent hole with nothing to show for it. Checked per page,
    // since every page is another await.
    if (gen !== undefined && gen !== wsGen) return null;
    const lines = (body && body.lines) || [];
    pages.push(lines);
    collected += lines.length;
    const step = planBackfillStep({
      sinceId, collected, limit,
      pageLen: lines.length,
      truncated: !!(body && body.truncated),
      oldest: oldestId(lines),
    });
    if (step.idTo === null) { gap = step.gap; break; }
    idTo = step.idTo;
  }
  // Each page is newest-first and the pages themselves walk backwards, so flattened they are
  // one descending run: a single reverse puts the whole backfill in capture order.
  return { rows: pages.flat().reverse(), gap };
}

// The divider row for lines the paging deliberately did not load is pane.js gapRow, shared
// with the scroll-to-top history paging; the charts and lanes get a break (breakCharts).

// A stream definition row, as plots.js reads it: cached rather than drawn, so a cleared
// backfill still has to hand it over.
function isPlotDef(row) {
  return row.chan === "event" && typeof row.raw === "string" && row.raw.startsWith("!pd");
}

// Every surface's clear token, read before an operation so it can tell afterwards which
// surfaces a clear hit while it was out. A pane added later is absent and reads as 0.
function clearTokens() {
  return { panes: new Map(panes.map((p) => [p, p.clearGen])), can: canClearGen(), charts: plotSeedGen() };
}

// Fill the gap between what we already have and the live stream. On the first connect state.maxId is 0,
// so seed the newest 200 rows (recent history, not the oldest ever captured); on a reconnect pull
// everything captured since the watermark. Rows already in the buffer are deduped by id.
async function runBackfill(gen) {
  // A fresh page or a post-reset re-seed; on a reconnect the charts already hold this history.
  const firstConnect = state.maxId === 0;
  // A clear clicked while this runs covers the rows it is about to deliver, whose clearId is
  // the watermark from before them (SPEC 9.1). One boolean for the whole backfill: the daemon
  // freezes the first page's id_to when it processes the request, so a clear clicked inside
  // that round trip also hides the handful of rows captured in the window between. Later pages
  // walk id_to backwards and are strictly older, so only the first page can hold any.
  const clears = clearTokens();
  const chartsToken = clears.charts;
  // A first connect still at watermark 0 under a chart floor follows a discarded first-connect
  // staging area whose clear-all covers this history too: hand the seed a token no clear holds.
  if (firstConnect && chartFloor) clears.charts = -1;
  try {
    // Newest rows first, then reversed to oldest-first so the buffer/CAN/plot models seed in
    // capture order. A first connect wants recent history and takes one bounded fetch; a
    // reconnect fills the gap from the watermark, paged against the server's limit clamp.
    let rows;
    let gap = 0;
    if (firstConnect) {
      const body = await api("GET", "/lines?order=desc&limit=200");
      if (gen !== undefined && gen !== wsGen) return;
      rows = ((body && body.lines) || []).slice().reverse();
    } else {
      const filled = await fetchSince(gen, state.maxId);
      if (!filled) return;   // superseded; fetchSince has already said so
      rows = filled.rows;
      gap = filled.gap;
    }
    // Definitions first, anchored to this window, so the typed samples below decode. No
    // rows means nothing to decode, and no reason to run the scan at all. Anchored to the
    // OLDEST row of the whole paged backfill, so the lookback floor still sits below it.
    if (rows.length && typeof rows[0].id === "number") {
      await seedPlotDefs(gen, rows[0].id);
      if (gen !== undefined && gen !== wsGen) return;   // re-check: the seed above awaited
    }
    // Before the rows below and before `staging` drains, because the seeded samples are the
    // older ones: addSample keeps each chart's x strictly increasing by nudging anything
    // that arrives out of order, so a seed applied afterwards would stack the whole history
    // just past the live edge instead of behind it.
    const anchor = rows.length ? rows[rows.length - 1] : null;   // newest row: the shared anchor
    if (firstConnect && anchor && typeof anchor.id === "number") {
      await seedPlotHistory(gen, anchor, clears.charts, rows);
      if (gen !== undefined && gen !== wsGen) return;   // re-check: the seed above awaited
    }
    // Per row, as the live path is (see onmessage): one malformed row must not abandon the
    // rest of the backfill, which would leave a permanent hole the watermark then hides.
    let bad = null;
    // The divider goes in ahead of the rows it precedes, in capture order, so the panes show
    // it exactly where the missing lines were. Only the buffer: it is not a captured line, so
    // the CAN table and the charts never see it.
    // The charts and lanes break there, or the last level before the absence is drawn held
    // across it (as for a shed notice, markShed).
    if (gap > 0 && rows.length && typeof rows[0].id === "number") {
      try { pushBuffer(gapRow(rows[0], gap)); breakCharts(); breakLanes(); } catch (err) { bad = err; }
    }
    const canCleared = clears.can !== canClearGen();
    const chartsCleared = chartsToken !== plotSeedGen();
    for (const row of rows) {
      if (!row || typeof row.id !== "number" || row.id <= state.maxId) continue;
      const chartsHidden = chartsCleared || row.id <= chartFloor;   // floors: see dropStaging
      try {
        pushRow(row, chartsHidden);
        if (!canCleared && row.id > canFloor) canIngest(row);
        // The clear gates the plotting, not the decoding: plotIngest's first branch caches the
        // !pd definitions, which no clear drops (see resetForDbReset) and seedPlotDefs fetches
        // only from below this window. Skipping them would leave every later !ps on those
        // streams undecodable until the board announced them again.
        if (!chartsHidden || isPlotDef(row)) plotIngest(row);
      } catch (err) { bad = err; }
    }
    // Over the live array, not the snapshot: a pane created while the backfill was out is not
    // in it, and its own clear must raise its clearId past these rows too.
    for (const p of panes) {
      if (p.clearGen !== (clears.panes.get(p) ?? 0)) p.clearId = Math.max(p.clearId, state.maxId);
    }
    if (bad) console.error("backfill: some rows were dropped, last error:", bad);
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
// `staging` carries one generation per handshake (fields at armStaging). It used to be a bare array
// shared by every socket, so a second connectWs (token save, auth-close retry) before the
// first backfill resolved had backfill A drain socket B's array and null out `staging`;
// backfill B then landed after the watermark had advanced and every one of its rows was
// dropped by the id guard, losing exactly the rows the staging mechanism exists to keep.
let staging = null;
// Id floors a discarded staging area's clears leave for later backfills (see dropStaging).
let canFloor = 0;
let chartFloor = 0;
let wsGen = 0;
let wsReconnect = null;
const WS_RECONNECT_MIN_MS = 1000;
const WS_RECONNECT_MAX_MS = 15000;
const WS_STABLE_MS = 5000;                    // uptime a connection must reach to count as good
let wsReconnectDelay = WS_RECONNECT_MIN_MS;   // doubles on each failed attempt, capped
let wsStableTimer = null;                     // pending "this connection held" backoff reset

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
  clearTimeout(wsStableTimer);   // the pending reset belongs to the socket being replaced
  wsStableTimer = null;
  if (curSock) {   // never run two streams at once (e.g. reconnectStream while healthy)
    const old = curSock;
    curSock = null;
    old.onclose = null;   // this close is intentional; don't let it schedule a reconnect
    try { old.close(); } catch { /* already closing */ }
  }
  let sock;
  try { sock = new WebSocket(wsUrl()); }
  catch { setStreamOnline(false); scheduleWsReconnect(); return; }
  curSock = sock;
  const gen = ++wsGen;   // this handshake's generation; see `staging`
  sock.onopen = () => {
    if (gen !== wsGen) return;               // superseded before it even opened
    setStreamOnline(true);
    // The backoff resets only once the connection has PROVED stable: a daemon that accepts
    // and immediately closes would otherwise reconnect at 1 Hz forever, each attempt running
    // a full backfill and rebuilding every pane.
    wsStableTimer = setTimeout(() => { wsStableTimer = null; wsReconnectDelay = WS_RECONNECT_MIN_MS; },
                               WS_STABLE_MS);
    armStaging(gen);   // hold live rows until the backfill has merged
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
    // Per-row guard: one malformed row must not cost the rest of the frame. Without it a
    // single throw out of the decoder escaped onmessage and silently dropped every later
    // row in that frame from the buffer, terminal, CAN table and plots - and recurred on
    // every frame carrying the offending line.
    for (const row of rows) {
      // Staging is re-checked per ROW, not once per frame: a capture token is applied
      // synchronously mid-frame and re-arms staging for its re-seed (resetForDbReset), and
      // the rows behind it in that same frame belong to that re-seed, not to the live path.
      if (staging && staging.gen === gen) { stageRow(row); continue; }
      try { handleWsRow(row); } catch (err) { console.error("row dropped:", err, row); }
    }
  };
  sock.onclose = (ev) => {
    if (curSock === sock) curSock = null;
    clearTimeout(wsStableTimer);   // this connection did not hold; keep the backoff climbing
    wsStableTimer = null;
    setStreamOnline(false);
    if (staging && staging.gen === gen) dropStaging();
    // A guard refusal (Host, Origin, token) is an HTTP 403 handshake the browser reports only as
    // 1006, indistinguishable from a network drop: the /status 401 path prompts for the token.
    scheduleWsReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch { /* already closing */ } };
}

// Hold one row for the drain below. Capped like the shared buffer, so a slow backfill against a
// saturated link cannot stage without bound: past the cap the oldest lines go, as pushBuffer
// evicts, trimmed in blocks for the same reason. Never a capture token, which this connection
// does not send again, nor a shed notice. Each run of dropped lines becomes a shed notice where
// it was, so the drain marks the hole like any shed. `at` keeps each row's arrival number, so
// the cuts survive the trim.
function stageRow(row) {
  const st = staging;
  noteClears(st);
  st.rows.push(row);
  st.at.push(st.count++);
  if (row && typeof row.id === "number") st.lastId = row.id;
  if (st.rows.length <= BUFFER_MAX + BUFFER_SLACK) return;
  let excess = st.rows.length - BUFFER_MAX;
  const rows = [], at = [];
  let run = 0;   // lines dropped since the last row kept
  // Adjacent notices merge, as the drain would sum them (pendingGap; a count below 1 adds
  // nothing there), so every trim leaves one notice per hole and BUFFER_MAX entries in all.
  const keep = (r, i) => {
    const prev = rows.at(-1);
    if (isShedNotice(r) && isShedNotice(prev)) {
      rows[rows.length - 1] = { gap: Math.max(prev.gap, 0) + Math.max(r.gap, 0) };
    } else { rows.push(r); at.push(st.at[i]); }
  };
  st.rows.forEach((r, i) => {
    if (excess > 0 && !isCaptureToken(r) && !isShedNotice(r)) {
      excess -= 1; st.dropped += 1; run += 1; return;
    }
    if (run) { keep({ gap: run }, i); run = 0; }
    keep(r, i);
  });
  st.rows = rows; st.at = at;
}

// A clear while rows are staged covers the ones that reached the page before it (SPEC 9.1).
// Their ids cannot say which those are: the watermark does not move while staging, so a clear
// sets clearId below all of them. Arrival order can: `seen` holds the clear tokens as of the
// last staged row, `cut` per surface the arrival count its latest clear covers, and `floor` the
// id of the last line staged by then (`lastId`), for when the area is dropped undrained.
function armStaging(gen) {
  dropStaging();
  pendingGap = 0;   // a shed notice names rows of the socket or capture being replaced
  staging = { gen, rows: [], at: [], count: 0, lastId: 0, dropped: 0, seen: clearTokens(),
              cut: { panes: new Map(), can: 0, charts: 0 },
              floor: { panes: new Map(), can: 0, charts: 0 } };
}

function noteClears(st) {
  const n = st.count;
  for (const p of panes) {
    if (p.clearGen === (st.seen.panes.get(p) ?? 0)) continue;
    st.seen.panes.set(p, p.clearGen);
    st.cut.panes.set(p, n);
    st.floor.panes.set(p, st.lastId);
  }
  if (canClearGen() !== st.seen.can) {
    st.seen.can = canClearGen(); st.cut.can = n; st.floor.can = st.lastId;
  }
  if (plotSeedGen() !== st.seen.charts) {
    st.seen.charts = plotSeedGen(); st.cut.charts = n; st.floor.charts = st.lastId;
  }
}

// A staging area thrown away before its drain: the socket closed, or a newer handshake armed its
// own. The watermark never passed its rows, so the next backfill fetches them again under a
// clear snapshot taken after any clear here. So each cut becomes an id floor: a pane's clearId
// at once, and for CAN and charts a floor the backfill's row loop reads. Ids at or below the
// floor were captured before a row that reached the page ahead of the click. A capture reset
// zeroes all of them (resetForDbReset).
function dropStaging() {
  const st = staging;
  if (!st) return;
  staging = null;
  noteClears(st);
  for (const [p, id] of st.floor.panes) p.clearId = Math.max(p.clearId, id);
  canFloor = Math.max(canFloor, st.floor.can);
  chartFloor = Math.max(chartFloor, st.floor.charts);
}

// A control object carrying the capture identity (SPEC 3.4): no id, so it is not a line.
function isCaptureToken(row) {
  return !!row && typeof row.id !== "number" && typeof row.capture === "string";
}

function isShedNotice(row) {
  return !!row && typeof row.id !== "number" && Number.isInteger(row.gap);
}

// Merge rows that arrived during the backfill. Each is deduped by the watermark (rows the
// backfill already covered are dropped inside handleWsRow).
//
// Sorted by id WITHIN one capture only. A capture token replaces the id space, so a reset
// landing mid-staging leaves the dead capture's high ids and the new capture's low ids in the
// same queue: one sort across the token put the old rows LAST, folded them back into the
// buffer the token had just wiped and jammed state.maxId at the old watermark - after which
// every row of the new capture read as a duplicate and was dropped, and the daemon, having
// already sent the token, never said so again. So segment at the tokens and keep the segments
// in arrival order.
//
// A pre-token segment is processed and then wiped by that token's own reset: those rows belong
// to a capture that no longer exists, and the wipe is what leaves the watermark on the newest
// NEW-capture row. The reset also re-arms `staging`, so everything behind the token waits for
// the re-seed instead of racing it, exactly as on connect.
function drainStaging(gen) {
  if (!staging || (gen !== undefined && staging.gen !== gen)) return;   // not ours to drain
  const st = staging;
  noteClears(st);   // a clear after the last staged row covers all of them
  const q = st.rows;
  staging = null;
  if (st.dropped) console.warn(`stream: ${st.dropped} rows dropped while the backfill ran (staging full)`);
  // Indices into q, so each row keeps its arrival position through the sort (see armStaging).
  let seg = [];
  const flushSegment = () => {
    seg.sort((a, b) => ((q[a] && q[a].id) || 0) - ((q[b] && q[b].id) || 0));
    for (const i of seg) feedStaged(q[i], st.at[i], st.cut);
    seg = [];
  };
  // A shed notice ends a segment too: it sits ahead of the row after the hole, and a sort
  // would carry it (no id) to the front of its segment.
  q.forEach((row, i) => {
    if (isCaptureToken(row) || isShedNotice(row)) { flushSegment(); feedStaged(row, st.at[i], st.cut); }
    else seg.push(i);
  });
  flushSegment();
}

// One staged row, with the same per-row guard the live path has (see onmessage): one malformed
// row must not abandon every row behind it in the queue. `i` is its arrival number and `cut`
// the staging area's clear cuts: a row below a surface's cut stays off that surface. A pane's
// clearId rises past it, so a later rebuild from the buffer hides it too; ids only climb within
// a segment, so no row that arrived after the clear sits below it.
function feedStaged(row, i, cut) {
  if (staging) { stageRow(row); return; }   // a token above re-armed staging: this row is its re-seed's
  try {
    if (row && typeof row.id === "number") {
      for (const [p, n] of cut.panes) if (i < n) p.clearId = Math.max(p.clearId, row.id);
    }
    handleWsRow(row, i < cut.can, i < cut.charts);
  } catch (err) { console.error("row dropped:", err, row); }
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

export { connectWs, setAuthFailed, reconnectStream,
         planBackfillStep, gapRow, LINES_LIMIT_MAX, BACKFILL_MAX };
