// terminal.js: the pane filter and the rebuild-from-buffer projection.
//
// A pane is a filtered projection of the one shared buffer, so `matches` decides what every
// pane shows and `rebuild` is what re-derives it after a filter change, a resume, or a
// backfill. The timestamp column (fmtTs) is read back off the rendered rows.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state, buffer } = await import(webuiUrl("state.js"));
const { matches, rebuild, render, updateJump, VIEW_MAX, panes, scheduleFlush,
        applyRegex, refillRegexBudget, REGEX_BUDGET_MS } =
  await import(webuiUrl("terminal.js"));

const row = (over) => makeRow(1, over);

test("matches applies the port, channel and regex filters", () => {
  const pane = makePane();
  assert.equal(matches(pane, row({ port: "p1" })), true);
  assert.equal(matches(pane, row({ port: "p9" })), true, "port 'all' takes every port");

  pane.port = "p1";
  assert.equal(matches(pane, row({ port: "p1" })), true);
  assert.equal(matches(pane, row({ port: "p9" })), false);

  pane.port = "all";
  pane.channels = new Set(["resp", "event"]);
  assert.equal(matches(pane, row({ chan: "resp" })), true);
  assert.equal(matches(pane, row({ chan: "debug" })), false);

  pane.channels = new Set(["debug"]);
  pane.regex = /^ready/;
  assert.equal(matches(pane, row({ raw: "ready to go" })), true);
  assert.equal(matches(pane, row({ raw: "not ready" })), false);
  pane.regex = null;
  assert.equal(matches(pane, row({ raw: "not ready" })), true);
});

test("rebuild re-derives the pane from the shared buffer", () => {
  buffer.length = 0;
  for (let i = 1; i <= 10; i++) {
    buffer.push(makeRow(i, { port: i % 2 ? "p1" : "p2", chan: i > 8 ? "resp" : "debug" }));
  }
  const pane = makePane({ port: "p1" });
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 3, 5, 7, 9]);

  pane.regex = /line (3|9)/;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [3, 9]);

  pane.regex = null;
  pane.clearId = 5;
  rebuild(pane);
  assert.deepEqual(pane.rows.map((r) => r.id), [7, 9], "rows up to clearId stay cleared");
});

test("rebuild trims to VIEW_MAX and clears the pending count", () => {
  buffer.length = 0;
  for (let i = 1; i <= VIEW_MAX + 250; i++) buffer.push(makeRow(i));
  const pane = makePane({ pending: 42 });
  rebuild(pane);
  assert.equal(pane.rows.length, VIEW_MAX);
  assert.equal(pane.rows[0].id, 251, "the trim must drop the oldest rows");
  assert.equal(pane.rows.at(-1).id, VIEW_MAX + 250);
  assert.equal(pane.pending, 0, "the backlog is folded into rows, so the counter resets");
});

test("rebuild does not resume a paused pane", () => {
  buffer.length = 0;
  buffer.push(makeRow(1));
  const pane = makePane({ autoscroll: false });
  rebuild(pane);
  assert.equal(pane.autoscroll, false, "re-filtering must never un-pause a pane");
  assert.equal(pane.selfScroll, true, "the scrollTop clamp this causes must be marked as ours");
});

test("the jump button counts what arrived while paused", () => {
  const pane = makePane({ autoscroll: false, pending: 0 });
  updateJump(pane);
  assert.equal(pane.jumpBtn.textContent, "↓ latest");
  pane.pending = 7;
  updateJump(pane);
  assert.equal(pane.jumpBtn.textContent, "↓ 7 new");
});

// Read the rendered rows back: each is a .ln div of [ts, port-tag, tag, msg] spans.
function renderRows(pane) {
  render(pane);
  return pane.vlist.children.map((ln) => ({
    cls: ln.className,
    parts: ln.children.map((s) => s.textContent),
  }));
}

test("the timestamp column follows the shared time mode", () => {
  buffer.length = 0;
  const pane = makePane();
  pane.rows = [
    makeRow(1, { ts: 1000, chan: "event", raw: "!can 500 - 100 -" }),
    makeRow(2, { ts: 1002.25, chan: "debug", raw: "plain line" }),
  ];
  state.anchorTs = 1000;
  state.anchorTick = 500;

  state.timeMode = "rel";
  assert.deepEqual(renderRows(pane).map((r) => r.parts[0]), ["0.000s", "2.250s"]);

  state.timeMode = "tick";
  assert.deepEqual(renderRows(pane).map((r) => r.parts[0]), ["0", "-"],
    "a line with no tick reads as '-', not as zero");

  state.timeMode = "host";
  const d = new Date(1002.25 * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  assert.equal(renderRows(pane)[1].parts[0],
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.250`);
  state.timeMode = "host";
});

test("a row renders its port, channel tag and text, and an ERR response is flagged", () => {
  const pane = makePane();
  pane.rows = [
    makeRow(1, { chan: "resp", raw: "<1 ERR 3 bad args" }),
    makeRow(2, { chan: "resp", raw: "<2 OK" }),
    makeRow(3, { chan: "marker", raw: "!m @99 boot done" }),
  ];
  const out = renderRows(pane);
  assert.equal(out[0].cls, "ln resp err");
  assert.deepEqual(out[0].parts.slice(1), ["p1", "resp", "<1 ERR 3 bad args"]);
  assert.equal(out[1].cls, "ln resp", "an OK response is not an error");
  assert.equal(out[2].cls, "ln marker");
  assert.equal(out[2].parts[1], "marker: boot done", "the wire prefix belongs in the tick column");
});

test("render only builds the visible window", () => {
  const pane = makePane();
  pane.rows = Array.from({ length: 2000 }, (_, i) => makeRow(i + 1));
  render(pane);
  assert.ok(pane.vlist.children.length < 100,
    `virtualizer built ${pane.vlist.children.length} rows for a 300px pane`);
  assert.equal(pane.winLast, 2000, "an autoscrolling pane renders the newest rows");
  assert.equal(pane.shownEl.textContent, "2000 lines");
  assert.equal(pane.vlist.style.paddingTop, `${pane.winFirst * 18}px`);
  assert.equal(pane.vlist.style.paddingBottom, "0px");
});

// The daemon caps the pattern length AND passes timeout= to `regex`; the web UI is the third
// client of this grammar and had only the cap. A 6-character pattern passes the cap and
// backtracks for tens of seconds per line, per keystroke, freezing the box that would undo it.
// JS has no regex timeout, so the budget below is the guard: a bounded hiccup, then the
// pattern is dropped, said so, and never run again.
const EVIL = "(a+)+$";
const EVIL_LINE = "a".repeat(26) + "b";   // ~1 s per test() against EVIL, well past the budget

test("a pattern that blows the matching budget is dropped, and the pane says so", () => {
  buffer.length = 0;
  for (let i = 1; i <= 20; i++) buffer.push(makeRow(i, { raw: EVIL_LINE }));
  const pane = makePane();
  applyRegex(pane, EVIL);
  assert.ok(pane.regex, "the pattern is inside MAX_MATCH_LEN, so it does arm");

  // Counted, not timed: how long one evil match takes is the runner's business (26 s on the
  // Windows CI leg against ~1 s here, which failed a `spent < 20 s` bound that was measuring
  // the machine). What the guard promises is that the buffer is not matched through - one
  // row's hiccup, then the pattern is gone.
  let evilMatches = 0;
  const realTest = RegExp.prototype.test;
  RegExp.prototype.test = function (s) {
    if (this.source === EVIL) evilMatches++;
    return realTest.call(this, s);
  };
  try {
    rebuild(pane);
  } finally {
    RegExp.prototype.test = realTest;
  }

  assert.equal(evilMatches, 1, `the whole buffer was still matched (${evilMatches} rows)`);
  assert.equal(pane.regex, null, "the pattern must be dropped, not left to freeze the tab");
  assert.equal(pane.matchInput.classList.contains("invalid"), true);
  assert.match(pane.matchInput.title, /UNFILTERED/,
    "an unfiltered view must never be shown as though it were the filtered one");
  assert.equal(pane.rows.length, 20,
    "the dropped pattern must leave a whole view, not a half-filtered one");
});

test("a dropped pattern is never armed again", () => {
  const pane = makePane();
  applyRegex(pane, EVIL);
  refillRegexBudget(pane);
  matches(pane, makeRow(1, { raw: EVIL_LINE }));   // one live row: one episode, one hiccup
  assert.equal(pane.regex, null);

  applyRegex(pane, EVIL);
  assert.equal(pane.regex, null, "re-typing the same pattern must not re-run it");
  assert.equal(pane.matchInput.classList.contains("invalid"), true);

  applyRegex(pane, "^line");
  assert.ok(pane.regex, "a different pattern still arms");
  assert.equal(pane.matchInput.classList.contains("invalid"), false);
  assert.equal(pane.regexBudget, REGEX_BUDGET_MS, "arming refills the budget");
});

test("an ordinary pattern's per-row cost cannot accumulate into a false drop", () => {
  const pane = makePane();
  applyRegex(pane, "^line");
  for (let i = 0; i < 50000; i++) {
    refillRegexBudget(pane);                 // api.js does this per live row
    matches(pane, makeRow(i, { raw: "line " + i }));
  }
  assert.ok(pane.regex, "a cheap pattern must survive any number of rows");
  assert.equal(pane.matchInput.classList.contains("invalid"), false);
});

// A rebuild re-derives rows from the shared buffer, which already holds anything still sitting
// in the pane's queue. Leaving the queue alone let the next flush append those rows a second
// time, so a backfill landing mid-stream duplicated lines in the terminal.
test("rebuild drops queued rows the new row set already contains", async () => {
  buffer.length = 0;
  for (let i = 1; i <= 5; i++) buffer.push(makeRow(i));
  const pane = makePane();
  panes.push(pane);
  pane.queue.push(buffer[3], buffer[4]);   // queued by the live path, not yet flushed

  rebuild(pane);
  assert.equal(pane.queue.length, 0, "the rebuild folded these rows in already");

  scheduleFlush();
  await tick(60);
  assert.deepEqual(pane.rows.map((r) => r.id), [1, 2, 3, 4, 5],
    "a flush after a rebuild must not append the queued rows twice");
});
