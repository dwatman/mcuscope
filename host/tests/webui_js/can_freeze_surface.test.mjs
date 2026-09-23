// The CAN table is the fourth pause-all surface (P12): before this it was the one live panel
// "pause all" left running, and its export dialog had no frozen window to offer.
//
// freeze.test.mjs covers the registry itself; what is driven here is can.js's own membership -
// that pause-all reaches it, that it does not hold the label on its own, and that its export
// is bounded by the line id it froze at.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const { anyLive, pauseAll } = await import(webuiUrl("freeze.js"));
const { canIngest, renderCan, clearAllCan, initCan, setCanPaused } = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
initCan();

// The id_to of the frame-history export the table's Export button issues, or null for none.
async function exportIdTo() {
  seen.lastUrl = null;
  env.byId("expTitle").textContent = "";
  env.byId("canExport").emit("click");
  assert.notEqual(env.byId("expTitle").textContent, "", "the export dialog did not open");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]).get("id_to");
}

let nextId = 0;
function ingest(raw) {
  state.maxId = ++nextId;
  canIngest({ id: nextId, ts: 1000 + nextId, port: "p1", chan: "event", raw });
}

test("an empty table is not live, so it cannot hold pause-all in the paused state", async () => {
  clearAllCan();
  assert.equal(anyLive(), false, "nothing has any rows yet");
  assert.equal(await exportIdTo(), null, "a live table exports to the live edge");
});

test("pause all reaches the table, and resuming it alone makes the button live again", async () => {
  ingest("!can 1 - 100 DE");
  renderCan();
  assert.equal(anyLive(), true, "a table with rows is a live surface");

  pauseAll(true);
  assert.equal(anyLive(), false, "the fourth surface must stop with the other three");
  const frozenAt = state.maxId;
  assert.equal(await exportIdTo(), String(frozenAt), "and export up to the id it froze at");

  ingest("!can 2 - 100 FF");
  assert.equal(await exportIdTo(), String(frozenAt), "which does not move while it stays frozen");

  pauseAll(false);
  assert.equal(anyLive(), true);
  assert.equal(await exportIdTo(), null, "resuming clears the bound, so exports run to the live edge");
});

test("the pause button and the paused tag follow the state, whoever set it", () => {
  clearAllCan();
  ingest("!can 1 - 100 DE");   // an empty table is not a surface pause-all can reach
  renderCan();
  pauseAll(true);
  assert.equal(env.byId("canPause").textContent, "resume");
  assert.equal(env.byId("canPause").classList.contains("on"), true);
  assert.equal(env.byId("canPausedTag").hidden, false);

  env.byId("canPause").emit("click");           // the panel's own control resumes it
  assert.equal(env.byId("canPause").textContent, "pause");
  assert.equal(env.byId("canPausedTag").hidden, true);
  assert.equal(anyLive(), true, "and one surface running again ends the pause-all latch");
  setCanPaused(false);
  clearAllCan();
});
