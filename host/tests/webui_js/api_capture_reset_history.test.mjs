// api.js: a capture reset ends every pane's history walk. A page in flight, or the
// next-page cursor, names ids of the capture that no longer exists.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async (url) => {
  const u = String(url);
  let body = {};
  if (u.includes("/lines")) body = { lines: [] };
  else if (u.includes("/plot/channels")) body = { channels: [] };
  else if (u.includes("/plot/series")) body = { points: [] };
  return { ok: true, status: 200, json: async () => body };
};

const { panes } = await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));

const pane = makePane();
panes.push(pane);
const settle = async () => { for (let i = 0; i < 6; i++) await tick(0); };

test("a new capture token resets each pane's history cursor and generation", async () => {
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  sock.onmessage({ data: JSON.stringify([{ capture: "cap-a" }]) });
  await settle();

  pane.historyNext = 123; pane.historyDone = true; pane.historyLoaded = 200;
  const gen = pane.historyGen;
  sock.onmessage({ data: JSON.stringify([{ capture: "cap-b" }]) });
  // Synchronously: the re-seed's rebuild comes only after its fetch, and a page can land first.
  assert.equal(pane.historyNext, null, "the next page would be read from the old capture");
  assert.equal(pane.historyDone, false);
  assert.equal(pane.historyLoaded, 0);
  assert.ok(pane.historyGen > gen, "a page in flight across the reset would still land");
});
