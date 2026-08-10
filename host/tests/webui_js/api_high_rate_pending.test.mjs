// The "N new" count on a paused pane, across the high-rate shed and a filter change.
//
// Above HIGH_RATE_ON the panes stop being fed entirely (api.js routeLiveRow): no filter test,
// no queue - and no `pending` increment either, so a paused pane's jump button undercounted
// what had arrived for the rest of the session. The data was never at risk (the shared buffer
// keeps filling), only the count, which is why it went unnoticed.
//
// The same counter is wrong for a second reason: a filter change re-derives everything about a
// frozen pane except `pending`, whose increments were counted against the OLD filter. Both end
// in a rebuild, so rebuild re-derives the count from the shared buffer.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ lines: [] }) });

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes, rebuild } = await import(webuiUrl("terminal.js"));
// api.js registers exactly one interval at module scope (the rate window); everything else it
// imports is already loaded above, so the entry it adds here is that one. The stub captures
// timers rather than arming them, so the window is closed on demand.
const before = env.intervals.length;
const { connectWs } = await import(webuiUrl("api.js"));
const rateTick = env.intervals.slice(before)[0].fn;

const live = makePane({ autoscroll: true });
const paused = makePane({ autoscroll: false });
panes.push(live, paused);

let sock = null;
let nextId = 1;

function feed(count) {
  const rows = [];
  for (let i = 0; i < count; i++) rows.push(makeRow(nextId++));
  sock.onmessage({ data: JSON.stringify(rows) });
}

test("open the stream and let the backfill settle", async () => {
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  assert.equal(state.maxId, 0);
});

test("an ordinary rate counts every matching row for a paused pane", () => {
  rateTick();          // open the rate window
  feed(10);
  assert.equal(paused.pending, 10);
});

test("the shed stops counting, and the release recovers the count", () => {
  feed(2500);
  rateTick();          // HIGH_RATE_ON: the panes stop being fed
  const shed = paused.pending;
  feed(500);
  assert.equal(paused.pending, shed, "the shed is only worth doing if it skips the pane work");

  rateTick();          // that burst's window: still far above the threshold
  assert.equal(paused.pending, shed, "the shed must last as long as the rate does");
  rateTick();          // a quiet window: the guard lets go and rebuilds every pane
  assert.equal(paused.pending, 3010,
    "the jump button undercounts the shed episode for the rest of the session");
  assert.equal(paused.jumpBtn.textContent, "↓ 3010 new");
  assert.equal(live.pending, 0, "a live pane folded its backlog in");
  assert.ok(buffer.length >= 3010, "the rows themselves were never at risk");
});

test("a filter change recounts the backlog against the new filter", () => {
  paused.regex = /line 7$/;
  rebuild(paused);
  assert.equal(paused.pending, 1, "the count still stood for the filter the pane no longer has");
  paused.regex = null;
  rebuild(paused);
  assert.equal(paused.pending, 3010);
});
