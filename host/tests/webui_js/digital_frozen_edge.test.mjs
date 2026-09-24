// digital.js: a paused lane panel's shown window ends at its frozen edge, and a lane colour
// applies to every port's lane of that name (F-26).

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
const C = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();

let nextId = 0;
function row(raw, ts, port = "p1") {
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
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

function resetAll() {
  F.pauseAll(false);
  P.clearAllCharts();
  D.clearAllDigital();
  C.clearAllCan();
  TW.setZoom(null);
  state.timeMode = "host";
}

test("a paused lane panel's shown window ends at its frozen edge", async () => {
  resetAll();
  row("!pd 2 f:u1:/b0", 170);
  row("!ps 2 00000000 01", 170);   // outside the 30 s ending at 209
  const first = nextId + 1;
  for (let i = 0; i < 10; i++) row(`!ps 2 ${(i + 1).toString(16)} 0${i % 2}`, 200 + i);
  const last = nextId;
  row("!m later", 500);
  D.setDigitalPaused(true);
  row("!ps 2 0000000B 01", 600);   // after the freeze: must not move the edge
  const q = await exportShown(D.exportDigital);
  assert.equal(q.get("id_to"), String(last), "the last sample before the freeze, not the later line");
  assert.equal(q.get("since_id"), String(first - 1), "the panel's own window, 30 s by default");
  assert.equal(q.has("since_ts") || q.has("until_ts"), false);
  D.setDigitalPaused(false);
});

// ---- F-26 --------------------------------------------------------------------------

test("a lane colour applies to every port's lane of that name, and to no other name", () => {
  resetAll();
  row("!pd 4 f:u1:/b0", 400, "a");
  row("!ps 4 00000001 01", 400, "a");
  row("!pd 4 f:u1:/b0,b1", 400, "b");
  row("!ps 4 00000001 01", 400, "b");
  const [a0, b0, b1] = ["a|b0", "b|b0", "b|b1"].map((k) => D.digitalLanes.get(k));
  a0.swEl.onclick({});
  const picker = env.body.children.filter((c) => c.type === "color").at(-1);
  assert.ok(picker, "the swatch must open a colour input");
  picker.value = "#123456";
  picker.onchange();
  assert.equal(a0.color, "#123456");
  assert.equal(b0.color, "#123456", "the other port's lane of the same name follows the colour");
  assert.notEqual(b1.color, "#123456", "a lane of another name does not");
});
