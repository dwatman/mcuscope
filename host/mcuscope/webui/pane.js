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
    frozenId: 0,          // paused-at boundary: rebuild ignores buffered lines past this id
    frozenRows: null,     // rows the freeze covers, snapshotted at pause; null while live
    selfScroll: false,
  };
}
