// can.js openCanExport: the frame-history CSV has no port column, so it is one board's. Without
// `port` two boards' frames for one id interleaved with nothing saying which sent which. The
// dialog offers a Port choice once the table (or the attached set) names two, and the request
// always carries one; the shown window of a paused table is that port's.

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
C.initCan();

let nextId = 0;
function frame(raw, port) {
  state.maxId = ++nextId;
  C.canIngest({ id: nextId, ts: 1000 + nextId, port, chan: "event", raw });
}

const opt = (name) => {
  const host = env.byId("expOptions");
  return [...host.querySelectorAll("input"), ...host.querySelectorAll("select")]
    .find((el) => el.id === "expOpt_" + name);
};

async function exportWith(set = () => {}, mode = "expModeSession") {
  seen.lastUrl = null;
  env.byId("canExport").emit("click");
  set();
  env.byId(mode).emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl && seen.lastUrl.startsWith("/can/frames?"), `built ${seen.lastUrl}`);
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function reset(aliases = []) {
  C.setCanPaused(false);
  C.clearAllCan();
  state.knownAliases = aliases;
}

test("two boards in the table: a Port choice, and the chosen board is the one exported", async () => {
  reset(["p1", "p2"]);
  frame("!can 1 - 100 01", "p1");
  frame("!can 2 - 100 02", "p2");
  assert.equal((await exportWith()).get("port"), "p1", "the first board is the default");
  const q = await exportWith(() => {
    assert.deepEqual(opt("port").children.map((o) => o.value), ["p1", "p2"]);
    opt("port").value = "p2";
    opt("port").emit("change");
  });
  assert.equal(q.get("port"), "p2");
});

test("one board: no choice to make, but the request still names it", async () => {
  reset(["p1"]);
  frame("!can 1 - 100 01", "p1");
  const q = await exportWith(() => assert.equal(opt("port"), undefined, "a one-board table needs no Port choice"));
  assert.equal(q.get("port"), "p1");
});

test("an empty table exports from the attached board", async () => {
  reset(["b1"]);
  assert.equal((await exportWith()).get("port"), "b1");
});

test("the Port choice follows the Source, as the ids field does", () => {
  reset(["p1", "p2"]);
  frame("!can 1 - 100 01", "p1");
  env.byId("canExport").emit("click");
  assert.equal(opt("port").disabled, false);
  opt("format").value = "snapshot";
  opt("format").emit("change");
  assert.equal(opt("port").disabled, true, "the snapshot has a port column and ignores the choice");
  env.byId("expCancel").emit("click");
});

test("a paused table's shown window is the chosen board's", async () => {
  reset(["p1", "p2"]);
  frame("!can 1 - 100 01", "p1");   // id 1
  frame("!can 2 - 100 02", "p2");
  frame("!can 3 - 200 02", "p2");
  frame("!can 4 - 100 03", "p1");   // p1's newest frame for 100
  C.setCanPaused(true);
  const shown = (port) => exportWith(() => {
    opt("port").value = port;
    opt("port").emit("change");
  }, "expModeShown");
  const p1 = await shown("p1");
  assert.equal(p1.get("port"), "p1");
  assert.equal(p1.get("since_id"), String(nextId - 1), "p1's oldest shown row is its frame for 100");
  const p2 = await shown("p2");
  assert.equal(p2.get("since_id"), String(nextId - 3), "p2's window starts at its own oldest row");
  C.setCanPaused(false);
});

test("the ids field holds the chosen board's ids and follows the Port choice until edited", async () => {
  reset(["p1", "p2"]);
  frame("!can 1 - 100 01", "p1");
  frame("!can 2 - 200 02", "p2");
  env.byId("canExport").emit("click");
  assert.equal(opt("ids").value, "100", "p2's id would select nothing in p1's export");
  opt("port").value = "p2";
  opt("port").emit("change");
  assert.equal(opt("ids").value, "200");
  opt("ids").value = "7DF";
  opt("ids").emit("input");
  opt("port").value = "p1";
  opt("port").emit("change");
  assert.equal(opt("ids").value, "7DF", "an edited field is the user's, not a default");
  env.byId("expCancel").emit("click");
  const q = await exportWith(() => { opt("port").value = "p2"; opt("port").emit("change"); });
  assert.equal(q.get("id"), "200", "the export carries the ids the field shows");
});
