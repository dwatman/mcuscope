// api.js: the plot history seed asks for the group window span (chrome.js groupWindow), so the
// re-seed after a capture reset covers the span the rebuilt charts take, not the 30 s default.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

// One ad-hoc channel, silent for 10 s before the anchor row.
const ANCHOR = { id: 5, ts: 1000, port: "p1", chan: "debug", raw: "hello" };
const CHANNEL = { name: "v", port: "p1", sid: null, kind: "analog", last_ts: 990, count: 1 };
const IDLE_MS = 10000;
const series = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  let body;
  if (u.startsWith("/lines?match=")) body = { lines: [] };
  else if (u.startsWith("/lines")) body = { lines: [ANCHOR] };
  else if (u.startsWith("/plot/channels")) body = { channels: [CHANNEL] };
  else if (u.startsWith("/plot/series")) {
    series.push(u);
    body = { points: [{ line_id: 4, ts: 990, tick_ms: 10, value: 1 }] };
  } else body = {};
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};

const { state } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const { PLOT_WINDOW_DEFAULT } = await import(webuiUrl("chrome.js"));
const { connectWs } = await import(webuiUrl("api.js"));

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }
const lastMs = () => Number(new URLSearchParams(series.at(-1).split("?")[1]).get("last_ms"));

test("the capture-reset re-seed asks for the shift-clicked span plus the channel's idle time", async () => {
  state.maxId = 0;
  connectWs();
  const sock = env.sockets.at(-1);
  sock.onopen();
  await settle();
  assert.equal(series.length, 1, "the page-load seed never asked for the series");
  assert.equal(lastMs(), PLOT_WINDOW_DEFAULT * 1000 + IDLE_MS, "before any shift-click the seed is 30 s");
  sock.onmessage({ data: JSON.stringify([{ capture: "cap-a" }]) });   // adopted, not a reset

  const chart = P.charts.get("p1|adhoc");
  assert.ok(chart, "the seed built no chart to shift-click");
  chart.winEl.children[2].emit("click", { shiftKey: true });   // 5 min, group-wide
  assert.equal(chart.window, 300);

  sock.onmessage({ data: JSON.stringify([{ capture: "cap-b" }]) });
  await settle();
  assert.equal(series.length, 2, "the capture reset did not re-seed");
  assert.equal(lastMs(), 300 * 1000 + IDLE_MS, "the re-seed asked for the default span");
  assert.equal(P.charts.get("p1|adhoc")?.window, 300, "the rebuilt chart shows another span");
});
