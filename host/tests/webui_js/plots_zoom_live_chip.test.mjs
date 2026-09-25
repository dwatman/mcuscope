// chrome.js window selectors under a standing drag zoom (SPEC 9.2): a selector shows the zoom
// chip only while its own surface is frozen on the zoom. A chart born live after one surface
// resumed by hand follows its own tail (F-23), and its selector showed the chip anyway, with
// no span lit, while the chart drew its span.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const { state } = await import(webuiUrl("state.js"));
const TW = await import(webuiUrl("timewindow.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const C = await import(webuiUrl("can.js"));

let nextId = 0;
function row(raw, ts) {
  state.maxId = ++nextId;
  P.plotIngest({ id: nextId, ts, port: "p1", chan: "event", raw });
}
// A selector's three spans and its chip, as the user sees them.
const look = (win) => ({
  chip: !win.children[3].hidden,
  lit: win.children.map((b) => b.getAttribute("aria-checked") === "true"),
});

test("a chart born live under a standing zoom lights its own span; paused, it shows the chip", () => {
  C.canIngest({ id: ++nextId, ts: 1, port: "p1", chan: "event", raw: "!can 1 - 100 AA" });
  row("!pd 0 v:u2", 10);
  for (let i = 0; i < 20; i++) row(`!ps 0 ${(i + 1).toString(16)} 0001`, 10 + i * 0.1);
  const a = P.charts.get("p1|s0");
  D.buildDigitalHead();
  const lanesWin = env.byId("digitalHead").children.find((el) => el.className === "plot-ctl").children[0];
  P.onSelect(a, { select: { left: 0, width: 10 }, posToVal: (px) => 10 + px / 100, setSelect() {} });
  assert.ok(TW.getZoom());
  assert.deepEqual(look(a.winEl), { chip: true, lit: [false, false, false, true] },
    "positive control: a chart frozen on the zoom shows the chip");
  assert.equal(look(lanesWin).chip, true, "positive control: the paused lanes show it too");

  C.setCanPaused(false);   // one surface resumed by hand: the next chart is born live
  row("!pd 1 w:u2", 20);
  for (let i = 0; i < 20; i++) row(`!ps 1 ${(i + 1).toString(16)} 0002`, 20 + i * 0.1);
  const b = P.charts.get("p1|s1");
  assert.equal(b.paused, false);
  assert.deepEqual(look(b.winEl), { chip: false, lit: [false, true, false, false] },
    "the live chart's selector must name the 30 s span it draws, not the zoom");

  P.setChartPaused(b, true);   // now frozen, it draws the zoom
  assert.deepEqual(look(b.winEl), { chip: true, lit: [false, false, false, true] });
});
