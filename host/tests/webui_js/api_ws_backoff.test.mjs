// api.js: the reconnect backoff and the staging cap.
//
// Two ways a sick daemon used to cost the browser unbounded work. The backoff was reset
// inside onopen, so a daemon that accepts a handshake and immediately closes it reconnected
// at 1 Hz forever, each attempt running a full backfill and rebuilding every pane. And
// `staging` - where live rows wait while that backfill runs - had no cap, so a slow backfill
// against a saturated link retained rows the shared buffer would have evicted anyway.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ lines: [] }) });

const { state, buffer, BUFFER_MAX } = await import(webuiUrl("state.js"));
await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const WS_STABLE_MS = 5000;

// Capture timers instead of arming them, so the delays api.js asks for can be read back and
// the "connection held" timer fires only when this test says so.
function withCapturedTimers(fn) {
  const timers = [];
  const real = globalThis.setTimeout;
  const realClear = globalThis.clearTimeout;
  globalThis.setTimeout = (cb, ms) => { timers.push({ cb, ms }); return 0; };
  globalThis.clearTimeout = () => {};
  try { fn(timers); } finally { globalThis.setTimeout = real; globalThis.clearTimeout = realClear; }
  return timers;
}

test("a connection that never proves stable keeps the backoff climbing", () => {
  const timers = withCapturedTimers(() => {
    for (let i = 0; i < 3; i++) {
      connectWs();
      const sock = env.sockets.at(-1);
      sock.onopen();
      sock.onclose({});     // accepted, then dropped before it held
    }
  });
  const delays = timers.filter((t) => t.ms !== WS_STABLE_MS).map((t) => t.ms);
  assert.deepEqual(delays, [1000, 2000, 4000],
    "an accept-then-close loop must back off, not reconnect at 1 Hz forever");
});

test("a connection that holds resets the backoff", () => {
  const timers = withCapturedTimers((captured) => {
    connectWs();
    const sock = env.sockets.at(-1);
    sock.onopen();
    const stable = captured.find((t) => t.ms === WS_STABLE_MS);
    assert.ok(stable, "opening arms the stability timer");
    stable.cb();          // the connection held for WS_STABLE_MS
    sock.onclose({});
  });
  const delays = timers.filter((t) => t.ms !== WS_STABLE_MS).map((t) => t.ms);
  assert.deepEqual(delays, [1000], "a proven connection starts the next backoff from the minimum");
});

test("staging is capped while the backfill runs", async () => {
  state.maxId = 0;
  buffer.length = 0;
  let release;
  globalThis.fetch = async () => {
    await new Promise((r) => { release = r; });
    return { ok: true, status: 200, json: async () => ({ lines: [] }) };
  };

  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();          // the backfill now hangs on the fetch above; live rows stage
  await tick(0);

  const over = 50;
  const rows = Array.from({ length: BUFFER_MAX + over },
                          (_, i) => ({ id: i + 1, ts: 1000 + i, port: "p1", chan: "debug", raw: "x" }));
  sock.onmessage({ data: JSON.stringify(rows) });

  release();
  await tick(20);

  assert.equal(state.maxId, BUFFER_MAX,
    `staging kept ${state.maxId} rows; the cap is ${BUFFER_MAX}`);
});
