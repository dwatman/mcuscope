// ---- the terminal pane model, with no DOM in it -------------------------------------
//
// createPane() built this object and wired ten listeners in one breath, so there was no
// way to obtain a pane without a <template> from index.html. The test stub answered that
// by hand-transcribing all twenty fields, including semantic ones like the regex budget
// and the freeze boundary - the same mirror pattern state.js records as having silently
// lost a clause twice. A field added to one copy and not the other simply behaves
// differently under test than in the browser.
//
// So the shape lives here, once. terminal.js adds the elements and the listeners; the
// stub adds fake elements. Neither restates what a pane *is*.

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
    clearId: 0,           // "cleared" boundary: rebuild ignores buffered lines up to this id
    frozenId: 0,          // paused-at boundary: rebuild ignores buffered lines past this id
    frozenRows: null,     // rows the freeze covers, snapshotted at pause; null while live
    selfScroll: false,
  };
}
