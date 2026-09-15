// Fix-diff 2, PD-4: pane.js planHistoryPage must not show a divider that counts nothing.
// loadHistoryPage sends since_id = clearId, so the oldest row a page can serve is floor + 1;
// when the budget runs out on exactly that row there is nothing left unloaded, and the old
// arithmetic still prepended "gap: 0 lines not loaded".
// pane.js is DOM-free, so the arithmetic is driven directly.

import test from "node:test";
import assert from "node:assert/strict";
import { makeRow } from "./dom_stub.mjs";
import { planHistoryPage, HISTORY_PAGE, HISTORY_MAX } from "../../mcuscope/webui/pane.js";

// A full page, newest first, whose oldest row is `oldest`, spending the last of the budget.
function spentPage(oldest) {
  const lines = Array.from({ length: HISTORY_PAGE }, (_, i) => makeRow(oldest + HISTORY_PAGE - 1 - i));
  return planHistoryPage({ lines, truncated: false, served: HISTORY_PAGE,
                           loaded: HISTORY_MAX - HISTORY_PAGE, oldestServedId: oldest, floor: 1000 });
}

test("the budget spent on the first row past the clear point leaves no divider", () => {
  const step = spentPage(1001);
  assert.equal(step.done, true, "the budget is spent, so the walk must still end");
  assert.equal(step.rows.length, HISTORY_PAGE, "a divider was prepended to a complete page");
  assert.equal(step.rows[0].id, 1001, "the page must start at the first row past the clear point");
  assert.equal(step.rows.filter((r) => r.chan === "gap").length, 0,
    "a pane holding everything above its clear point was told lines were not loaded");
});

test("one row higher, the divider is there and counts the one line left behind", () => {
  const step = spentPage(1002);
  assert.equal(step.done, true);
  assert.equal(step.rows[0].chan, "gap", "a page stopping above the clear point must say so");
  assert.equal(step.rows[0].raw, "gap: 1 lines not loaded");
  assert.equal(step.rows[0].id, 1001, "the divider sorts just below the page it precedes");
  assert.equal(step.rows[1].id, 1002);
});

// The divider is the budget's, not the floor's: an uncleared pane (floor 0) stopping at id 1
// has nothing below it either.
test("an uncleared pane paged to the capture's first line gets no divider", () => {
  const lines = Array.from({ length: HISTORY_PAGE }, (_, i) => makeRow(HISTORY_PAGE - i));
  const step = planHistoryPage({ lines, truncated: false, served: HISTORY_PAGE,
                                 loaded: HISTORY_MAX - HISTORY_PAGE, oldestServedId: 1 });
  assert.equal(step.done, true);
  assert.equal(step.rows.filter((r) => r.chan === "gap").length, 0,
    "the capture's own first line is not a gap");
  assert.equal(step.rows[0].id, 1);
});
