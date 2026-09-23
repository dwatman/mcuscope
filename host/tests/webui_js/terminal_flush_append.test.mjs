// terminal.js flush: once a pane holds VIEW_MAX rows, the VIEW_MAX trim renumbers every row, and
// the append-only path (shiftWindow) must still find the rows it drew at their new indices.
// Left unadjusted, every flush at the cap rebuilt the whole visible window (about 60 elements,
// 30 times a second per live pane), which on a bench left open is the steady state.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

installDom();
await import(webuiUrl("state.js"));
const { panes, render, scheduleFlush, VIEW_MAX } = await import(webuiUrl("terminal.js"));

// A live pane already at the cap, rendered, with `extra` rows queued for the next flush.
function atCap(extra) {
  panes.length = 0;
  const pane = makePane({ autoscroll: true, viewH: 300 });
  panes.push(pane);
  pane.rows = Array.from({ length: VIEW_MAX }, (_, i) => makeRow(i + 1));
  render(pane);
  for (let i = 0; i < extra; i++) pane.queue.push(makeRow(VIEW_MAX + 1 + i));
  return pane;
}

async function flushNow() {
  scheduleFlush();
  await tick(60);   // FLUSH_MS is 33
}

test("a flush at VIEW_MAX keeps the drawn rows' elements and appends only the new ones", async () => {
  const pane = atCap(5);
  const before = pane.domEls.slice();
  assert.ok(before.length > 10, "the window must hold more rows than the flush adds");
  await flushNow();
  assert.equal(pane.rows.length, VIEW_MAX);
  assert.equal(pane.rows[0].id, 6, "the trim must drop the oldest rows");
  const after = pane.domEls;
  assert.equal(after.length, before.length);
  for (let i = 0; i < before.length - 5; i++) {
    assert.equal(after[i], before[i + 5],
      `element ${i} was rebuilt: the window indices did not follow the VIEW_MAX trim`);
  }
  assert.deepEqual(after.slice(-5).map((el) => el.__row.id),
    [VIEW_MAX + 1, VIEW_MAX + 2, VIEW_MAX + 3, VIEW_MAX + 4, VIEW_MAX + 5]);
  assert.deepEqual([...pane.vlist.children], after, "the DOM holds exactly the window, in order");
});

test("a flush larger than the window at the cap rebuilds it and still shows the newest rows", async () => {
  const pane = atCap(200);
  await flushNow();
  assert.equal(pane.rows.at(-1).id, VIEW_MAX + 200);
  assert.equal(pane.domEls.at(-1).__row.id, VIEW_MAX + 200);
  assert.deepEqual([...pane.vlist.children], pane.domEls);
  assert.equal(pane.winLast, VIEW_MAX);
  assert.equal(pane.winLast - pane.winFirst, pane.domEls.length);
});
