// digital.js: paused lanes in tick mode export the samples inside their tick window (FW-2).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const F = await import(webuiUrl("freeze.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

let nextId = 0;
function ingest(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  const r = { id: nextId, ts, port, chan: "event", raw };
  P.plotIngest(r);
  return r;
}

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

// ---- FW-2: tick mode's shown window is the samples drawn -----------------------------------

// The daemon's selection over `rows`: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to,
// each bound only when given.
function selected(rows, q) {
  const bound = (k, dflt) => (q.has(k) ? Number(q.get(k)) : dflt);
  const s = bound("since_ts", -Infinity), u = bound("until_ts", Infinity);
  const sinceId = bound("since_id", -Infinity), idTo = bound("id_to", Infinity);
  return rows.filter((r) => r.ts > s && r.ts <= u && r.id > sinceId && r.id <= idTo).map((r) => r.id);
}

// 100 Hz by the MCU clock, which runs 1 percent slow against host time (an STM32 on HSI), plus
// any latency `lag(i)` adds.
function hsiStream(sid, n, value, lag = () => 0) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const tick = 10000 + i * 10;
    rows.push({ ...ingest(`!ps ${sid} ${tick.toString(16)} ${value(i)}`, 500 + i * 0.0101 + lag(i)), tick });
  }
  return rows;
}

// b0 toggles every 7 samples; b1 only 99 samples either side of the window's left edge, with a
// 1 s latency step between each of b1's vertices and the edge: only b0's vertices bracket it
// tightly enough to read the host time there.
test("FW-2: paused lanes in tick mode export the samples inside their tick window", async () => {
  F.pauseAll(false);
  D.clearAllDigital();
  ingest("!pd 2 f:u1:/b0,b1", 1);
  const rows = hsiStream(2, 4000, (i) => "0" + ((Math.floor(i / 7) % 2) | (i >= 900 && i < 1100 ? 2 : 0)),
                         (i) => (i >= 950 ? 1 : 0) + (i >= 1050 ? 1 : 0));
  D.setDigitalPaused(true);
  state.timeMode = "tick";
  const q = await exportShown(D.exportDigital);
  state.timeMode = "host";
  D.setDigitalPaused(false);
  const lastTick = rows.at(-1).tick;
  const drawn = rows.filter((r) => r.tick >= lastTick - 30000).map((r) => r.id);
  assert.deepEqual(selected(rows, q), drawn);
});

test("FW-2: lanes shorter than their tick window export from their first sample", async () => {
  F.pauseAll(false);
  D.clearAllDigital();
  ingest("!pd 3 f:u1:/b0", 1);
  const rows = hsiStream(3, 100, (i) => "0" + (Math.floor(i / 7) % 2));
  D.setDigitalPaused(true);
  state.timeMode = "tick";
  const q = await exportShown(D.exportDigital);
  state.timeMode = "host";
  D.setDigitalPaused(false);
  assert.equal(q.get("since_id"), String(rows[0].id - 1));
  assert.deepEqual(selected([{ id: 1, ts: 1 }, ...rows], q), rows.map((r) => r.id), "the `!pd` line is not a sample");
});
