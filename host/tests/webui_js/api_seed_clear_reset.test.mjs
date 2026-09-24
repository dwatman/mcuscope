// api.js: the plot history seed across a clear-all or capture reset (FW-8).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
installExportDaemon(env);

// `route` answers a request first; returning undefined falls through to the export double.
const daemon = globalThis.fetch;
let route = null;
globalThis.fetch = async (url, opt = {}) => {
  const r = route && route(String(url), opt);
  return (r && (await r)) || daemon(url, opt);
};
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null },
                        json: async () => body, blob: async () => new Blob(["x"]) });

const { state } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { connectWs } = await import(webuiUrl("api.js"));

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

// Requests matching `pred` wait until released; `answer(url, opt)` is what they then get.
function holdOn(pred, answer) {
  const held = [];
  route = (u, opt) => (pred(u, opt) ? new Promise((r) => held.push(() => r(answer(u, opt)))) : undefined);
  return held;
}

// ---- FW-8: the plot history seed (first: it needs a fresh page, state.maxId 0) -------------

const SESSION_ROW = { id: 5, ts: 1000, port: "p1", chan: "debug", raw: "hello" };
const CHANNEL = { name: "v", port: "p1", sid: null, kind: "analog", last_ts: 999, count: 2 };
const points = (a, b) => ({ points: [{ line_id: 1, ts: 998, tick_ms: 10, value: a },
                                     { line_id: 2, ts: 999, tick_ms: 20, value: b }] });
const frame = (sock, rows) => sock.onmessage({ data: JSON.stringify(rows) });
let sock = null;

function seedRoute(series) {
  const held = holdOn((u) => u.startsWith("/plot/series"), () => ok(series.shift()));
  const hold = route;
  route = (u, opt) => {
    if (u.startsWith("/lines?match=")) return ok({ lines: [] });
    if (u.startsWith("/lines")) return ok({ lines: [SESSION_ROW] });
    if (u.startsWith("/plot/channels")) return ok({ channels: [CHANNEL] });
    return hold(u, opt);
  };
  return held;
}

async function freshPage(series) {
  const held = seedRoute(series);
  state.maxId = 0;   // a first connect: the backfill seeds the charts
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await settle();
  assert.equal(held.length, 1, "the page-load seed is out");
  return held;
}

test("FW-8: a clear-all while the page-load seed is out leaves the charts empty", async () => {
  P.clearAllCharts();
  const held = await freshPage([points(1, 2)]);
  P.clearAllCharts();   // terminal.js clear-all
  D.clearAllDigital();
  held.splice(0)[0]();
  await settle();
  route = null;
  assert.equal(P.charts.size, 0, "the cleared history came back");
});

// The reset half needs no check of its own: a capture token arriving while the seed is out is
// staged until the backfill, seed included, has landed.
test("FW-8: a capture reset during the page-load seed waits for it, then plots the new capture", async () => {
  P.clearAllCharts();
  const held = await freshPage([points(1, 2), points(7, 8)]);
  frame(sock, [{ capture: "cap-a" }, { capture: "cap-b" }]);
  await settle();
  assert.equal(held.length, 1, "the reset ran while the old seed was still out");
  held[0]();
  await settle();
  assert.equal(held.length, 2, "the reset's own seed is out");
  held[1]();
  await settle();
  route = null;
  assert.deepEqual(P.charts.get("p1|adhoc")?.ys.get("v"), [7, 8]);
});
