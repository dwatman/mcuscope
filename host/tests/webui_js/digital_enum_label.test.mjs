// digital.js enumLabel: `!pd 0 st:u1:=0=OFF,0=IDLE,1=RUN` lists 0 twice (SPEC 2.5 does not
// forbid it). The CLI and the export build a dict from the pairs, so the last label wins there;
// the lane took the first, and read OFF where `mcu tail --decode` read IDLE.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };
const { plotIngest } = await import(webuiUrl("plots.js"));
const dg = await import(webuiUrl("digital.js"));

test("a value listed twice reads as its last label, as the CLI and export read it", () => {
  dg.clearAllDigital();
  plotIngest({ id: 1, ts: 1000, port: "p1", chan: "event", raw: "!pd 0 st:u1:=0=OFF,0=IDLE,1=RUN" });
  plotIngest({ id: 2, ts: 1001, port: "p1", chan: "event", raw: "!ps 0 3E8 00" });
  const lane = dg.digitalLanes.get("p1|st");
  lane.canvas.clientWidth = 200;
  dg.redrawDigital();
  assert.equal(lane.valEl.textContent, "IDLE");
  plotIngest({ id: 3, ts: 1002, port: "p1", chan: "event", raw: "!ps 0 3E9 01" });
  dg.redrawDigital();
  assert.equal(lane.valEl.textContent, "RUN", "a value listed once is unaffected");
});
