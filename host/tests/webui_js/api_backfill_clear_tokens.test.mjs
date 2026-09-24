// api.js runBackfill: what a clear clicked while the backfill is out may and may not take
// with it (PD-1, PD-2), and the generation tokens a capture reset moves (PD-6).
// Real createPane, real clear buttons, the definition row inside the backfill window.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl, tick } from "./dom_stub.mjs";
import { ALL_CHANS } from "../../mcuscope/webui/pane.js";

const env = installDom();
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

// The window: a plain line (its oldest row, what seedPlotDefs is anchored to), the board's
// !pd announcement INSIDE the window, and one sample of that stream. Each test takes its own
// port: plotDefs is cached for the life of the module and no clear drops it (that is what
// PD-1 is about), so a definition an earlier test taught it would answer for this one.
const windowRows = (port) => [
  makeRow(12, { port, chan: "event", raw: "!ps 0 000003E8 0064" }),
  makeRow(11, { port, chan: "event", raw: "!pd 0 v:u2" }),
  makeRow(10, { port }),
];
let BACKFILL = windowRows("p1");
let release = null;
const defQueries = [];
globalThis.fetch = async (url) => {
  const u = String(url);
  const ok = (b) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => b });
  if (u.startsWith("/lines?order=desc&limit=200")) {
    await new Promise((r) => { release = r; });
    return ok({ lines: BACKFILL, truncated: false });
  }
  // seedPlotDefs looks BELOW the window's oldest row and finds nothing: the definition it
  // needs is one of the rows above, delivered by the backfill itself.
  if (u.includes("match=%5E!pd")) { defQueries.push(u); return ok({ lines: [], truncated: false }); }
  return ok({ lines: [], channels: [], truncated: false });
};

env.localStorage.setItem("termState", JSON.stringify({ timeMode: "host", panes: [
  { port: "all", channels: ALL_CHANS, regex: "" },
] }));

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const { initCan, canClearGen } = await import(webuiUrl("can.js"));
const { charts, clearAllCharts, plotSeedGen } = await import(webuiUrl("plots.js"));
const { connectWs } = await import(webuiUrl("api.js"));
T.initTerminal();
initCan();

// A fresh page with the backfill in flight: every pane holding nothing, cleared or not.
async function backfillOut(port = "p1") {
  BACKFILL = windowRows(port);
  state.maxId = 0;
  buffer.length = 0;
  clearAllCharts();
  for (const p of T.panes) { p.clearId = 0; p.rows = []; }
  release = null;
  defQueries.length = 0;
  connectWs();
  env.sockets.at(-1).onopen();
  for (let i = 0; i < 5 && !release; i++) await tick(0);
  assert.ok(release, "the backfill never went out");
}
async function land() { release(); await tick(30); }
function live(raw, id, ts, port = "p1") {
  env.sockets.at(-1).onmessage({ data: JSON.stringify([{ id, ts, port, chan: "event", raw }]) });
}
const ids = (p) => p.rows.map((r) => r.id);

// ---- PD-1 ---------------------------------------------------------------------------------

test("PD-1 control: with no clear, the window's own !pd decodes the live samples after it", async () => {
  await backfillOut();
  await land();
  assert.ok(defQueries.length, "seedPlotDefs must have run, and found nothing below the window");
  assert.equal(charts.size, 1, "the backfill's own sample built the chart");
  live("!ps 0 000007D0 00C8", 20, 2000);
  assert.equal(charts.get("p1|s0").ys.get("v").length, 2, "the live sample must decode");
});

test("PD-1: clear-all during the backfill keeps the definitions it carried, so later !ps decode",
  async () => {
    await backfillOut("p2");
    env.byId("clearAllBtn").emit("click");
    await land();
    assert.equal(charts.size, 0, "the clear must still hold for the backfill's own samples");
    live("!ps 0 000007D0 00C8", 21, 2001, "p2");
    live("!ps 0 00000BB8 00C9", 22, 2002, "p2");
    assert.equal(charts.size, 1,
      "the stream's definition went with the cleared rows: every later !ps is undecodable");
    assert.equal(charts.get("p2|s0").ys.get("v").length, 2, "both live samples charted");
  });

// ---- PD-2 ---------------------------------------------------------------------------------

test("PD-2: a pane born while the backfill was out and then cleared does not refill", async () => {
  await backfillOut();
  env.byId("addPaneBtn").emit("click");
  env.byId("addPaneBtn").emit("click");
  const [cleared, untouched] = T.panes.slice(-2);
  cleared.el.querySelector(".clear").emit("click");
  await land();
  assert.deepEqual(ids(cleared), [], "the clear on a pane born mid-backfill was lost");
  assert.deepEqual(ids(untouched), [10, 11, 12],
    "positive control: a pane born mid-backfill and not cleared takes the rows");
});

// ---- PD-6 ---------------------------------------------------------------------------------

test("PD-6: a capture reset moves every pane's clear token, as it moves the CAN and chart ones",
  async () => {
    await backfillOut();
    await land();
    env.byId("addPaneBtn").emit("click");
    const before = { panes: T.panes.map((p) => p.clearGen), can: canClearGen(), plot: plotSeedGen() };
    // Two capture identities: the first is the page's own, only a change is a reset.
    env.sockets.at(-1).onmessage({ data: JSON.stringify([{ capture: "cap-a" }]) });
    assert.deepEqual(T.panes.map((p) => p.clearGen), before.panes,
      "positive control: the first capture id seen is not a reset");
    env.sockets.at(-1).onmessage({ data: JSON.stringify([{ capture: "cap-b" }]) });
    await tick(0);
    for (const [i, p] of T.panes.entries()) {
      assert.notEqual(p.clearGen, before.panes[i], `pane ${i}: a reset left its clear token behind`);
    }
    assert.notEqual(canClearGen(), before.can);
    assert.notEqual(plotSeedGen(), before.plot);
    release = null;   // the re-seed backfill is out; nothing else here waits on it
  });
