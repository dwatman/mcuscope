// exportdlg.js: a refused plot export lands inline beside the option that caused it, and the
// dialog stays open to fix it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

let nextId = 0;
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
}
const hex = (n) => n.toString(16).padStart(8, "0");

// The dialog's option fields, as exportdlg.js ids them.
const opt = (name) => {
  const host = env.byId("expOptions");
  return [...host.querySelectorAll("input"), ...host.querySelectorAll("select")]
    .find((el) => el.id === "expOpt_" + name);
};

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  TW.setZoom(null);
  state.timeMode = "host";
}

// 50 samples, 100 ms apart on the host clock from 100 s, while the MCU clock runs at half that
// rate (50 ms a sample from tick 1000), so a tick edge mapped as if it were host time is wrong.
// `v` is analog (a chart), `b0` toggles every sample (a lane with a vertex per sample).
// Returns sample i's line id.
function stream() {
  row("!pd 0 v:u2 f:u1:/b0", 99);
  for (let i = 0; i < 50; i++) row(`!ps 0 ${hex(1000 + i * 50)} 0001,0${i % 2}`, 100 + i * 0.1);
  const last = nextId;
  return (i) => last - 49 + i;
}

// The positive control for the empty `seen.refusals` the shown-window export tests assert: a
// double that had stopped checking would leave it empty too. The deadband field is free text,
// so a mistyped channel name is the refusal a user reaches from here, and it must arrive
// beside the field that produced it.
test("a deadband naming no exported channel is refused, and the dialog says so", async () => {
  resetAll();
  stream();
  P.exportChart(P.charts.get("p1|s0"));
  opt("changes").checked = true;
  opt("changes").emit("change");
  opt("deadband").value = "nosuch=0.5";
  opt("deadband").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.deepEqual(seen.refusals.map(([, why]) => why),
    ["deadband names no exported channel: nosuch=0.5"],
    "the guard the assertions above rely on never fired");
  assert.equal(env.byId("expErr").textContent,
    "plot export failed: deadband names no exported channel: nosuch=0.5",
    "the refusal must land beside the option that caused it, not in a toast over a closed dialog");
  assert.equal(env.byId("exportDlg").hasAttribute("open"), true,
    "a refused export must leave the dialog open to fix");
  seen.refusals.length = 0;   // every assertion below is about what the panels build
  env.byId("expCancel").emit("click");
});
