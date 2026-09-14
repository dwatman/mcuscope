// can.js panel head: the id filter, the empty-section fold, `clear`, the column titles and the
// export dialog's CAN fields. Each drives the path a user reaches only after something else
// happened first: a filter typed while paused, a filter surviving a clear, a fold on clear.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { state } = await import(webuiUrl("state.js"));
const { canIngest, renderCan, canRows, clearAllCan, initCan, setCanPaused, setCanFilter } =
  await import(webuiUrl("can.js"));
const { initExportDialog } = await import(webuiUrl("exportdlg.js"));
initExportDialog();
initCan();

const sidebar = env.byId("sidebar");
const foldedAtInit = sidebar.classList.contains("can-empty");
let seq = 1;
function ingest(raw, port = "p1") {
  state.maxId = seq;
  canIngest({ id: seq++, ts: 1000 + seq / 100, port, chan: "event", raw });
}
const shownIds = () => {
  renderCan();
  return env.byId("canWrap").querySelectorAll("tr").slice(1).map((tr) => tr.children[0].textContent);
};
const reset = () => { setCanPaused(false); setCanFilter(""); clearAllCan(); };

// ---- empty section fold ----------------------------------------------------------------

test("the CAN section folds while empty, opens on the first row and folds again on clear", () => {
  assert.equal(foldedAtInit, true, "a page that never receives a frame never folded: only a render set the class");
  reset();
  assert.equal(sidebar.classList.contains("can-empty"), true, "an empty table must fold to its head");
  ingest("!can 1 - 100 DE");
  renderCan();
  assert.equal(sidebar.classList.contains("can-empty"), false, "the first row did not open the section");
  env.byId("canClear").emit("click");
  assert.equal(canRows.size, 0, "the clear button did not clear");
  assert.equal(sidebar.classList.contains("can-empty"), true, "clear left an open, empty section");
});

test("a cleared paused table stays folded while live frames arrive behind it", () => {
  reset();
  ingest("!can 1 - 100 DE");
  renderCan();
  setCanPaused(true);
  clearAllCan();
  ingest("!can 1 - 200 DE");
  renderCan();
  assert.equal(sidebar.classList.contains("can-empty"), true, "the fold followed the live rows, not the frozen view");
  setCanPaused(false);
  assert.equal(sidebar.classList.contains("can-empty"), false, "resume shows the live row, so it opens");
});

// Folded in the Both view, the head is all there is: a bare "CAN" head with an id box and three
// buttons read as "no CAN section at all", so the head carries the empty state itself.
const headState = () => ({
  empty: !env.byId("canHeadEmpty").hidden,
  filter: !env.byId("canIdFilter").hidden,
  exp: !env.byId("canExport").hidden,
  clear: !env.byId("canClear").hidden,
  pause: !env.byId("canPause").hidden,
});

test("a folded head says no frames yet and hides the controls with nothing to act on", () => {
  reset();
  assert.deepEqual(headState(), { empty: true, filter: false, exp: true, clear: false, pause: true });
  assert.equal(env.byId("canHeadEmpty").textContent, "no frames yet");
  assert.match(env.byId("canHeadEmpty").title, /^One line per frame: !can <tick>.*section 2\.5\.$/,
    "the grammar and the doc pointers ride in the head's tooltip");
  ingest("!can 1 - 100 DE");
  renderCan();
  assert.deepEqual(headState(), { empty: false, filter: true, exp: true, clear: true, pause: true },
    "the first frame must bring the controls back and take the empty line away");
  env.byId("canClear").emit("click");
  assert.deepEqual(headState(), { empty: true, filter: false, exp: true, clear: false, pause: true },
    "clear folds the head back to its empty state");
});

test("a paused, cleared table keeps pause and its tag, and resume brings the controls back", () => {
  reset();
  ingest("!can 1 - 100 DE");
  renderCan();
  setCanPaused(true);
  clearAllCan();
  ingest("!can 1 - 200 DE");
  renderCan();
  assert.deepEqual(headState(), { empty: true, filter: false, exp: true, clear: false, pause: true },
    "the head follows the frozen (empty) view, not the live rows behind it");
  assert.equal(env.byId("canPausedTag").hidden, false, "the paused tag says why nothing shows");
  assert.equal(env.byId("canPause").textContent, "resume");
  setCanPaused(false);
  assert.deepEqual(headState(), { empty: false, filter: true, exp: true, clear: true, pause: true });
});

test("a filter matching nothing is not an empty table: the head keeps its controls", () => {
  reset();
  ingest("!can 1 - 100 DE");
  setCanFilter("7ff");
  renderCan();
  assert.deepEqual(headState(), { empty: false, filter: true, exp: true, clear: true, pause: true },
    "hiding the filter box here would take away the way back out");
  setCanFilter("");
});

test("the CAN view keeps its body empty state and does not repeat it in the head", () => {
  const css = readFileSync(new URL(webuiUrl("style.css")), "utf8");
  assert.match(css, /\.sidebar\[data-view="can"\] \.can-head \.can-head-empty \{ display: none; \}/);
  assert.match(css, /\.can-head \.can-filter\[hidden\], \.can-head \.iconbtn\[hidden\], \.can-head \.can-head-empty\[hidden\] \{ display: none; \}/,
    "a display rule on these would beat [hidden] the way .plot-head's did");
  reset();
  assert.equal(env.byId("canWrap").children[0].className, "empty-state");
});

// ---- id filter ---------------------------------------------------------------------------

test("the filter input narrows the table by id substring, 0x prefix and case ignored", () => {
  reset();
  ingest("!can 1 - 100 DE");
  ingest("!can 1 - 1AB DE");
  ingest("!can 1 x 18A DE");
  const input = env.byId("canIdFilter");
  input.value = "0x1a";
  input.emit("input");
  assert.deepEqual(shownIds(), ["1AB"]);
  assert.equal(env.byId("canCount").textContent, "1 of 3 ids");
  input.value = "18A";
  input.emit("input");
  assert.deepEqual(shownIds(), ["0000018Aext"], "the extended id is matched on its padded form");
  input.value = "";
  input.emit("input");
  assert.deepEqual(shownIds(), ["100", "0000018Aext", "1AB"], "numeric id order");
  assert.equal(env.byId("canCount").textContent, "3 ids");
});

test("a filter with no match says so in the table and keeps the section open", () => {
  reset();
  ingest("!can 1 - 100 DE");
  setCanFilter("7ff");
  assert.deepEqual(shownIds(), ["no id contains 7FF"]);
  assert.equal(env.byId("canCount").textContent, "0 of 1 id");
  assert.equal(sidebar.classList.contains("can-empty"), false, "a no-match filter folded away its own input");
  ingest("!can 2 - 7FF DE");   // a matching id arrives: a row-set change rebuilds under the filter
  assert.deepEqual(shownIds(), ["7FF"]);
});

test("a filter typed while paused filters the frozen rows, not the live ones", () => {
  reset();
  ingest("!can 1 - 100 DE");
  ingest("!can 1 - 200 DE");
  renderCan();
  setCanPaused(true);
  ingest("!can 1 - 210 DE");   // live only
  setCanFilter("2");
  assert.deepEqual(shownIds(), ["200"], "the filter rebuild drew a live id onto the frozen table");
  setCanPaused(false);
  assert.deepEqual(shownIds(), ["200", "210"], "resume must apply the filter to the live rows");
});

test("the filter survives a clear and applies to the rows that follow", () => {
  reset();
  setCanFilter("1");
  ingest("!can 1 - 100 DE");
  clearAllCan();
  ingest("!can 1 - 300 DE");
  ingest("!can 1 - 301 DE");
  assert.deepEqual(shownIds(), ["301"]);
  assert.equal(env.byId("canCount").textContent, "1 of 2 ids");
});

test("filtered-out groups lose their divider; a collapsed match still shows its count", () => {
  reset();
  env.localStorage.setItem("canCollapsed", JSON.stringify(["p1 CAN2"]));
  ingest("!can 1 - 100 DE");
  ingest("!can2 1 - 610 DE");
  ingest("!can2 1 - 611 DE");
  ingest("!can 1 - 100 DE", "p2");
  setCanFilter("10");   // 100 on both ports and 610: three groups
  assert.deepEqual(shownIds(), ["▾ p1 CAN1", "100", "▸ p1 CAN2 (1 id)", "▾ p2 CAN1", "100"],
    "the collapsed divider counts the matches, not the group");
  setCanFilter("61");   // one group left: the plain table, as for a single group unfiltered
  assert.deepEqual(shownIds(), ["610", "611"]);
  env.localStorage.clear();
});

// The table snapshot is "what is on screen", so an id the filter hides is not in it.
async function openCanExport() {
  env.byId("canExport").emit("click");
  await tick();
}
const optSelect = () => env.byId("expOptions").querySelector("select");
const idsField = () => env.byId("expOptions").querySelector("#expOpt_ids");

test("the export prefill and the snapshot follow the filter", async () => {
  reset();
  ingest("!can 1 - 100 DE");
  ingest("!can 1 - 200 DE");
  setCanFilter("2");
  renderCan();
  await openCanExport();
  assert.equal(idsField().value, "200", "the prefill named an id the filter hides");
  optSelect().value = "snapshot";
  optSelect().emit("change");
  const before = env.blobs.length;
  env.byId("expGo").emit("click");
  await tick();
  const lines = env.blobs.slice(before).at(-1).parts.join("").trim().split("\n");
  assert.deepEqual(lines.slice(1).map((l) => l.split(",")[2]), ["200"]);
});

// ---- export fields -------------------------------------------------------------------------

test("the ids field is disabled under snapshot and back under history; Source names both", async () => {
  reset();
  ingest("!can 1 - 100 DE");
  await openCanExport();
  const labels = env.byId("expOptions").querySelectorAll("label").map((l) => l.textContent);
  assert.deepEqual(labels, ["Source", "CAN ids"]);
  assert.deepEqual(optSelect().children.map((o) => [o.value, o.textContent]),
    [["history", "frame history (capture)"], ["snapshot", "table snapshot (on screen)"]]);
  assert.equal(idsField().disabled, false, "history is the default, and it takes ids");
  optSelect().value = "snapshot";
  optSelect().emit("change");
  assert.equal(idsField().disabled, true, "ids stayed live under a snapshot that ignores them");
  optSelect().value = "history";
  optSelect().emit("change");
  assert.equal(idsField().disabled, false);
});

test("changing an option does not wipe clock bounds typed but not yet exported", async () => {
  reset();
  ingest("!can 1 - 100 DE");
  await openCanExport();
  env.byId("expModeClock").emit("change");
  env.byId("expFrom").value = "2026-09-14T10:00:00";
  optSelect().value = "snapshot";
  optSelect().emit("change");
  assert.equal(env.byId("expFrom").value, "2026-09-14T10:00:00", "an option change re-rendered the range");
  env.byId("expModeSession").emit("change");
});

// ---- head and columns ----------------------------------------------------------------------

test("every column header says what it holds, and period is not labelled ms", () => {
  reset();
  ingest("!can 1 - 100 DE");
  renderCan();
  const ths = env.byId("canWrap").querySelectorAll("th");
  assert.deepEqual(ths.map((th) => th.textContent), ["id", "dlc", "data", "period", "age"]);
  for (const th of ths) assert.ok(th.title.length > 10, `${th.textContent} has no title`);
  assert.match(ths[3].title, /EWMA/);
  assert.match(ths[4].title, /5 periods/);
});

test("the static head and empty state in index.html match what can.js renders", () => {
  const html = readFileSync(new URL(webuiUrl("index.html")), "utf8");
  assert.match(html, /id="canClear"[^>]*>clear</, "the clear button is lower case, like every other control");
  assert.ok(!/>Reset</.test(html), "a Reset button is still in the page");
  assert.match(html, /<aside class="sidebar can-empty" id="sidebar"/, "first paint, before can.js runs, is folded too");
  reset();
  renderCan();
  const el = env.byId("canWrap").children[0];
  const unescape = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  const m = html.match(/<div class="can-wrap" id="canWrap">\s*<div class="empty-state" title="([^"]*)">([^<]*)<\/div>/);
  assert.ok(m, "no static CAN empty state found");
  assert.equal(m[2], el.textContent, "the page-load empty line and the rendered one drifted apart");
  assert.equal(unescape(m[1]), el.title, "the page-load tooltip and the rendered one drifted apart");
  const head = html.match(/id="canHeadEmpty" title="([^"]*)"[^>]*>([^<]*)</);
  assert.ok(head, "no static head empty state");
  assert.equal(unescape(head[1]), env.byId("canHeadEmpty").title, "the head tooltip drifted from can.js");
  assert.equal(head[2], env.byId("canHeadEmpty").textContent, "the head line drifted from can.js");
  assert.doesNotMatch(html, /id="canExport"[^>]* hidden>/, "export stays: frame history outlives a clear");
  for (const id of ["canIdFilter", "canClear"]) {
    assert.match(html, new RegExp(`id="${id}"[^>]* hidden>`), `first paint must hide ${id} as the folded render does`);
  }
});
