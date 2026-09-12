// filterPaneTo: another panel points the last terminal pane at a pattern it built, so
// "0x321 looks wrong, show me its raw frames" is a click instead of hand-typing a regex that
// has to match parseCanEvent's grammar exactly. can.js/app.js are the callers; this is the
// terminal half, and the clear-again path, which is the one a caller forgets.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { pushBuffer } = await import(webuiUrl("state.js"));
const { panes, rebuild, filterPaneTo } = await import(webuiUrl("terminal.js"));

for (const [i, raw] of ["!can 10 - 100 0102", "!can 11 - 321 0304", "!can2 12 - 321 05"].entries()) {
  pushBuffer(makeRow(i + 1, { chan: "event", raw }));
}

// Two panes: the hook drives the LAST one, so a user watching a filtered pane keeps it.
const kept = makePane();
const target = makePane();
panes.push(kept, target);
rebuild(kept);
rebuild(target);

test("a pattern sets the last pane's filter and re-derives its rows", () => {
  filterPaneTo("^!can \\d+ \\S+ 321 ");
  assert.equal(target.matchInput.value, "^!can \\d+ \\S+ 321 ");
  assert.ok(target.regex, "the pattern must be applied, not just typed into the box");
  assert.deepEqual(target.rows.map((r) => r.id), [2],
    "the other bus and the other id must be filtered out");
  assert.equal(kept.matchInput.value, "", "only the last pane is retargeted");
  assert.deepEqual(kept.rows.map((r) => r.id), [1, 2, 3]);
});

test("an empty pattern clears the filter rather than matching nothing", () => {
  filterPaneTo("");
  assert.equal(target.matchInput.value, "");
  assert.equal(target.regex, null, "an empty box is no filter at all");
  assert.deepEqual(target.rows.map((r) => r.id), [1, 2, 3]);
  filterPaneTo(undefined);
  assert.equal(target.matchInput.value, "", "a missing pattern is the same as an empty one");
  assert.equal(target.regex, null);
});

test("a pattern that cannot compile is refused inline, not thrown at the caller", () => {
  filterPaneTo("^!can (");
  assert.equal(target.regex, null);
  assert.ok(target.matchInput.classList.contains("invalid"));
  assert.match(target.matchInput.title, /invalid pattern/);
  filterPaneTo("");
  assert.equal(target.matchInput.classList.contains("invalid"), false);
});

test("the pending debounce cannot re-apply the value the user typed before", () => {
  // The match box re-renders on a 200 ms timer; a hook firing inside that window would be
  // overwritten by the stale typed value if the timer were left armed.
  target.matchInput.value = "half-typed";
  target.regexTimer = setTimeout(() => { throw new Error("the debounced rebuild must be cancelled"); }, 0);
  filterPaneTo("^!can2 ");
  assert.deepEqual(target.rows.map((r) => r.id), [3]);
  return new Promise((r) => setTimeout(r, 5));
});
