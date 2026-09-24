// can.js: a filtered paused table's shown window starts at the oldest row it shows (FW-3).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const C = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
env.byId("sidebar").setAttribute("data-view", "both");
C.initCan();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

let nextId = 0;

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

// ---- FW-3: the CAN filter ------------------------------------------------------------------

test("FW-3: a filtered paused table's shown window starts at the oldest row it shows", async () => {
  C.setCanPaused(false);
  C.setCanFilter("");
  C.clearAllCan();
  const frame = (raw, ts) => { state.maxId = ++nextId; C.canIngest({ id: nextId, ts, port: "p1", chan: "event", raw }); };
  frame("!can 1 - 7DF 01", 1000);   // an hour before, hidden by the filter
  let last = 0;
  for (let i = 0; i < 50; i++) frame(`!can ${i} - 100 AA`, (last = 4600 + i * 0.1));
  C.setCanFilter("100");
  C.setCanPaused(true);
  const q = await exportShown(() => env.byId("canExport").emit("click"));
  C.setCanPaused(false);
  C.setCanFilter("");
  assert.equal(q.get("id"), "100");
  assert.equal(q.get("since_id"), String(nextId - 1), "the window reached back to the hidden 7DF frame");
  assert.equal(q.has("since_ts"), false);
});
