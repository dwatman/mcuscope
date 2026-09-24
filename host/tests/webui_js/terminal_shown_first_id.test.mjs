// terminal.js: a paused pane's shown window sends its first row's id, exclusive (FW-4).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick, makePane, makeRow } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { exportPane } = await import(webuiUrl("terminal.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

// ---- FW-4: the pane's first row id ---------------------------------------------------------

test("FW-4: a paused pane's shown window sends its first row's id, exclusive", async () => {
  const pane = makePane({ port: "p1", autoscroll: false, frozenId: 77 });
  // One burst stamped once: the rows before id 40 share its ts but were cleared from the pane.
  pane.rows = [makeRow(40, { ts: 1000 }), { chan: "gap", ts: 999, raw: "gap" }, makeRow(41, { ts: 1000 }),
               makeRow(45, { ts: 1002.5 })];
  const q = await exportShown(() => exportPane(pane));
  assert.equal(q.get("since_id"), "39");
  assert.equal(q.get("since_ts"), String(1000 - 1e-6));
  assert.equal(q.get("until_ts"), "1002.5");
  env.byId("expModeSession").emit("change");
  assert.deepEqual(seen.refusals, []);

  seen.lastUrl = null;
  exportPane(pane);
  env.byId("expModeClock").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.equal(new URLSearchParams(seen.lastUrl.split("?")[1]).has("since_id"), false,
    "the first row bounds the shown window only");
  env.byId("expModeSession").emit("change");
});
