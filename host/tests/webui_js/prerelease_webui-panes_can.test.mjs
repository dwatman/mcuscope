// Pre-release round 2026-09-15, CAN table: the pause-all label on the table's own create and
// destroy (D-4), the age clock after a page load onto a silent board (D-5), the id-click
// pattern against the decoder (D-6), an RTR-polled id's byte diff (D-7), a collapse while
// paused (D-8), and the export window and age branches nothing pinned (D-2, F-13 to F-16).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

const { state } = await import(webuiUrl("state.js"));
const F = await import(webuiUrl("freeze.js"));
const C = await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
env.byId("sidebar").setAttribute("data-view", "both");
C.initCan();

let label = null;
F.onFreezeChanged(() => { label = F.pauseAllLabel(); });

let nextId = 0;
function frame(raw, ts, port = "p1") {
  state.maxId = ++nextId;
  C.canIngest({ id: nextId, ts, port, chan: "event", raw });
}

const wrap = () => env.byId("canWrap");
const dataRows = () => wrap().querySelectorAll("tr")
  .filter((t) => !t.className.includes("bus-hdr") && t.querySelectorAll("td").length > 1);
const cellsOf = (tr) => tr.children.map((td) => td.textContent);

function reset() {
  C.setCanPaused(false);
  C.setCanFilter("");
  C.clearAllCan();
  env.store.clear();
}

// ---- D-4 ---------------------------------------------------------------------------

test("the table's first row and its clear both re-render the pause-all label", () => {
  reset();
  label = null;
  frame("!can 1 - 100 AA", 10);
  assert.equal(label, "pause all", "a live table born with its first frame must relabel the button");
  label = null;
  frame("!can 2 - 100 AB", 10.1);
  assert.equal(label, null, "later frames of a known id do not re-render the label");
  C.clearAllCan();
  assert.equal(label, "resume all", "clearing the last live surface must relabel the button");
});

// ---- D-5 ---------------------------------------------------------------------------

function tenHzBackfill(t0) {
  for (let i = 0; i < 20; i++) frame(`!can ${i} - 100 0${i % 10}`, t0 + i * 0.1);
}

const ageCell = () => dataRows()[0].children[4];

test("a daemon clock reading ages a board silent since before the page loaded", () => {
  reset();
  const t0 = 50000;
  tenHzBackfill(t0);
  C.noteDaemonNow(t0 + 600);
  C.renderCan();
  assert.equal(ageCell().className, "age-dead", "a 10 Hz id silent for 600 s is dead");
  assert.match(ageCell().textContent, /^9m5[89]s$/);
});

test("with no daemon clock (an older daemon) the newest row stays the anchor", () => {
  reset();
  C.noteDaemonNow(undefined);   // before any row: nothing to compare the reading against
  C.noteDaemonNow(Number.NaN);
  tenHzBackfill(60000);
  C.noteDaemonNow(undefined);
  C.renderCan();
  assert.equal(ageCell().className, "age-fresh");
  assert.match(ageCell().textContent, /^\d+ms$/, "a missing reading must not become the anchor");
});

test("a daemon reading older than the rows already seen does not pull the clock back", () => {
  reset();
  tenHzBackfill(70000);
  C.noteDaemonNow(70000 - 100);
  C.renderCan();
  assert.doesNotMatch(ageCell().textContent, /^-/, "a stale reading made every age negative");
  assert.equal(ageCell().className, "age-fresh");
});

// ---- D-6 ---------------------------------------------------------------------------

// Every wire form parseCanEvent accepts, and some near misses. Each line's own row key comes
// from the real decoder; a pattern must select exactly the lines that decode to its row.
const CORPUS = [
  "!can 12 - 7DF 0201", "!can 12 - 7df 0201", "!can1 12 - 7DF 0201", "!can  12   -  7DF  0201",
  "  !can 12 - 7DF 0201", "!can\t12\t-\t7DF\t0201", "!can 12 r 7DF 2", "!can 12 rr 07df 2",
  "!can 12 - 0x7Df 01", "!can 12 x 7DF 0201", "!can 12 xr 0X7df 2", "!can 12 rx 000007DF 2",
  "!can2 12 - 7DF 01", "!can2 12 x 7df 01", "!can 12 - 7DE 01", "!can 12 x 17DF 01",
  "!can 12 x 1ABCDEF 01", "!can 12 x 01abcdef 01", "!can9 1 - 0 -", "!can9 1 - 000 -",
];

function keyOf(raw) {
  C.clearAllCan();
  frame(raw, 1);
  const keys = [...C.canRows.keys()];
  return keys.length ? keys[0] : null;
}

test("an id click selects exactly the lines the decoder files under that row", () => {
  C.setCanPaused(false);
  const keys = CORPUS.map(keyOf);
  // Tokens split on spaces only (SPEC 2.1), so a tab is part of a token and the line no frame.
  assert.deepEqual(CORPUS.filter((_, i) => keys[i] === null), ["  !can 12 - 7DF 0201", "!can\t12\t-\t7DF\t0201"],
    "the table decodes only a line that starts with the event name, split on spaces");
  const entries = new Map();
  for (const raw of CORPUS) { C.clearAllCan(); frame(raw, 1); for (const [k, e] of C.canRows) entries.set(k, e); }
  assert.ok(entries.size >= 8, "the corpus must reach several distinct rows");
  for (const [key, e] of entries) {
    const re = new RegExp(C.canFilterPattern(e));
    CORPUS.forEach((raw, i) => {
      assert.equal(re.test(raw), keys[i] === key,
        `pattern ${re} for row ${key} against ${JSON.stringify(raw)} (decodes to ${keys[i]})`);
    });
  }
});

// ---- D-7 ---------------------------------------------------------------------------

const lit = () => { C.renderCan(); return dataRows()[0].children[2].children.map((b) => b.className === "byte chg"); };

test("an id polled by RTR between its data frames still lights the byte that moved", () => {
  reset();
  frame("!can 1 - 200 AABB", 1);
  C.renderCan();
  frame("!can 2 - 200 AACC", 1.1);   // byte 1 moved
  frame("!can 3 r 200 2", 1.2);
  frame("!can 4 - 200 AACC", 1.3);
  assert.deepEqual(lit(), [false, true], "the change before the remote frame was discarded");
  // Requester and responder on one bus: data, RTR, data, RTR, with a paint after each data frame.
  frame("!can 5 r 200 2", 1.4);
  frame("!can 6 - 200 AADD", 1.5);
  assert.deepEqual(lit(), [false, true], "diffed against the last data frame, across the RTR");
});

test("a length change after a remote frame still lights nothing", () => {
  reset();
  frame("!can 1 - 200 AABB", 1);
  C.renderCan();
  frame("!can 2 r 200 3", 1.1);
  frame("!can 3 - 200 AACCDD", 1.2);
  assert.deepEqual(lit(), [false, false, false]);
});

// ---- D-8 ---------------------------------------------------------------------------

test("collapsing a group divider works while the table is paused", () => {
  reset();
  frame("!can 1 - 100 AA", 1, "a");
  frame("!can 1 - 200 BB", 1, "b");
  C.renderCan();
  C.setCanPaused(true);
  const header = () => wrap().querySelectorAll("tr").find((t) => t.className.includes("bus-hdr"));
  assert.equal(dataRows().length, 2);
  header().emit("click");
  assert.equal(dataRows().length, 1, "the view was keyed on the frozen version alone");
  assert.equal(header().children[0].children[0].textContent, "▸ ");
  header().emit("click");
  assert.equal(dataRows().length, 2, "and a second click expands it again");
});

// ---- F-15, F-16 ---------------------------------------------------------------------

test("a paused table's ages stand still across a repaint after the clock moved", () => {
  reset();
  frame("!can 1 - 100 AA", 1000);
  frame("!can 1 - 300 AA", 1001);
  C.renderCan();
  C.setCanPaused(true);
  const frozenAge = cellsOf(dataRows()[0])[4];
  frame("!can 2 - 100 AB", 1030);   // the anchor moves 29 s on, into a frame the snapshot lacks
  C.noteDaemonNow(1100);
  C.renderCan();
  assert.equal(cellsOf(dataRows()[0])[4], frozenAge, "a frozen table's ages kept ticking");
});

test("a period just under a second reads in ms, and from 999.5 ms it reads as seconds", () => {
  reset();
  frame("!can 1 - 100 AA", 2000);
  frame("!can 2 - 100 AA", 2000.9996);
  frame("!can 1 - 101 AA", 3000);
  frame("!can 2 - 101 AA", 3000.9994);
  C.renderCan();
  const periods = dataRows().map((tr) => cellsOf(tr)[3]);
  assert.deepEqual(periods, ["1.0s", "999ms"], "never '1000ms'");
});

// ---- F-13, F-14, D-2: the paused table's export ---------------------------------------

async function exportShown() {
  seen.lastUrl = null;
  env.byId("canExport").emit("click");
  env.byId("expModeShown").emit("change");
  env.byId("expGo").emit("click");
  await tick();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

test("a paused table exports up to its freeze, over the span its rows came from", async () => {
  reset();
  frame("!can 1 - 100 AA", 4000.25);
  frame("!can 1 - 200 AA", 4003.5);
  const oldest = state.maxId;
  frame("!can 2 - 100 AB", 4004);   // the older id's last frame is now 4004, the other's 4003.5
  C.setCanPaused(true);
  const frozenAt = state.maxId;
  frame("!can 3 - 100 AC", 4010);
  const q = await exportShown();
  assert.equal(q.get("id_to"), String(frozenAt), "a paused table must not export past its freeze");
  assert.equal(q.get("since_id"), String(oldest - 1),
    "the window starts at the OLDEST row's last frame, not the newest");
  assert.equal(q.has("since_ts") || q.has("until_ts"), false, "by line id, and it ends at the freeze");
  assert.equal(q.has("last_ms"), false);
  assert.deepEqual(seen.refusals, []);
});

test("a paused table whose filter hides every row offers no shown window", () => {
  reset();
  frame("!can 1 - 100 AA", 4000);
  C.setCanPaused(true);
  C.setCanFilter("7DF");
  env.byId("canExport").emit("click");
  assert.equal(env.byId("expModeShown").disabled, true, "no row on screen names a first id");
  env.byId("expCancel").emit("click");
  C.setCanFilter("100");   // positive control: the same table with its row shown
  env.byId("canExport").emit("click");
  assert.equal(env.byId("expModeShown").disabled, false);
  env.byId("expCancel").emit("click");
  C.setCanFilter("");
  C.setCanPaused(false);
});
