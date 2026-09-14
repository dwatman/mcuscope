// Pre-release round 2026-09-15, terminal panes: a history page landing on a pane whose rows
// were replaced while it was in flight (D-1), divider tooltips (D-11), hand-edited termState
// (D-12), and two untested column/empty-state branches (F-21, F-22).
//
// The panes are built by the real createPane from index.html's own <template> classes, so the
// clear, jump and port handlers driven below are the product's, not a transcription.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makePane, makeRow, FakeEl, tick } from "./dom_stub.mjs";
import { paneCfgFromStorage, ALL_CHANS } from "../../mcuscope/webui/pane.js";

const env = installDom();

// The template's elements, flattened: createPane only ever looks them up by class.
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

// A /lines double whose answers wait for release(), so a writer can run mid-flight.
let gated = false;
const waiting = [];
function releaseAll() { while (waiting.length) waiting.shift()(); }
globalThis.fetch = async (url) => {
  const q = new URL(url, "http://x");
  if (gated) await new Promise((r) => waiting.push(r));
  const idTo = Number(q.searchParams.get("id_to"));
  const since = Number(q.searchParams.get("since_id") || 0);
  const lines = [];
  for (let id = idTo; id > idTo - 200 && id > since; id--) lines.push(makeRow(id));
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ lines, truncated: true }) };
};

env.localStorage.setItem("termState", JSON.stringify({ timeMode: "host", panes: [
  { port: "all", channels: ALL_CHANS, regex: "" },
  { port: 7, channels: "resp", regex: { a: 1 } },
  { port: "p1", channels: ["debug", "bogus"], regex: "line" },
  null,
] }));

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));

for (let id = 9001; id <= 10000; id++) buffer.push(makeRow(id));
state.maxId = 10000;
state.knownAliases = ["p1"];
T.initTerminal();
const pane = T.panes[0];

// Pause the pane on the buffer and start a page below it, held in flight.
async function startPage() {
  releaseAll();
  gated = false;
  pane.clearId = 0;   // an earlier test's clear
  if (!pane.autoscroll) T.setAutoscroll(pane, true);
  T.setAutoscroll(pane, false);
  assert.equal(pane.rows[0].id, 9001, "the paused pane holds the buffer");
  gated = true;
  const p = T.loadHistory(pane);
  await tick();
  assert.equal(waiting.length, 1, "the page must be in flight");
  return { p };   // wrapped: an async function returning the promise itself would await it
}

const idsBelow = (limit) => pane.rows.filter((r) => r.id <= limit).length;

test("control: a page nothing interfered with lands", async () => {
  const { p } = await startPage();
  releaseAll();
  await p;
  assert.equal(pane.rows[0].id, 8801, "200 older rows prepended");
  assert.equal(pane.historyLoaded, 200);
});

test("clear while a page is in flight: the cleared pane does not refill with what it cleared", async () => {
  const { p } = await startPage();
  pane.el.querySelector(".clear").emit("click");
  const clearId = pane.clearId;
  releaseAll();
  await p;
  assert.equal(idsBelow(clearId), 0, "rows at or below the clear point came back");
  assert.equal(pane.historyLoaded, 0);
  assert.equal(pane.historyNext, null, "the stale page must not move the next page's bound");
});

test("resume while a page is in flight: no capture rows land above a live pane's buffer", async () => {
  const { p } = await startPage();
  pane.jumpBtn.emit("click");
  assert.equal(pane.autoscroll, true);
  releaseAll();
  await p;
  assert.equal(idsBelow(9000), 0, "rows from below the buffer joined a live pane across an unmarked hole");
  assert.equal(pane.historyLoaded, 0);
});

test("a filter change while a page is in flight drops the page asked for under the old filter", async () => {
  const { p } = await startPage();
  pane.portSel.value = "p1";
  pane.portSel.emit("change");
  releaseAll();
  await p;
  assert.equal(pane.autoscroll, false, "re-filtering keeps the pane paused");
  assert.equal(idsBelow(9000), 0);
  pane.portSel.value = "all";
  pane.portSel.emit("change");
});

test("clear-all while a page is in flight drops it too", async () => {
  const { p } = await startPage();
  env.byId("clearAllBtn").emit("click");
  releaseAll();
  await p;
  assert.equal(pane.rows.length, 0, "a cleared pane refilled");
});

test("resetHistory (the capture reset's seam) drops a page in flight", async () => {
  const { p } = await startPage();
  pane.clearId = 0; pane.frozenId = 0; pane.frozenRows = null; pane.rows = [];
  T.resetHistory(pane);
  releaseAll();
  await p;
  assert.equal(pane.rows.length, 0, "rows from the old capture landed in the new one");
  gated = false;
});

// ---- D-12: termState pane configs are type-checked ----------------------------------

test("hand-edited pane configs fall back per field, and what is written back is clean", () => {
  const [, bad, mixed, nul] = T.panes;
  assert.equal(bad.port, "all", "a numeric port is not an alias");
  assert.deepEqual([...bad.channels], ALL_CHANS, "a string is not a channel list");
  assert.equal(bad.regexSrc, "", "an object is not a pattern");
  assert.equal(bad.regex, null);
  assert.equal(mixed.port, "p1");
  assert.deepEqual([...mixed.channels], ["debug"], "an unknown channel name is dropped");
  assert.equal(mixed.regexSrc, "line");
  assert.equal(nul.port, "all");
  const saved = JSON.parse(env.localStorage.getItem("termState")).panes;
  assert.deepEqual(saved[1], { port: "all", channels: ALL_CHANS, regex: "" },
    "the bad values must not be persisted back, or a reload never recovers");
});

test("paneCfgFromStorage keeps a well-typed config as it is", () => {
  assert.deepEqual(paneCfgFromStorage({ port: "sim", channels: ["cmd", "resp"], regex: "ERR" }),
    { port: "sim", channels: ["cmd", "resp"], regex: "ERR" });
  assert.deepEqual(paneCfgFromStorage("x"), { port: "all", channels: ALL_CHANS, regex: "" });
  assert.deepEqual(paneCfgFromStorage({ port: "", channels: [1, "sys"], regex: 5 }),
    { port: "all", channels: ["sys"], regex: "" });
});

// ---- D-11, F-21, F-22: what a rendered row and an empty pane say ---------------------

function renderRows(rows, over = {}) {
  const p = makePane({ ...over });
  p.rows = rows;
  T.render(p);
  return p;
}

test("a marker and a gap divider carry their full text as a tooltip", () => {
  state.timeMode = "host";
  const long = "x".repeat(240);
  const p = renderRows([
    makeRow(1, { chan: "marker", raw: "!m @123 " + long }),
    makeRow(2, { chan: "gap", raw: "gap: 7 lines not loaded" }),
  ]);
  const [marker, gap] = p.vlist.children.map((d) => d.children[1]);
  assert.equal(marker.className, "divider");
  assert.equal(marker.title, "marker: " + long, "a clipped marker had no way to be read");
  assert.equal(gap.title, "gap: 7 lines not loaded");
  for (const d of [marker, gap]) {
    assert.deepEqual(d.children.map((c) => c.className), ["divider-text"],
      "the text must sit in the span style.css shrinks to an ellipsis");
  }
  assert.equal(marker.children[0].textContent, "marker: " + long);
});

test("under the tick time base a gap row reads '-', not an estimate", () => {
  state.timeMode = "tick";
  try {
    const p = renderRows([makeRow(5, { chan: "gap", raw: "gap: 3 lines not loaded" })]);
    assert.equal(p.vlist.children[0].children[0].textContent, "-");
  } finally {
    state.timeMode = "host";
  }
});

test("a paused pane with no snapshot counts nothing past its freeze as waiting rows", () => {
  // The capture reset's state (api.js resetForDbReset): frozenId 0, no snapshot, still paused,
  // while the new capture's rows fill the buffer.
  buffer.length = 0;
  for (let id = 1; id <= 3; id++) buffer.push(makeRow(id));
  const p = renderRows([], { autoscroll: false, frozenId: 0, frozenRows: null });
  const el = p.vlist.children[0];
  assert.ok(el && el.className === "empty-state", "the empty pane must say why it is empty");
  assert.equal(el.textContent, "Waiting for the first line",
    "rows past the freeze are not in this pane's view, so they are not 'in scope'");
});
