// Two ways a panel's export button produces nothing, both driven here against the real
// dialog and the URL it builds.
//
// W12: with no channel or lane shown, exportChart/exportDigital returned silently, so an
// enabled button did nothing and said nothing (REVIEW class 12). The digital side had no pin
// at all - mutation M27, "a digital panel with nothing shown still opens the dialog", survived.
//
// W5: `changes only` and `decode values` are two independent checkboxes, and the daemon
// answers `400 changes requires decode` (SPEC 9.2). The combination is one click away from
// the default, and what came back was a refusal, not an export.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();

// The double refuses what the daemon refuses and records the URL by whichever road it left
// on (a fetch, or the `<a download>` navigation state.js uses when no token is set), so a
// `changes` without `decode` fails here the way it fails on the wire.
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const { charts, plotIngest, exportChart } = await import(webuiUrl("plots.js"));
const { digitalLanes, exportDigital, buildDigitalHead } = await import(webuiUrl("digital.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
buildDigitalHead();

let nextId = 0;
function ingest(raw) {
  const row = { id: ++nextId, ts: 1000 + nextId * 0.01, port: "p1", chan: "event", raw };
  state.maxId = row.id;
  plotIngest(row);
}

ingest("!pd 0 a:u2 b:u2");
ingest("!ps 0 100 0001,0002");
ingest("!pd 1 f:u1:/b0,b1");
ingest("!ps 1 100 03");

const chart = charts.get("s0");
const digitalBtn = () => env.byId("digitalHead").querySelector(".exportbtn");
const opt = (name) => env.byId("expOptions").querySelector("#expOpt_" + name);

// Open a panel's dialog and press Export; returns the query string, or null if no request
// was made (the dialog never opened).
async function pressExport(open) {
  seen.lastUrl = null;
  const dlg = env.byId("exportDlg");
  dlg.close();
  open();
  if (!dlg.hasAttribute("open")) return null;   // the panel refused to open the dialog at all
  env.byId("expGo").emit("click");
  await tick();
  return seen.lastUrl ? new URLSearchParams(seen.lastUrl.split("?")[1]) : null;
}

test("a chart with no channel shown disables its export button and says why", () => {
  assert.equal(chart.exportBtn.disabled, false, "two channels are shown at the start");
  for (const name of chart.names) chart.chansEl.children[chart.names.indexOf(name)]
    .emit("click", { preventDefault() {} });
  assert.deepEqual(chart.names.map((n) => chart.show.get(n)), [false, false]);
  assert.equal(chart.exportBtn.disabled, true,
    "an enabled button that does nothing at all is a control that lies about itself");
  assert.match(chart.exportBtn.title, /Nothing is shown/);
});

test("and the click that gets through anyway exports nothing", async () => {
  // The DOM stub does not honour `disabled`, which is exactly the belt the guard is:
  // no names means no request, rather than /plot/export?names= .
  assert.equal(await pressExport(() => exportChart(chart)), null);
});

test("showing a channel again re-enables the button", () => {
  chart.chansEl.children[0].emit("click", { preventDefault() {} });
  assert.equal(chart.exportBtn.disabled, false);
  assert.match(chart.exportBtn.title, /Export the shown channels/);
});

test("a digital panel with no lane shown disables its export button", async () => {
  const btn = digitalBtn();
  assert.equal(btn.disabled, false, "the bits stream built two shown lanes");
  const lanes = [...digitalLanes.values()];
  for (const l of lanes) l.nameEl.onclick({});
  assert.equal(btn.disabled, true, "this is the side mutation M27 walked straight through");
  assert.match(btn.title, /No lanes are shown/);
  assert.equal(await pressExport(exportDigital), null);

  lanes[0].nameEl.onclick({});
  assert.equal(btn.disabled, false);
  assert.match(btn.title, /Export the shown lanes/);
});

test("changes only always carries decode, whatever the decode box says", async () => {
  for (const [open, what] of [[() => exportChart(chart), "chart"], [exportDigital, "digital"]]) {
    seen.lastUrl = null;
    open();
    opt("decode").checked = false;
    opt("decode").emit("change");
    opt("changes").checked = true;
    opt("changes").emit("change");
    env.byId("expGo").emit("click");
    await tick();
    const q = new URLSearchParams(seen.lastUrl.split("?")[1]);
    assert.equal(q.get("changes"), "1", what);
    assert.equal(q.get("decode"), "1",
      `${what}: changes=1 without decode=1 is 400 "changes requires decode", not an export`);
  }
});

test("changes off still lets decode be turned off", async () => {
  seen.lastUrl = null;
  exportChart(chart);
  opt("decode").checked = false;
  opt("decode").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  const q = new URLSearchParams(seen.lastUrl.split("?")[1]);
  assert.equal(q.has("decode"), false, "the forced decode must belong to changes, not to every export");
  assert.equal(q.has("changes"), false);
});

test("the changes box follows the decode box in the dialog", () => {
  exportChart(chart);
  opt("decode").checked = false;
  opt("decode").emit("change");
  assert.equal(opt("changes").disabled, true,
    "the dependency the daemon enforces must be visible before the request, not after it");
  opt("decode").checked = true;
  opt("decode").emit("change");
  assert.equal(opt("changes").disabled, false);
});

test("nothing this panel built would be refused by the daemon", () => {
  // The forced decode is only worth anything if the URL it produces is one the daemon
  // answers: the double applies server.py's own guards, so a 400 lands here.
  assert.ok(seen.lastUrl, "the suite must have built at least one export URL");
  assert.deepEqual(seen.refusals, []);
});
