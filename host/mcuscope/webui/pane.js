// ---- the terminal pane model, with no DOM in it -------------------------------------
//
// The shape lives here, once, so a pane can be built without index.html's <template>.
// terminal.js adds the real elements and the listeners, the test stub adds fake ones, and
// neither restates what a pane *is* - a hand-transcribed mirror of these fields drifted.

export const ALL_CHANS = ["debug", "cmd", "resp", "event", "marker", "sys"];

// A pattern that spends longer than this matching one flush is dropped and never re-armed
// (a catastrophic backtrack would otherwise wedge the tab).
export const REGEX_BUDGET_MS = 250;

// `els` carries the pane's elements - real ones from the template, fakes under test.
// Everything else is the pane's state, and defaults the same way in both.
export function newPaneModel(cfg = {}, els = {}) {
  return {
    ...els,
    port: cfg.port || "all",
    channels: new Set(cfg.channels && cfg.channels.length ? cfg.channels : ALL_CHANS),
    regex: null,
    regexSrc: "",
    regexSlow: null,      // a source that blew the matching budget; never armed again
    regexBudget: REGEX_BUDGET_MS,
    autoscroll: true,
    regexTimer: null,
    rows: [],             // this pane's filtered lines (data, not DOM); virtualized on render
    queue: [],            // rows waiting for the next flush
    pending: 0,           // matching rows seen while paused (shown on the jump button)
    pendingDirty: false,  // `pending` moved since the last flush; refresh the jump button
    winFirst: 0,          // index range currently rendered into the DOM
    winLast: 0,
    viewH: 0,             // cached scrollback height in px; 0 means "measure again"
    domEls: null,         // the elements currently rendered, in order (see shiftWindow)
    clearId: 0,           // "cleared" boundary: rebuild ignores buffered lines up to this id
    clearGen: 0,          // bumped by each clear, so a backfill in flight can see one happened
    frozenId: 0,          // paused-at boundary: rebuild ignores buffered lines past this id
    frozenRows: null,     // rows the freeze covers, snapshotted at pause; null while live
    selfScroll: false,
    historyBusy: false,   // a scroll-to-top page fetch is in flight (see terminal.js loadHistory)
    historyDone: false,   // the capture has nothing older for this filter, or the budget is spent
    historyLoaded: 0,     // rows pulled from the capture past the live set, against HISTORY_MAX
    historyNext: null,    // upper bound for the next page; null means "below the oldest row"
    historyGen: 0,        // bumped when the rows are replaced; a page fetched before that is dropped
    canFilter: null,      // the pattern a CAN id click applied (terminal.js filterPaneTo)
    canFilterPrev: "",    // the pattern it replaced, which unfilter puts back
    tsCol: null,          // timestamp column width {mode, ch} (see tsColumnWidth)
    scope: null,          // the regex readout's running in-scope count (terminal.js scopedCount)
  };
}

// ---- one regex dialect for the pane and its export ------------------------------------
//
// A pane filters with JavaScript's RegExp (compiled with the `s` flag, so `.` also matches an
// embedded CR as Python's does), while its export and history pages send the same source to the
// daemon's `regex` module. The constructs below read differently in the two, or are refused by
// only one, so the pane refuses them rather than show one set of lines and export another.
// tests/regex_dialect_cases.json runs the accepted forms through both engines.
const SAME_LETTER_ESCAPES = "bBdDfnrsStvwW";

// The first construct the two engines read differently, as the text to name it by, or null.
export function regexDialectIssue(src) {
  let inClass = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === "\\") {
      const n = src[i + 1];
      if (n === undefined) return null;   // a trailing backslash: RegExp refuses it itself
      if (n === "x" && /^[0-9A-Fa-f]{2}$/.test(src.slice(i + 2, i + 4))) { i += 3; continue; }
      if (n === "u" && /^[0-9A-Fa-f]{4}$/.test(src.slice(i + 2, i + 6))) { i += 5; continue; }
      if (/[A-Za-z]/.test(n) && !SAME_LETTER_ESCAPES.includes(n)) return "\\" + n;
      // A backreference to a missing group is octal in JS and an error in `regex`; \0 is NUL
      // (or octal) in both.
      if (/[1-9]/.test(n)) return "\\" + n;
      i += 1;
      continue;
    }
    if (inClass) {
      if (c === "[" && src[i + 1] === ":") return "[:";   // a POSIX class in `regex`
      if (c === "]") inClass = false;
      continue;
    }
    if (c === "[") {
      inClass = true;
      const j = src[i + 1] === "^" ? i + 2 : i + 1;
      if (src[j] === "]") return src.slice(i, j + 1);   // JS: empty class; `regex`: a literal ]
      i = j - 1;
      continue;
    }
    if (c === "{" && src[i + 1] === ",") return "{,";     // `regex`: {0,n}; JS: literal text
  }
  return null;
}

// A pane config read back from localStorage, which is hand-editable: each field of the wrong
// type falls back to the default a fresh pane takes, and unknown channel names are dropped.
export function paneCfgFromStorage(c) {
  const o = c && typeof c === "object" ? c : {};
  return {
    port: typeof o.port === "string" && o.port ? o.port : "all",
    channels: Array.isArray(o.channels) ? o.channels.filter((ch) => ALL_CHANS.includes(ch)) : ALL_CHANS,
    regex: typeof o.regex === "string" ? o.regex : "",
  };
}

// The timestamp column's width in ch; the scrollback is monospace, so a stamp's length is its
// width. It only grows within one time base, so rows stay aligned when a stamp gains a digit
// and scrolling never narrows it; another time base starts again from its own stamps.
// Returns `prev` itself while unchanged, so the caller writes the style only on growth.
export function tsColumnWidth(prev, mode, text) {
  if (prev && prev.mode === mode && text.length <= prev.ch) return prev;
  return { mode, ch: text.length };
}

// What an empty pane says, so a filter that hides everything never looks like a dead page.
// `total` is the rows past the clear point (and within a freeze), `scoped` those that pass
// the port and channel filters, `ports` the number of attached ports. Returns {text, title}:
// one short line for the pane and the longer explanation for its tooltip, or null when a pane
// with these counts would not be empty.
export function emptyPaneText({ total, scoped, cleared, ports, port, channels, regex }) {
  const say = (text, title = "") => ({ text, title });
  if (!total) {
    if (cleared) return say("Cleared (view only)", "Clear empties this view; the capture keeps every line.");
    if (!ports) {
      return say("No ports attached: + Attach one",
        "Attach a serial port with + Attach above, or start the daemon as mcuscoped --sim "
        + "for the zero-hardware demo.");
    }
    return say("Waiting for the first line");
  }
  if (!channels) return say("No channels ticked: tick one above");
  if (!scoped) {
    return port === "all" ? say(`${total} lines, none on the ticked channels`)
      : say(`No lines from ${port} on the ticked channels`);
  }
  return regex ? say(`${scoped} lines in scope, none match the regex`) : null;
}

// The footer's hint: how to copy a clipped line while live, and whether scrolling to the top
// of a paused pane will pull older lines from the capture.
export function paneHint(pane) {
  if (pane.autoscroll) return "dbl-click a line to copy";
  if (pane.historyBusy) return "loading older lines...";
  return historyIdTo(pane) === null ? "no older lines to load" : "scroll to the top for older lines";
}

// A divider row standing in for lines deliberately not loaded. An ordinary row to the panes,
// id just below the oldest row it precedes so it sorts into place, with its own `chan` to
// keep it out of the CAN/plot decoders and out of every channel filter (terminal.js matches
// and buildLine give it the marker's divider treatment). Shared by the reconnect backfill
// (api.js), the scroll-to-top history paging (terminal.js) and the stream's shed notice
// (api.js, `why` "shed by the live stream").
export function gapRow(oldest, gap, why = "not loaded") {
  return { id: oldest.id - 1, ts: oldest.ts, port: oldest.port, chan: "gap",
           raw: `gap: ${gap} lines ${why}` };
}

// ---- scroll-to-top history paging ---------------------------------------------------
//
// A pane starts from the shared buffer (a ring of the newest BUFFER_MAX lines) and pulls
// older pages from the capture when scrolled to its top. One page per top hit; the rows go
// into the pane only, not the shared buffer, so a rebuild (filter change, resume) drops
// them and the next top hit starts over from the buffer's oldest row.
export const HISTORY_PAGE = 200;
export const HISTORY_MAX = 5000;   // rows a pane may hold from the capture past the live set
export const HISTORY_HOPS = 5;     // pages one top hit may walk when the filter empties them

// The upper bound (inclusive) for the next page, or null when there is nothing to ask for:
// no rows yet, a fetch in flight, the walk finished, or the oldest line is the first past the
// pane's clear point (a cleared pane must not refill with what it cleared; the capture's first
// line when never cleared). A divider ahead of the oldest line (a shed or reconnect gap) names
// rows the capture holds, so the page is bounded by that line and paging fills the hole.
export function historyIdTo(pane) {
  if (pane.historyBusy || pane.historyDone || !pane.rows.length) return null;
  const floor = (pane.clearId || 0) + 1;
  if (pane.historyNext != null) return pane.historyNext >= floor ? pane.historyNext : null;
  const oldest = pane.rows.find((r) => r.chan !== "gap");
  if (!oldest || !(oldest.id > floor)) return null;
  return oldest.id - 1;
}

// What a fetched page does to the pane. `lines` is the page as served (newest first) after
// the pane's own filter, `served` its row count before that filter, `truncated` the
// envelope flag, `loaded` the rows earlier pages pulled, `oldestServedId` the smallest id
// the server answered with (null for an empty page), `floor` the pane's clear point. Returns the
// rows to prepend in capture order, whether the walk is finished, and where the next page's upper
// bound sits: below the served page rather than below the kept rows, or a page the filter emptied
// would be asked for again forever.
export function planHistoryPage({ lines, truncated, served, loaded, oldestServedId, floor = 0 }) {
  const rows = lines.slice().reverse();
  // Served short of the page and not clamped: the capture holds nothing older.
  const exhausted = !truncated && served < HISTORY_PAGE;
  const spent = loaded + rows.length >= HISTORY_MAX;
  // A divider ahead of the page, as the backfill marks a gap it did not close. The count is
  // exact while the capture's ids are contiguous, as the backfill's own is; a page ending one
  // row past the floor leaves nothing unloaded, so it gets no divider ("gap: 0 lines").
  if (spent && !exhausted && rows.length && rows[0].id - 1 > floor) {
    rows.unshift(gapRow(rows[0], rows[0].id - 1 - floor));
  }
  return { rows, done: exhausted || spent,
           nextIdTo: oldestServedId == null ? null : oldestServedId - 1 };
}
