// digital.js, owner rulings D-2 and D-9: the lanes' "shown window" export while a drag zoom
// stands exports the zoom range (D-2); a digital panel paused before its first lane stays empty
// and keeps its pause-time watermark (D-9).

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
D.initDigitalCursorSync();

let nextId = 0;
function row(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port, chan: "event", raw });
}
const hex = (n) => n.toString(16).padStart(8, "0");

// The id_to of a whole-range export, which carries the panel's freeze bound in every mode.
async function exportIdTo(open) {
  seen.lastUrl = null;
  env.byId("expTitle").textContent = "";
  open();
  assert.notEqual(env.byId("expTitle").textContent, "", "the export dialog did not open");
  env.byId("expModeSession").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]).get("id_to");
}

async function exportShown(open) {
  seen.lastUrl = null;
  open();
  assert.equal(env.byId("expModeShown").disabled, false, "the shown window was not offered");
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function shownOffered(open) {
  open();
  const ok = !env.byId("expModeShown").disabled;
  env.byId("expCancel").emit("click");
  return ok;
}

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

// The real drag: onSelect with a uPlot whose pixels are x values.
function drag(min, max) {
  P.onSelect(P.charts.get("p1|s0"), { select: { left: min, width: max - min }, posToVal: (px) => px,
                                      setSelect() {} });
  assert.ok(TW.getZoom(), "the drag must leave a zoom standing");
}

test("tick base: the lanes export the ids of the first and last samples inside the zoom", async () => {
  resetAll();
  const id = stream();
  state.timeMode = "tick";
  drag(1210, 1590);   // i = 5 (tick 1250) to i = 11 (tick 1550); 1600 is past the edge
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(id(5) - 1));
  assert.equal(q.get("id_to"), String(id(11)));
  assert.equal(q.has("since_ts") || q.has("until_ts"), false);
  state.timeMode = "host";
});

test("tick base: a lanes zoom dragged past the frozen edge ends at the edge's sample", async () => {
  resetAll();
  const id = stream();   // edge: tick 3450
  state.timeMode = "tick";
  drag(3300, 3600);
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("since_id"), String(id(46) - 1));
  assert.equal(q.get("id_to"), String(id(49)));
  state.timeMode = "host";
});

// ---- D-9 ---------------------------------------------------------------------------

test("a panel paused before its first lane stays empty and keeps its pause-time watermark", async () => {
  resetAll();
  state.maxId = nextId = 100;
  F.pauseAll(true);
  nextId = 499;
  row("!pd 3 e:u1:=0=OFF,1=ON", 200);
  row("!ps 3 00000001 01", 201);
  row("!ps 3 00000002 00", 202);
  const lane = D.digitalLanes.get("p1|e");
  assert.equal(await exportIdTo(D.exportDigital), "100", "the watermark jumped to a line after the pause");
  assert.deepEqual(D.laneDrawData(lane).vs, [], "a vertex from after the pause is on the frozen view");
  assert.equal(D.digitalRightEdge(), null, "the ruler would move under a paused panel");
  assert.equal(D.isDigitalPaused(), true);
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  D.redrawDigital();
  D.refreshDigitalReadouts();
  assert.equal(lane.valEl.textContent, "", "the readout shows a post-pause value");
  assert.equal(shownOffered(D.exportDigital), false, "an empty panel has no shown window");
  F.pauseAll(false);
  assert.deepEqual(D.laneDrawData(lane).vs, [1, 0], "resume shows what arrived meanwhile");
  assert.equal(D.digitalRightEdge(), 202);
});

test("clear-all while paused, then a sample: still empty, still paused, watermark at the clear", async () => {
  resetAll();
  stream();
  F.pauseAll(true);
  state.maxId = nextId = 900;
  D.clearAllDigital();
  row(`!ps 0 ${hex(9000)} 0001,01`, 300);
  assert.equal(await exportIdTo(D.exportDigital), "900");
  assert.deepEqual(D.laneDrawData(D.digitalLanes.get("p1|b0")).vs, []);
  assert.equal(D.digitalRightEdge(), null);
  F.pauseAll(false);
});
