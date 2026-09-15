// A clear pressed while a backfill is out covers everything that reached the page before the
// click (SPEC 9.1, 9.2): the backfill's rows, the plot history seed, and live /ws rows staged
// behind the backfill. Rows arriving after the click still show. Browser leg defects A and B
// (docs/review/2026-09-15-prerelease/browser-charts.md). Real createPane and real clear buttons.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, makeRow, FakeEl, tick } from "./dom_stub.mjs";
import { ALL_CHANS } from "../../mcuscope/webui/pane.js";

const env = installDom();
const tpl = readFileSync(new URL("../../mcuscope/webui/index.html", import.meta.url), "utf8")
  .match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const tplRoot = new FakeEl("div");
for (const m of tpl.matchAll(/<(\w+)[^>]*\bclass="([^"]+)"/g)) {
  const el = new FakeEl(m[1]);
  el.className = m[2];
  tplRoot.appendChild(el);
}
env.byId("paneTpl").content.appendChild(tplRoot);

// The daemon. `stored` is the lines table; each route can be held to put a click inside its
// round trip. The seed channels are typed stream 0 (an analog and an enum channel).
let stored = [];
let defs = [];
const SEED_CHANNELS = [
  { name: "v", port: "p1", sid: "0", type: "u2", kind: "analog", last_ts: 1000.004, count: 2 },
  { name: "st", port: "p1", sid: "0", type: "u1", kind: "enum", labels: [[0, "idle"], [1, "run"]],
    last_ts: 1000.004, count: 2 },
];
const SEED_SERIES = [{ line_id: 1, ts: 1000.001, tick_ms: 10, value: 0 },
                     { line_id: 2, ts: 1000.002, tick_ms: 20, value: 1 }];
const gates = {};
const seen = [];
function routeOf(u) {
  if (u.startsWith("/lines?match=")) return "defs";
  if (u.startsWith("/lines")) return "lines";
  if (u.startsWith("/plot/channels")) return "channels";
  if (u.startsWith("/plot/series")) return "series";
  return "other";
}
globalThis.fetch = async (url) => {
  const u = String(url);
  const route = routeOf(u);
  seen.push(route);
  if (gates[route]) await gates[route];
  const q = new URLSearchParams(u.slice(u.indexOf("?") + 1));
  let body = {};
  if (route === "defs") body = { lines: defs, truncated: false };
  else if (route === "lines") {
    const since = Number(q.get("since_id") || 0);
    body = { lines: stored.filter((r) => r.id > since).reverse().slice(0, 200), truncated: false };
  } else if (route === "channels") body = { channels: SEED_CHANNELS };
  else if (route === "series") body = { points: SEED_SERIES };
  return { ok: true, status: 200, headers: { get: () => null }, json: async () => body };
};
function hold(route) {
  let release;
  gates[route] = new Promise((r) => { release = r; });
  return () => { delete gates[route]; release(); };
}

env.localStorage.setItem("termState", JSON.stringify({ timeMode: "host", panes: [
  { port: "all", channels: ALL_CHANS, regex: "" },
  { port: "all", channels: ALL_CHANS, regex: "" },
] }));

const { state, buffer } = await import(webuiUrl("state.js"));
const T = await import(webuiUrl("terminal.js"));
const { canRows, initCan } = await import(webuiUrl("can.js"));
const { charts, clearAllCharts } = await import(webuiUrl("plots.js"));
const { digitalLanes, clearAllDigital } = await import(webuiUrl("digital.js"));
const { connectWs, reconnectStream } = await import(webuiUrl("api.js"));
T.initTerminal();
initCan();
const [a, b] = T.panes;

const ids = (p) => p.rows.map((r) => r.id);
const frame = (sock, rows) => sock.onmessage({ data: JSON.stringify(rows) });
const clearAll = () => env.byId("clearAllBtn").emit("click");
const settle = async () => { for (let i = 0; i < 8; i++) await tick(0); await tick(40); };
async function until(what, cond) {
  for (let i = 0; i < 20 && !cond(); i++) await tick(0);
  assert.ok(cond(), `setup: ${what} never happened`);
}

// Nothing held anywhere: no watermark, no rows, charts, lanes or CAN frames.
function blank() {
  for (const k of Object.keys(gates)) delete gates[k];
  state.maxId = 0;
  buffer.length = 0;
  clearAllCharts();
  clearAllDigital();
  env.byId("canClear").emit("click", {});
  for (const p of T.panes) { p.clearId = 0; p.rows = []; p.queue.length = 0; }
  seen.length = 0;
  stored = [1, 2, 3, 4].map((id) => makeRow(id));
  defs = [];
}
function open() {
  connectWs();
  return env.sockets.at(-1);
}

// ---- A: the plot history seed --------------------------------------------------------

async function seedPage(route, click) {
  blank();
  const release = hold(route);
  open().onopen();
  await until(`the ${route} request`, () => seen.includes(route));
  if (click) clearAll();
  release();
  await settle();
}

test("A control: an undisturbed first connect seeds the chart and the lane", async () => {
  await seedPage("lines", false);
  assert.equal(charts.get("p1|s0")?.xsHost.length, 2, "the seed built no chart");
  assert.equal(digitalLanes.size, 1, "the seed built no lane");
});

test("A: clear-all while /lines is out drops the seed on charts and lanes", async () => {
  await seedPage("lines", true);
  assert.equal(charts.size, 0, "the history seed landed on charts cleared during /lines");
  assert.equal(digitalLanes.size, 0, "the history seed landed on lanes cleared during /lines");
  assert.deepEqual(ids(a), [], "setup: the backfill gate itself failed");
});

test("A: clear-all while the definition seed is out drops the history seed", async () => {
  await seedPage("defs", true);
  assert.equal(charts.size, 0, "the history seed landed on charts cleared during the !pd seed");
  assert.equal(digitalLanes.size, 0);
});

test("A: clear-all while /plot/series is out drops the history seed", async () => {
  await seedPage("series", true);
  assert.equal(charts.size, 0, "the history seed landed on charts cleared during /plot/series");
  assert.equal(digitalLanes.size, 0);
});

// ---- B: live rows staged behind the backfill -----------------------------------------

// Stream 7 is declared only by a staged row, so a post-click sample decodes only if that
// definition reached the cache. The definition cache outlives a clear, so each page uses a
// port no other test has declared stream 7 on. Clear-all does not clear CAN; the CAN clear is
// its own button.
let BEFORE, AFTER, PORT;
let ports = 0;
function newStreams() {
  PORT = `bp${++ports}`;
  const ev = (id, raw) => makeRow(id, { chan: "event", port: PORT, raw });
  BEFORE = [ev(5, "!pd 7 w:u2 s:u1:=0=idle,1=run"), ev(6, "!ps 7 64 0001,00"),
            ev(7, "!can 1 - 111 00"), makeRow(8, { port: PORT })];
  AFTER = [ev(9, "!ps 7 C8 0002,01"), ev(10, "!can 1 - 222 00"), makeRow(11, { port: PORT })];
}
const ALL = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const chart7 = () => charts.get(`${PORT}|s7`)?.xsHost.length ?? 0;
const lane7 = () => digitalLanes.get([...digitalLanes.keys()].find((k) => k.startsWith(PORT)))?.vs.length ?? 0;

// A first connect whose /lines answer (rows 1..4) is held while BEFORE and AFTER arrive on /ws,
// with `click` run between the two frames.
async function stagedPage(click) {
  blank();
  newStreams();
  const release = hold("lines");
  const sock = open();
  sock.onopen();
  await until("the backfill request", () => seen.includes("lines"));
  frame(sock, BEFORE);
  click();
  frame(sock, AFTER);
  release();
  await settle();
  return sock;
}

test("B control: nothing cleared, every staged row reaches panes, CAN, charts and lanes", async () => {
  await stagedPage(() => {});
  assert.deepEqual(ids(a), ALL);
  assert.deepEqual(ids(b), ALL);
  assert.equal(canRows.size, 2);
  assert.equal(chart7(), 2);
  assert.equal(lane7(), 2);
});

test("B: clear-all between staged frames hides the earlier rows everywhere, keeps the later", async () => {
  await stagedPage(clearAll);
  // The rebuilt pane reads clearId; the other still holds what the drain queued into it.
  T.rebuild(b);
  assert.deepEqual(ids(b), [9, 10, 11], "a rebuild from the buffer brought the cleared rows back");
  assert.deepEqual(ids(a), [9, 10, 11], "the drain queued rows staged before the clear into a pane");
  assert.equal(chart7(), 1, "the chart holds a sample staged before the clear, or lost the later one");
  assert.equal(lane7(), 1, "the lane holds a sample staged before the clear, or lost the later one");
  assert.equal(canRows.size, 2, "clear-all does not clear the CAN table");
  assert.equal(buffer.length, 11, "staged rows still reach the shared buffer");
});

test("B: the CAN clear between staged frames keeps only the later frame", async () => {
  await stagedPage(() => env.byId("canClear").emit("click", {}));
  assert.deepEqual([...canRows.values()].map((e) => e.id), [0x222],
    "a CAN frame staged before the CAN clear came back, or the later one was lost");
  assert.deepEqual(ids(a), ALL, "the CAN clear hid pane rows");
  assert.equal(chart7(), 2, "the CAN clear hid chart samples");
});

test("B: one pane's clear between staged frames hides the earlier rows in that pane only", async () => {
  await stagedPage(() => a.el.querySelector(".clear").emit("click"));
  assert.deepEqual(ids(a), [9, 10, 11], "the cleared pane shows rows staged before its clear");
  assert.deepEqual(ids(b), ALL, "a pane nobody cleared lost staged rows");
  assert.equal(chart7(), 2);
});

test("B: clear-all after the last staged row covers every staged row; later live rows show", async () => {
  blank();
  newStreams();
  const release = hold("lines");
  const sock = open();
  sock.onopen();
  await until("the backfill request", () => seen.includes("lines"));
  frame(sock, [...BEFORE, ...AFTER]);
  clearAll();
  release();
  await settle();
  assert.deepEqual(ids(a), [], "a clear after the last staged row let staged rows through");
  assert.equal(chart7(), 0);
  assert.equal(lane7(), 0);
  frame(sock, [makeRow(12, { chan: "event", port: PORT, raw: "!ps 7 12C 0003,01" }), makeRow(13)]);
  await settle();
  assert.deepEqual(ids(a), [12, 13], "a live row after the drain did not show");
  assert.equal(chart7(), 1, "the cleared staged !pd did not reach the definition cache");
});

test("B: a pane added during the backfill gets the staged rows; its own clear covers them", async () => {
  blank();
  newStreams();
  const release = hold("lines");
  const sock = open();
  sock.onopen();
  await until("the backfill request", () => seen.includes("lines"));
  frame(sock, BEFORE);
  env.byId("addPaneBtn").emit("click");
  const c = T.panes.at(-1);
  env.byId("addPaneBtn").emit("click");
  const d = T.panes.at(-1);
  try {
    d.el.querySelector(".clear").emit("click");
    frame(sock, AFTER);
    release();
    await settle();
    assert.deepEqual(ids(c), ALL, "a pane added during the backfill lost staged rows");
    assert.deepEqual(ids(d), [9, 10, 11], "a pane added and cleared during the backfill");
  } finally {
    d.el.querySelector(".closepane").emit("click");
    c.el.querySelector(".closepane").emit("click");
  }
});

test("B: the reconnect backfill applies the same cut to its staged rows", async () => {
  blank();
  let sock = open();
  sock.onopen();
  await settle();
  assert.deepEqual(ids(a), [1, 2, 3, 4], "setup: the first connect did not land");
  stored = [1, 2, 3, 4, 5, 6].map((id) => makeRow(id));
  const release = hold("lines");
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  await until("the reconnect backfill request", () => seen.filter((r) => r === "lines").length >= 2);
  frame(sock, [makeRow(6), makeRow(7), makeRow(8)]);
  clearAll();
  frame(sock, [makeRow(9)]);
  release();
  await settle();
  assert.deepEqual(ids(a), [9], "the reconnect drain let rows staged before the clear through");
});

test("B: the capture-reset re-seed applies the same cut to its staged rows", async () => {
  blank();
  const tok = `cap-${Math.random()}`;
  let sock = open();
  sock.onopen();
  frame(sock, [{ capture: `${tok}-old` }]);
  await settle();
  // The new capture: the re-seed's /lines answers row 1 only, rows 1 and 2 arrive on /ws first.
  stored = [makeRow(1, { raw: "new 1" })];
  const release = hold("lines");
  frame(sock, [{ capture: `${tok}-new` }, makeRow(1, { raw: "new 1" }), makeRow(2, { raw: "new 2" })]);
  await until("the re-seed request", () => seen.filter((r) => r === "lines").length >= 2);
  clearAll();
  frame(sock, [makeRow(3, { raw: "new 3" })]);
  release();
  await settle();
  assert.deepEqual(a.rows.map((r) => r.raw), ["new 3"],
    "the re-seed drain let rows staged before the clear through");
});

test("B control: the capture-reset re-seed with no clear shows every new-capture row", async () => {
  blank();
  const tok = `cap-${Math.random()}`;
  const sock = open();
  sock.onopen();
  frame(sock, [{ capture: `${tok}-old` }]);
  await settle();
  stored = [makeRow(1, { raw: "new 1" })];
  const release = hold("lines");
  frame(sock, [{ capture: `${tok}-new` }, makeRow(1, { raw: "new 1" }), makeRow(2, { raw: "new 2" })]);
  await until("the re-seed request", () => seen.filter((r) => r === "lines").length >= 2);
  frame(sock, [makeRow(3, { raw: "new 3" })]);
  release();
  await settle();
  assert.deepEqual(a.rows.map((r) => r.raw), ["new 1", "new 2", "new 3"]);
});

// ---- L1, L2: a control object staged before a clear; the relative-time zero ----------

test("B: a {gap} notice staged before one pane's clear leaves that pane's later rows showing", async () => {
  await stagedPage(() => {});   // declares PORT's stream 7 for the page below
  blank();
  newStreams();
  const release = hold("lines");
  const sock = open();
  sock.onopen();
  await until("the backfill request", () => seen.includes("lines"));
  frame(sock, [{ gap: 3 }, ...BEFORE]);
  a.el.querySelector(".clear").emit("click");
  frame(sock, AFTER);
  release();
  await settle();
  T.rebuild(a);
  assert.deepEqual(ids(a), [9, 10, 11], "a staged {gap} before the clear left the pane blank on rebuild");
});

test("clear-all during the backfill re-zeroes relative time on the first row shown, not a covered one", async () => {
  await stagedPage(clearAll);
  assert.deepEqual(ids(a), [9, 10, 11], "setup");
  assert.equal(state.anchorTs, a.rows[0].ts,
    "relative time zeroed on a backfill or staged row the clear-all covers");
});

// ---- M1: a staging area dropped before its drain -------------------------------------

// A dropped area's CAN and chart floors are module state that only a capture reset lifts (the
// watermark never otherwise returns below them), so each page below starts on a new capture.
let captures = 0;
async function newCapture() {
  const sock = open();
  sock.onopen();
  frame(sock, [{ capture: `floors-${++captures}` }]);
  await settle();
}

// The first connect lands rows 1..5 (5 declares PORT's stream 7). A reconnect's backfill is
// answered; rows 6 and 7 are staged; `click`; the area is dropped by `drop` before the drain;
// then rows 8 and 9 are captured and the next connection backfills 6..9.
async function droppedPage(click, drop) {
  await newCapture();
  blank();
  newStreams();
  const ev = (id, raw) => makeRow(id, { chan: "event", port: PORT, raw });
  stored = [...[1, 2, 3, 4].map((id) => makeRow(id)), ev(5, "!pd 7 w:u2 s:u1:=0=idle,1=run")];
  let sock = open();
  sock.onopen();
  await settle();
  assert.deepEqual(ids(a), [1, 2, 3, 4, 5], "setup: the first connect did not land");
  const release = hold("lines");
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  await until("the reconnect backfill request", () => seen.filter((r) => r === "lines").length >= 2);
  const staged = [ev(6, "!can 1 - 111 00"), ev(7, "!ps 7 64 0001,00")];
  stored.push(...staged);
  frame(sock, staged);
  click();
  sock = drop(sock);
  stored.push(ev(8, "!can 1 - 222 00"), ev(9, "!ps 7 C8 0002,01"));
  release();
  sock.onopen();
  await settle();
}
// The socket closes (its reconnect then runs at once), or a token save supersedes the handshake.
const closed = (sock) => { sock.onclose({}); reconnectStream(); return env.sockets.at(-1); };
const superseded = () => { reconnectStream(); return env.sockets.at(-1); };
const canIds = () => [...canRows.values()].map((e) => e.id);

test("M1 control: a dropped staging area with no clear loses nothing on the next connection", async () => {
  await droppedPage(() => {}, closed);
  assert.deepEqual(ids(a), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.deepEqual(canIds(), [0x111, 0x222]);
  assert.equal(chart7(), 2);
});

test("M1: clear-all then the socket closes before the drain: the next backfill keeps the covered rows hidden", async () => {
  await droppedPage(clearAll, closed);
  T.rebuild(b);
  assert.deepEqual(ids(a), [8, 9], "rows staged before the clear came back through the next backfill");
  assert.deepEqual(ids(b), [8, 9], "a rebuild brought back rows staged before the clear");
  assert.equal(chart7(), 1, "a chart sample staged before the clear came back through the next backfill");
  assert.equal(lane7(), 1);
  assert.equal(state.anchorTs, a.rows[0].ts, "relative time zeroed on a row the dropped area's clear covered");
});

test("M1: the CAN clear then a token save before the drain keeps the covered frame out", async () => {
  await droppedPage(() => env.byId("canClear").emit("click", {}), superseded);
  assert.deepEqual(canIds(), [0x222], "a CAN frame staged before the CAN clear came back through the next backfill");
  assert.deepEqual(ids(a), [1, 2, 3, 4, 5, 6, 7, 8, 9], "the CAN clear hid pane rows");
  assert.equal(chart7(), 2, "the CAN clear hid chart samples");
});

test("M1: one pane's clear then a token save before the drain covers that pane only", async () => {
  await droppedPage(() => a.el.querySelector(".clear").emit("click"), superseded);
  assert.deepEqual(ids(a), [8, 9], "the superseding handshake brought back rows staged before the pane's clear");
  assert.deepEqual(ids(b), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test("M1: a capture reset after the drop lifts its floors: the new capture's low ids all show", async () => {
  await droppedPage(clearAll, closed);
  assert.deepEqual(ids(a), [8, 9], "setup: the floor did not hold before the reset");
  // A new capture whose ids restart at 1 and pass below the old floor (7).
  const ev = (id, raw) => makeRow(id, { chan: "event", port: PORT, raw });
  stored = [makeRow(1, { port: PORT, raw: "new 1" }), ev(2, "!can 1 - 333 00"), ev(3, "!ps 7 12C 0003,01")];
  frame(env.sockets.at(-1), [{ capture: `floors-${++captures}` }]);
  await settle();
  assert.deepEqual(ids(a), [1, 2, 3], "a new-capture row below the old capture's floor stayed hidden in a pane");
  assert.deepEqual(canIds(), [0x333], "a new-capture CAN frame below the old capture's floor stayed hidden");
  assert.equal(chart7(), 1, "a new-capture sample below the old capture's floor stayed hidden");
});

test("M1: a first connect superseded after a staged clear-all drops the next connection's history seed", async () => {
  for (const cleared of [false, true]) {
    await newCapture();
    blank();
    newStreams();
    const release = hold("lines");
    let sock = open();
    sock.onopen();
    await until("the backfill request", () => seen.includes("lines"));
    frame(sock, BEFORE);
    if (cleared) clearAll();
    sock = superseded();
    sock.onopen();
    stored = [...stored, ...BEFORE, ...AFTER];
    release();
    await settle();
    if (!cleared) {
      assert.equal(charts.get("p1|s0")?.xsHost.length, 2, "control: the seed built no chart");
      assert.deepEqual(ids(a), ALL, "control");
      continue;
    }
    assert.equal(charts.get("p1|s0"), undefined, "the history seed landed on charts a staged clear-all covered");
    assert.equal(digitalLanes.has("p1|s0|st"), false);
    assert.deepEqual(ids(a), [9, 10, 11], "the next first connect brought back rows staged before the clear");
    assert.equal(chart7(), 1, "the next first connect plotted a sample staged before the clear, or lost the later one");
  }
});

// ---- M2: staging past its cap --------------------------------------------------------

test("M2: staging past its cap keeps a capture token; the new capture is not read as duplicates", async () => {
  blank();
  const tok = `cap-${Math.random()}`;
  stored = [1, 2, 3, 4].map((id) => makeRow(id, { raw: "old" }));
  let sock = open();
  sock.onopen();
  frame(sock, [{ capture: `${tok}-old` }]);
  await settle();
  const release = hold("lines");
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  await until("the reconnect backfill request", () => seen.filter((r) => r === "lines").length >= 2);
  const burst = [];
  for (let id = 1; id <= 6000; id++) burst.push(makeRow(id, { raw: "new" }));
  frame(sock, [{ capture: `${tok}-old` }, ...[5, 6, 7].map((id) => makeRow(id, { raw: "old" })),
               { capture: `${tok}-new` }, ...burst]);
  stored = burst.slice(-200);
  release();
  await settle();
  assert.equal(buffer.filter((r) => r.raw === "old").length, 0,
    "the capture token was dropped from full staging: old-capture rows survived");
  assert.equal(state.maxId, 6000, "the new capture did not land");
});

test("M2: staging past its cap drops the oldest rows, and a clear's cut still falls at the click", async () => {
  await newCapture();
  blank();
  let sock = open();
  sock.onopen();
  await settle();
  const release = hold("lines");
  reconnectStream();
  sock = env.sockets.at(-1);
  sock.onopen();
  await until("the reconnect backfill request", () => seen.filter((r) => r === "lines").length >= 2);
  const burst = [];
  for (let id = 5; id <= 6004; id++) burst.push(makeRow(id));
  frame(sock, burst);
  clearAll();
  frame(sock, [makeRow(6005), makeRow(6006), makeRow(6007)]);
  const warned = [];
  const warn = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    release();
    await settle();
  } finally { console.warn = warn; }
  // 6003 staged: past BUFFER_MAX + 512 the oldest go down to BUFFER_MAX, once.
  assert.deepEqual(warned, ["stream: 513 rows dropped while the backfill ran (staging full)"],
    "staging was not capped");
  T.rebuild(b);
  assert.deepEqual(ids(a), [6005, 6006, 6007], "full staging lost rows after the click, or showed rows before it");
  assert.deepEqual(ids(b), [6005, 6006, 6007], "a rebuild after a trimmed staging area showed rows before the click");
});
