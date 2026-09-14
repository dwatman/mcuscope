// api.js seedPlotHistory with two boards declaring one channel name: an unfiltered
// /plot/channels names only the board with the newest sample, so the seed must list per port,
// taking the port set from /status too, or the other board's history never comes back.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const row = (port, lastTs) => ({ name: "temp", port, sid: null, kind: "analog", last_ts: lastTs, count: 2 });
const SERIES = {
  p1: [{ line_id: 1, ts: 990.0, tick_ms: 1, value: 11 }, { line_id: 2, ts: 990.1, tick_ms: 2, value: 12 }],
  p2: [{ line_id: 3, ts: 999.0, tick_ms: 1, value: 21 }, { line_id: 4, ts: 999.1, tick_ms: 2, value: 22 }],
};

const seen = [];
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  const q = u.searchParams;
  seen.push(u.pathname + u.search);
  let body = { lines: [], truncated: false };
  if (u.pathname === "/plot/channels") {
    // p1 is shadowed on its only name: the unfiltered list carries p2 alone.
    body = { channels: q.has("port") ? [row(q.get("port"), q.get("port") === "p1" ? 990.1 : 999.1)]
                                     : [row("p2", 999.1)] };
  } else if (u.pathname === "/status") {
    body = { ports: [{ alias: "p1" }, { alias: "p2" }] };
  } else if (u.pathname === "/plot/series") {
    body = { name: q.get("name"), points: SERIES[q.get("port")] || [] };
  } else if (u.pathname === "/lines" && !q.get("match")) {
    body = { lines: [{ id: 10, ts: 1000, port: "p2", chan: "debug", raw: "hello" }], truncated: false };
  }
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};

const { charts } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));

test("a name two boards share seeds each board's history, the shadowed one included", async () => {
  connectWs();
  env.sockets.at(-1).onopen();
  for (let i = 0; i < 6; i++) await tick(0);

  assert.deepEqual(charts.get("p1|adhoc")?.ys.get("temp"), [11, 12],
    `p1's stored history never came back; requests: ${seen.join(" | ")}`);
  assert.deepEqual(charts.get("p2|adhoc")?.ys.get("temp"), [21, 22]);
  const series = seen.filter((u) => u.startsWith("/plot/series"));
  assert.equal(series.length, 2, "one series request per (port, name), not per name");
});
