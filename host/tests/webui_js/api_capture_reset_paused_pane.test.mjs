// api.js resetForDbReset: a paused pane's freeze point is an id of the old capture. The new
// capture's ids restart low, so left in place it sat above them, and the re-seed's rebuild
// folded the new capture into a pane that still read "paused". The pane must come out of the
// reset empty and still paused (the capture it froze on no longer exists).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makeRow, tick } from "./dom_stub.mjs";

const env = installDom();
let snap = Array.from({ length: 10 }, (_, i) => makeRow(i + 1, { raw: `old ${i + 1}` }));
globalThis.fetch = async (url) => {
  const u = String(url);
  let body = {};
  if (u.includes("/lines?match=")) body = { lines: [] };
  else if (u.includes("/lines")) body = { lines: [...snap].reverse() };
  else if (u.includes("/plot/channels")) body = { channels: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { connectWs } = await import(webuiUrl("api.js"));
const { panes, initTerminal, setAutoscroll } = await import(webuiUrl("terminal.js"));

test("a paused pane shows none of the new capture after a reset, and stays paused", async () => {
  initTerminal();
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  for (let i = 0; i < 4; i++) await tick(0);
  sock.onmessage({ data: JSON.stringify([{ capture: "A" }]) });
  await tick(0);
  const pane = panes[0];
  assert.equal(pane.rows.length, 10, "setup: the first capture did not load");
  setAutoscroll(pane, false);

  snap = Array.from({ length: 3 }, (_, i) => makeRow(i + 1, { raw: `new ${i + 1}` }));
  sock.onmessage({ data: JSON.stringify([{ capture: "B" }]) });
  for (let i = 0; i < 6; i++) await tick(0);

  assert.equal(pane.autoscroll, false, "a reset must not resume a paused pane");
  assert.deepEqual(pane.rows.map((r) => r.raw), [],
    "the new capture folded into a pane that still reads paused");
  setAutoscroll(pane, true);
  assert.deepEqual(pane.rows.map((r) => r.raw), ["new 1", "new 2", "new 3"],
    "resumed, the pane shows the new capture (positive control)");
});
