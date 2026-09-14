// api.js seedPlotHistory: when a per-port /plot/channels request fails, the seed falls back to
// the unfiltered list rather than seeding nothing.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const row = (port) => ({ name: "temp", port, sid: null, kind: "analog", last_ts: 999.1, count: 1 });
const seen = [];
globalThis.fetch = async (url) => {
  const u = new URL(String(url), "http://x");
  const q = u.searchParams;
  seen.push(u.pathname + u.search);
  if (u.pathname === "/plot/channels" && q.has("port")) {
    return { ok: false, status: 500, headers: { get: () => null }, json: async () => ({ error: "boom" }) };
  }
  let body = { lines: [], truncated: false };
  if (u.pathname === "/plot/channels") body = { channels: [row("p2")], ports: ["p1", "p2"] };
  else if (u.pathname === "/plot/series") body = { points: [{ line_id: 3, ts: 999.1, tick_ms: 1, value: 21 }] };
  else if (u.pathname === "/lines" && !q.get("match")) {
    body = { lines: [{ id: 10, ts: 1000, port: "p2", chan: "debug", raw: "hello" }], truncated: false };
  }
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};

const { charts } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));

test("a failed per-port channel list still seeds each name's newest port", async () => {
  const errors = [];
  const orig = console.error;
  console.error = (...a) => errors.push(a.join(" "));
  try {
    connectWs();
    env.sockets.at(-1).onopen();
    for (let i = 0; i < 6; i++) await tick(0);
  } finally {
    console.error = orig;
  }
  assert.ok(seen.some((u) => u.startsWith("/plot/channels?port=")), "the per-port path was not taken");
  assert.deepEqual(charts.get("p2|adhoc")?.ys.get("temp"), [21], `requests: ${seen.join(" | ")}`);
  assert.ok(errors.some((e) => e.includes("per-port plot channel list failed")), errors.join("\n"));
});
