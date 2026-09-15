// A first connect whose only `!pd` sits inside the 200-row backfill: plotIngest caches it after
// the history seed has run, so the seed must read the field order from the backfill rows it is
// handed (api.js runBackfill -> seedPlotHistory -> plots.js plotSeed).

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl, tick } from "./dom_stub.mjs";

const env = installDom();
const tpl = readFileSync(new URL(webuiUrl("index.html")), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]); el.className = m[2]; tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

const { ALL_CHANS } = await import(webuiUrl("pane.js"));
const pdRow = makeRow(3, { chan: "event", raw: "!pd 0 zt:u2 za:u2" });
const stored = [makeRow(1), makeRow(2), pdRow, makeRow(4), makeRow(5)];
const asked = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  asked.push(u);
  const q = new URLSearchParams(u.slice(u.indexOf("?") + 1));
  let body = {};
  if (u.startsWith("/lines?match=")) {
    body = { lines: [pdRow].filter((r) => r.id <= Number(q.get("id_to"))), truncated: false };
  } else if (u.startsWith("/lines")) body = { lines: stored.slice().reverse(), truncated: false };
  else if (u.startsWith("/plot/channels")) {
    body = { channels: ["za", "zt"].map((name) => ({ name, port: "p1", sid: "0", type: "u2", kind: "analog", last_ts: 1000 })) };
  } else if (u.startsWith("/plot/series")) body = { points: [{ line_id: 4, ts: 1000.004, tick_ms: 10, value: 1 }] };
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};
env.localStorage.setItem("termState", JSON.stringify({ timeMode: "host", panes: [{ port: "all", channels: ALL_CHANS, regex: "" }] }));
await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const { initCan } = await import(webuiUrl("can.js"));
const { charts } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));
T.initTerminal();
initCan();

test("a `!pd` inside the first connect's backfill orders the seeded chips", async () => {
  connectWs();
  env.sockets.at(-1).onopen();
  for (let i = 0; i < 20; i++) await tick(0);
  await tick(40);
  assert.ok(asked.some((u) => u.startsWith("/lines?match=")), "the definition lookback ran");
  assert.ok(asked.some((u) => u.startsWith("/plot/series")), "the seed ran");
  const chart = charts.get("p1|s0");
  assert.ok(chart, "the seed built the chart");
  assert.deepEqual(chart.names, ["zt", "za"], "the `!pd` order, not /plot/channels' name order");
});
