// The terminal's flush interval follows the stream's measured rate (api.js tickRate ->
// terminal.js setLineRate): 33 ms below 200 lines/s, so a reply shows at once; 250 ms from
// there, where each flush repaints a whole screenful and the flush rate is the browser's cost.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ lines: [] }) });

// The delay of every flush the terminal schedules.
const delays = [];
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...a) => {
  if (fn && fn.name === "flush") delays.push(ms);
  return realSetTimeout(fn, ms, ...a);
};

const { state, buffer } = await import(webuiUrl("state.js"));
const { panes } = await import(webuiUrl("terminal.js"));
const before = env.intervals.length;
const { connectWs } = await import(webuiUrl("api.js"));
const rateTick = env.intervals.slice(before)[0].fn;

let sock = null;
let nextId = 1;
let clock = 1000;   // capture time, s

// `count` rows captured `stepS` seconds apart, delivered in one frame.
function feed(count, stepS) {
  const rows = [];
  for (let i = 0; i < count; i++) rows.push(makeRow(nextId++, { ts: (clock += stepS) }));
  sock.onmessage({ data: JSON.stringify(rows) });
}

async function stream() {
  buffer.length = 0;
  state.maxId = 0;
  nextId = 1;
  clock = 1000;
  panes.length = 0;
  panes.push(makePane({ autoscroll: true }));
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await tick(0);
  rateTick();   // a quiet window: whatever rate an earlier test left is gone
  rateTick();
}

// The delay the next flush is scheduled with, once any pending one has run.
async function nextFlushDelay() {
  await tick(300);
  delays.length = 0;
  feed(1, 0.001);
  assert.equal(delays.length, 1, "a live row scheduled no flush (positive control)");
  return delays[0];
}

test("a slow stream flushes at 33 ms", async () => {
  await stream();
  feed(100, 0.01);   // 100 lines/s
  rateTick();
  assert.equal(await nextFlushDelay(), 33);
});

test("a window at 200 lines/s or more flushes at 250 ms", async () => {
  await stream();
  feed(300, 0.003);   // ~330 lines/s, well under the shed
  rateTick();
  assert.equal(await nextFlushDelay(), 250, "a busy stream kept repainting ~30 times a second");
});

test("199 lines/s is not busy and 200 is", async () => {
  await stream();
  feed(199, 1 / 198);   // n rows span n - 1 steps: 199 rows over 1 s
  rateTick();
  assert.equal(await nextFlushDelay(), 33, "just under the threshold went busy");
  rateTick();           // close the window holding the probe row
  feed(200, 1 / 199);
  rateTick();
  assert.equal(await nextFlushDelay(), 250, "the threshold itself was not busy");
});

test("a quiet window after a busy one goes back to 33 ms", async () => {
  await stream();
  feed(300, 0.003);
  rateTick();
  assert.equal(await nextFlushDelay(), 250, "setup: the stream did not go busy");
  rateTick();   // nothing arrived
  assert.equal(await nextFlushDelay(), 33, "the slow rate stuck after the stream went quiet");
});
