// Pre-release fix-diff round, web UI: an Export pending when its dialog closed (FW-1), the shown
// window in tick mode (FW-2), under the CAN filter (FW-3) and at a burst's shared timestamp
// (FW-4), late PlotJuggler answers (FW-5), the restart badge after a failed re-read (FW-6), and
// the plot history seed across a clear-all or capture reset (FW-8).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick, makePane, makeRow } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
const seen = installExportDaemon(env);

// `route` answers a request first; returning undefined falls through to the export double.
const daemon = globalThis.fetch;
let route = null;
globalThis.fetch = async (url, opt = {}) => {
  const r = route && route(String(url), opt);
  return (r && (await r)) || daemon(url, opt);
};
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null },
                        json: async () => body, blob: async () => new Blob(["x"]) });

const { state, setToken, STATUS_TIMEOUT_MS } = await import(webuiUrl("state.js"));
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const C = await import(webuiUrl("can.js"));
const F = await import(webuiUrl("freeze.js"));
const { exportPane } = await import(webuiUrl("terminal.js"));
const { connectWs } = await import(webuiUrl("api.js"));
const { initExportDialog, openExportDialog } = await import(webuiUrl("exportdlg.js"));
const { initSettings } = await import(webuiUrl("settings.js"));
initExportDialog();
env.byId("sidebar").setAttribute("data-view", "both");
C.initCan();

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

// Requests matching `pred` wait until released; `answer(url, opt)` is what they then get.
function holdOn(pred, answer) {
  const held = [];
  route = (u, opt) => (pred(u, opt) ? new Promise((r) => held.push(() => r(answer(u, opt)))) : undefined);
  return held;
}

let nextId = 0;
function ingest(raw, ts, port = "p1") {
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
  await settle();
  assert.ok(seen.lastUrl, "no export request was issued");
  return new URLSearchParams(seen.lastUrl.split("?")[1]);
}

const dlgOpen = () => env.byId("exportDlg").hasAttribute("open");

// ---- FW-8: the plot history seed (first: it needs a fresh page, state.maxId 0) -------------

const SESSION_ROW = { id: 5, ts: 1000, port: "p1", chan: "debug", raw: "hello" };
const CHANNEL = { name: "v", port: "p1", sid: null, kind: "analog", last_ts: 999, count: 2 };
const points = (a, b) => ({ points: [{ line_id: 1, ts: 998, tick_ms: 10, value: a },
                                     { line_id: 2, ts: 999, tick_ms: 20, value: b }] });
const frame = (sock, rows) => sock.onmessage({ data: JSON.stringify(rows) });
let sock = null;

function seedRoute(series) {
  const held = holdOn((u) => u.startsWith("/plot/series"), () => ok(series.shift()));
  const hold = route;
  route = (u, opt) => {
    if (u.startsWith("/lines?match=")) return ok({ lines: [] });
    if (u.startsWith("/lines")) return ok({ lines: [SESSION_ROW] });
    if (u.startsWith("/plot/channels")) return ok({ channels: [CHANNEL] });
    return hold(u, opt);
  };
  return held;
}

async function freshPage(series) {
  const held = seedRoute(series);
  state.maxId = 0;   // a first connect: the backfill seeds the charts
  connectWs();
  sock = env.sockets.at(-1);
  sock.onopen();
  await settle();
  assert.equal(held.length, 1, "the page-load seed is out");
  return held;
}

test("FW-8: a clear-all while the page-load seed is out leaves the charts empty", async () => {
  P.clearAllCharts();
  const held = await freshPage([points(1, 2)]);
  P.clearAllCharts();   // terminal.js clear-all
  D.clearAllDigital();
  held.splice(0)[0]();
  await settle();
  route = null;
  assert.equal(P.charts.size, 0, "the cleared history came back");
});

// The reset half needs no check of its own: a capture token arriving while the seed is out is
// staged until the backfill, seed included, has landed.
test("FW-8: a capture reset during the page-load seed waits for it, then plots the new capture", async () => {
  P.clearAllCharts();
  const held = await freshPage([points(1, 2), points(7, 8)]);
  frame(sock, [{ capture: "cap-a" }, { capture: "cap-b" }]);
  await settle();
  assert.equal(held.length, 1, "the reset ran while the old seed was still out");
  held[0]();
  await settle();
  assert.equal(held.length, 2, "the reset's own seed is out");
  held[1]();
  await settle();
  route = null;
  assert.deepEqual(P.charts.get("p1|adhoc")?.ys.get("v"), [7, 8]);
});

// ---- FW-1: an Export pending when its dialog closes ----------------------------------------

const SESSIONS = { sessions: [{ id: 7, name: "run-b", lines: 5, started_ts: 3000, ended_ts: null, auto: false }] };
const closes = {
  Cancel: () => env.byId("expCancel").emit("click", {}),
  "the x": () => env.byId("expClose").emit("click", {}),
  Escape: () => env.byId("exportDlg").emit("cancel", { preventDefault() {} }),
};

for (const [how, close] of Object.entries(closes)) {
  test(`FW-1: ${how} ends an Export waiting on the session list; the next dialog is its own`, async () => {
    const held = holdOn((u) => u.startsWith("/sessions"), () => ok(SESSIONS));
    const built = [];
    openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                       build: () => { built.push("A"); return null; } });
    env.byId("expGo").emit("click", {});
    await settle();
    close();
    openExportDialog({ kind: "can", watermark: null, shown: null, options: [],
                       build: () => { built.push("B"); return null; } });
    await settle();
    assert.equal(env.byId("expGo").disabled, false, "the closed dialog's Export holds the next one's button");
    env.byId("expGo").emit("click", {});   // B's own Export, waiting on B's list
    await settle();
    held[0]();                             // A's list answers
    await settle();
    assert.deepEqual(built, [], "the closed dialog's Export ran");
    assert.equal(dlgOpen(), true, "the closed dialog's Export closed the next one");
    assert.equal(env.byId("expGo").disabled, true, "the closed Export released B's pending button");
    held[1]();
    await settle();
    route = null;
    assert.deepEqual(built, ["B"], "B's own Export still goes");
    assert.equal(dlgOpen(), false);
  });
}

test("FW-1: an Export pending when Cancel closed the dialog does nothing once the list answers", async () => {
  const held = holdOn((u) => u.startsWith("/sessions"), () => ok(SESSIONS));
  const built = [];
  openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                     build: () => { built.push("A"); return null; } });
  env.byId("expGo").emit("click", {});
  await settle();
  env.byId("expCancel").emit("click", {});
  held[0]();
  await settle();
  route = null;
  assert.deepEqual(built, [], "Cancel did not end the pending Export");
});

test("FW-1: a download answering after Cancel neither reports into nor releases the next dialog", async () => {
  setToken("tok");   // a token fetches the export, so its answer comes back to the dialog
  const refused = { ok: false, status: 400, headers: { get: () => null }, json: async () => ({ error: "refused" }) };
  const held = holdOn((u) => u.includes("/export"), (u) => (u.startsWith("/lines") ? refused : ok({})));
  const inner = route;
  route = (u, opt) => (u.startsWith("/sessions") ? ok(SESSIONS) : inner(u, opt));
  openExportDialog({ kind: "lines", watermark: null, shown: null, options: [],
                     build: () => "/lines/export?format=text" });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.equal(held.length, 1);
  env.byId("expCancel").emit("click", {});
  openExportDialog({ kind: "plot", watermark: null, shown: null, options: [],
                     build: () => "/plot/export?names=a&format=long" });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.equal(held.length, 2, "B's download is out");
  held[0]();   // A's refusal
  await settle();
  assert.equal(env.byId("expErr").textContent, "", "A's refusal landed in B");
  assert.equal(dlgOpen(), true);
  assert.equal(env.byId("expGo").disabled, true, "A's end released B's button while B's download is out");
  held[1]();
  await settle();
  route = null;
  setToken(null);
  assert.equal(dlgOpen(), false, "B's own download closes B");
});

test("FW-1: a session list that never answers leaves Export usable over the whole capture", async () => {
  const realTimeout = AbortSignal.timeout;
  const armed = [];
  AbortSignal.timeout = (ms) => { const ac = new AbortController(); armed.push({ ms, ac }); return ac.signal; };
  route = (u, opt) => (u.startsWith("/sessions")
    ? new Promise((_, rej) => opt.signal?.addEventListener("abort", () => rej(opt.signal.reason)))
    : undefined);
  env.localStorage.setItem("mcuscope.exportRange",
    JSON.stringify({ mode: "session", session: "4", fromTs: null, toTs: null }));
  const built = [];
  openExportDialog({ kind: "can", watermark: null, shown: null, options: [],
                     build: (p) => { built.push(p.get("session")); return null; } });
  env.byId("expGo").emit("click", {});
  await settle();
  assert.deepEqual(built, [], "exported before the list or its deadline");
  assert.deepEqual(armed.map((a) => a.ms), [STATUS_TIMEOUT_MS], "the list fetch has no deadline");
  assert.ok(STATUS_TIMEOUT_MS < 5000);
  armed[0].ac.abort(new DOMException("timed out", "TimeoutError"));
  await settle();
  AbortSignal.timeout = realTimeout;
  route = null;
  assert.deepEqual(built, [null], "the whole capture, as the select then offers");
  assert.deepEqual(env.byId("expSession").children.map((o) => o.textContent), ["whole capture"]);
  assert.equal(env.byId("expErr").textContent, "could not list sessions: no reply from daemon");
  assert.equal(env.byId("expGo").disabled, false);
});

// ---- FW-2: tick mode's shown window is the samples drawn -----------------------------------

// The daemon's selection over `rows`: ts > since_ts, ts <= until_ts, id > since_id, id <= id_to,
// each bound only when given.
function selected(rows, q) {
  const bound = (k, dflt) => (q.has(k) ? Number(q.get(k)) : dflt);
  const s = bound("since_ts", -Infinity), u = bound("until_ts", Infinity);
  const sinceId = bound("since_id", -Infinity), idTo = bound("id_to", Infinity);
  return rows.filter((r) => r.ts > s && r.ts <= u && r.id > sinceId && r.id <= idTo).map((r) => r.id);
}

// 100 Hz by the MCU clock, which runs 1 percent slow against host time (an STM32 on HSI), plus
// any latency `lag(i)` adds.
function hsiStream(sid, n, value, lag = () => 0) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const tick = 10000 + i * 10;
    rows.push({ ...ingest(`!ps ${sid} ${tick.toString(16)} ${value(i)}`, 500 + i * 0.0101 + lag(i)), tick });
  }
  return rows;
}

test("FW-2: a paused chart in tick mode exports exactly the samples it draws", async () => {
  F.pauseAll(false);
  P.clearAllCharts();
  ingest("!pd 1 v:u2", 1);
  const rows = hsiStream(1, 4000, () => "0001");
  const chart = P.charts.get("p1|s1");
  chart.window = 30;
  P.setChartPaused(chart, true);
  state.timeMode = "tick";
  const q = await exportShown(() => P.exportChart(chart));
  state.timeMode = "host";
  const lastTick = rows.at(-1).tick;
  const drawn = rows.filter((r) => r.tick >= lastTick - 30000).map((r) => r.id);
  assert.equal(drawn.length, 3001);
  assert.deepEqual(selected(rows, q), drawn);
  P.setChartPaused(chart, false);
});

// b0 toggles every 7 samples; b1 only 99 samples either side of the window's left edge, with a
// 1 s latency step between each of b1's vertices and the edge: only b0's vertices bracket it
// tightly enough to read the host time there.
test("FW-2: paused lanes in tick mode export the samples inside their tick window", async () => {
  F.pauseAll(false);
  D.clearAllDigital();
  ingest("!pd 2 f:u1:/b0,b1", 1);
  const rows = hsiStream(2, 4000, (i) => "0" + ((Math.floor(i / 7) % 2) | (i >= 900 && i < 1100 ? 2 : 0)),
                         (i) => (i >= 950 ? 1 : 0) + (i >= 1050 ? 1 : 0));
  D.setDigitalPaused(true);
  state.timeMode = "tick";
  const q = await exportShown(D.exportDigital);
  state.timeMode = "host";
  D.setDigitalPaused(false);
  const lastTick = rows.at(-1).tick;
  const drawn = rows.filter((r) => r.tick >= lastTick - 30000).map((r) => r.id);
  assert.deepEqual(selected(rows, q), drawn);
});

test("FW-2: lanes shorter than their tick window export from their first sample", async () => {
  F.pauseAll(false);
  D.clearAllDigital();
  ingest("!pd 3 f:u1:/b0", 1);
  const rows = hsiStream(3, 100, (i) => "0" + (Math.floor(i / 7) % 2));
  D.setDigitalPaused(true);
  state.timeMode = "tick";
  const q = await exportShown(D.exportDigital);
  state.timeMode = "host";
  D.setDigitalPaused(false);
  assert.equal(q.get("since_id"), String(rows[0].id - 1));
  assert.deepEqual(selected([{ id: 1, ts: 1 }, ...rows], q), rows.map((r) => r.id), "the `!pd` line is not a sample");
});

// ---- FW-3: the CAN filter ------------------------------------------------------------------

test("FW-3: a filtered paused table's shown window starts at the oldest row it shows", async () => {
  C.setCanPaused(false);
  C.setCanFilter("");
  C.clearAllCan();
  const frame = (raw, ts) => { state.maxId = ++nextId; C.canIngest({ id: nextId, ts, port: "p1", chan: "event", raw }); };
  frame("!can 1 - 7DF 01", 1000);   // an hour before, hidden by the filter
  let last = 0;
  for (let i = 0; i < 50; i++) frame(`!can ${i} - 100 AA`, (last = 4600 + i * 0.1));
  C.setCanFilter("100");
  C.setCanPaused(true);
  const q = await exportShown(() => env.byId("canExport").emit("click"));
  C.setCanPaused(false);
  C.setCanFilter("");
  assert.equal(q.get("id"), "100");
  assert.equal(q.get("since_id"), String(nextId - 1), "the window reached back to the hidden 7DF frame");
  assert.equal(q.has("since_ts"), false);
});

// ---- FW-4: the pane's first row id ---------------------------------------------------------

test("FW-4: a paused pane's shown window sends its first row's id, exclusive", async () => {
  const pane = makePane({ port: "p1", autoscroll: false, frozenId: 77 });
  // One burst stamped once: the rows before id 40 share its ts but were cleared from the pane.
  pane.rows = [makeRow(40, { ts: 1000 }), { chan: "gap", ts: 999, raw: "gap" }, makeRow(41, { ts: 1000 }),
               makeRow(45, { ts: 1002.5 })];
  const q = await exportShown(() => exportPane(pane));
  assert.equal(q.get("since_id"), "39");
  assert.equal(q.get("since_ts"), String(1000 - 1e-6));
  assert.equal(q.get("until_ts"), "1002.5");
  env.byId("expModeSession").emit("change");
  assert.deepEqual(seen.refusals, []);

  seen.lastUrl = null;
  exportPane(pane);
  env.byId("expModeClock").emit("change");
  env.byId("expGo").emit("click");
  await settle();
  assert.equal(new URLSearchParams(seen.lastUrl.split("?")[1]).has("since_id"), false,
    "the first row bounds the shown window only");
  env.byId("expModeSession").emit("change");
});

// ---- FW-5, FW-6: Settings -------------------------------------------------------------------

let pjState = { enabled: false, dest: "127.0.0.1:9870" };
let configDown = false;
let putAnswer = { ok: true, restart_required: false };
const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  ports: [], update: { check: false },
};

function settingsRoute(u, opt) {
  const method = opt.method || "GET";
  if (u === "/config" && method === "GET") return configDown ? Promise.reject(new TypeError("Failed to fetch")) : ok(CONFIG);
  if (u.startsWith("/config/") && method === "PUT") return ok(putAnswer);
  if (u === "/plotjuggler" && method === "PUT") {
    const body = JSON.parse(opt.body);
    return ok({ enabled: body.enabled, dest: body.dest || "127.0.0.1:9870" });
  }
  if (u === "/plotjuggler") return ok(pjState);
  if (u.startsWith("/devices")) return ok({ devices: [] });
  if (u.startsWith("/status")) return ok({});
  return undefined;
}

// Requests of `key` ("GET /plotjuggler") wait; `refuse` makes a held PUT fail instead.
function holdSettings(keys) {
  const held = [];
  route = (u, opt) => {
    const key = `${opt.method || "GET"} ${u}`;
    if (keys.has(key)) return new Promise((r, rej) => held.push({ key, go: () => r(settingsRoute(u, opt)), fail: rej }));
    return settingsRoute(u, opt);
  };
  return held;
}

initSettings();
async function openSettings() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
}

test("FW-5: the PlotJuggler GET answering after open does not overwrite a control changed meanwhile", async () => {
  pjState = { enabled: false, dest: "127.0.0.1:9870" };
  env.byId("cfgPjDest").value = "";
  env.byId("cfgPjEnabled").checked = false;
  let held = holdSettings(new Set(["GET /plotjuggler"]));
  await openSettings();
  env.byId("cfgPjDest").value = "10.0.0.5:9870";
  env.byId("cfgPjEnabled").checked = true;
  held.splice(0).forEach((h) => h.go());
  await settle();
  assert.equal(env.byId("cfgPjDest").value, "10.0.0.5:9870", "typing replaced by the late answer");
  assert.equal(env.byId("cfgPjEnabled").checked, true, "a tick undone by the late answer");

  pjState = { enabled: true, dest: "192.168.1.2:9870" };
  held = holdSettings(new Set());
  await openSettings();
  route = null;
  assert.equal(env.byId("cfgPjDest").value, "192.168.1.2:9870", "an untouched field shows the daemon's state");
  assert.equal(env.byId("cfgPjEnabled").checked, true);
});

test("FW-5: PUT answers out of order leave the box as the last change set it", async () => {
  route = settingsRoute;
  await openSettings();
  env.byId("cfgPjDest").value = "";
  const held = holdSettings(new Set(["PUT /plotjuggler"]));
  env.byId("cfgPjEnabled").checked = true;
  env.byId("cfgPjEnabled").emit("change", {});
  await settle();
  env.byId("cfgPjEnabled").checked = false;
  env.byId("cfgPjEnabled").emit("change", {});
  await settle();
  const [first, second] = held.splice(0);
  second.go(); await settle();
  first.go(); await settle();
  route = null;
  assert.equal(env.byId("cfgPjEnabled").checked, false, "the stale answer re-ticked a stream turned off");
});

test("FW-5: a refused PUT's re-sync does not overwrite a change made while it is out", async () => {
  const refused = { ok: false, status: 400, headers: { get: () => null }, json: async () => ({ error: "bad dest" }) };
  route = settingsRoute;
  await openSettings();
  for (const changed of [false, true]) {
    pjState = { enabled: false, dest: "127.0.0.1:9870" };
    const held = holdSettings(new Set(["GET /plotjuggler"]));
    const inner = route;
    route = (u, opt) => (opt.method === "PUT" && u === "/plotjuggler" ? refused : inner(u, opt));
    env.byId("cfgPjEnabled").checked = true;
    env.byId("cfgPjEnabled").emit("change", {});   // refused; its re-sync GET is held
    await settle();
    assert.equal(held.length, 1);
    assert.equal(env.byId("cfgPjErr").textContent, "bad dest");
    // Untouched, the box takes the daemon's off; unticked while the GET is out, it keeps that
    // even when the answer (another client turned the stream on) says otherwise.
    if (changed) env.byId("cfgPjEnabled").checked = false;
    pjState = { enabled: changed, dest: "127.0.0.1:9870" };
    held[0].go();
    await settle();
    route = null;
    assert.equal(env.byId("cfgPjEnabled").checked, false,
      changed ? "the re-sync undid a change made while it was out" : "an untouched box is not re-synced");
  }
});

for (const [section, field, value, save] of [["Server", "cfgPort", "8600", "cfgServerSave"],
                                             ["Storage", "cfgDbPath", "/tmp/other.db", "cfgStorageSave"]]) {
  test(`FW-6: a ${section} save whose re-read fails still raises the restart badge`, async () => {
    route = settingsRoute;
    configDown = false;
    await openSettings();
    const badge = env.byId("restartBadge");
    for (const restart of [false, true]) {
      badge.hidden = true;
      putAnswer = { ok: true, restart_required: restart };
      configDown = true;
      env.byId(field).value = value;
      env.byId(save).emit("click", {});
      await settle();
      configDown = false;
      assert.equal(env.byId(`cfg${section}Err`).textContent, "saved; could not re-read the config");
      assert.equal(badge.hidden, !restart, `restart_required ${restart} from the PUT`);
    }
    route = null;
    putAnswer = { ok: true, restart_required: false };
  });
}
