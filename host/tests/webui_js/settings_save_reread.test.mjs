// settings.js: a Storage save must not re-round a cap it did not edit, a save
// whose re-read fails must not render the stale config as clean, a stalled daemon must still
// open the dialog, and a late answer must not overwrite typing or duplicate a fill.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const MiB = 1024 * 1024;

let config;
function freshConfig(over = {}) {
  config = {
    path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
    server: { host: "127.0.0.1", port: 8558 },
    storage: { db_path: "", retention_days: 7, max_db_bytes: 1500000, min_sessions: 1,
               auto_session: true },
    ports: [{ alias: "keep", device: "/dev/ttyACM0", baud: 921600, eol: "lf",
              autoconnect: true, identify: true }],
    update: { check: false },
    ...over,
  };
}
freshConfig();

const puts = [];
let configDown = false;          // GET /config fails (the daemon went away after a PUT)
let stall = null;                // a path prefix that never answers unless aborted
const held = [];                 // [{path, release(body)}] for paths in `hold`
const hold = new Set();
let sessions = [{ id: 1, name: "a", started_ts: 1, ended_ts: 2, lines: 3, auto: false }];

function neverUnlessAborted(opt) {
  return new Promise((_, rej) => {
    if (opt.signal) opt.signal.addEventListener("abort", () => rej(opt.signal.reason));
  });
}
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  const method = opt.method || "GET";
  if (stall && u.startsWith(stall)) return neverUnlessAborted(opt);
  const answer = () => {
    if (method === "PUT") {
      const body = JSON.parse(opt.body);
      puts.push({ url: u, body });
      if (u === "/plotjuggler") return ok({ enabled: body.enabled, dest: body.dest || "127.0.0.1:9870" });
      return ok({ ok: true });
    }
    if (u === "/config") {
      if (configDown) throw new TypeError("Failed to fetch");
      return ok(config);
    }
    if (u.startsWith("/devices")) return ok({ devices: [] });
    if (u.startsWith("/sessions")) return ok({ sessions });
    if (u.startsWith("/status")) return ok({ db_size_bytes: 0, db_content_bytes: 0 });
    if (u.startsWith("/plotjuggler")) return ok({ enabled: false, dest: "127.0.0.1:9870" });
    return ok({});
  };
  const key = `${method} ${u.split("?")[0]}`;
  if (hold.has(key)) {
    return new Promise((res, rej) => held.push({ key, release: () => res(answer()), fail: rej }));
  }
  return answer();
};

const { hooks } = await import(webuiUrl("state.js"));
const { initSettings, saveAttachedPortToConfig } = await import(webuiUrl("settings.js"));
initSettings();

const reported = [];
hooks.reportError = (m) => reported.push(m);

async function settle() { for (let i = 0; i < 6; i++) await tick(0); }
async function open() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
}
const put = (path) => puts.filter((p) => p.url === path).at(-1)?.body;
const rows = () => env.byId("cfgPortsBody").querySelectorAll("tr");

// ---- E-1: the size cap round-trips its loaded bytes ---------------------------------------

test("E-1: saving another Storage field sends a non-whole-MiB cap back unchanged", async () => {
  for (const bytes of [1500000, 1600000, 1048577, 2 ** 42 - 1]) {
    freshConfig({ storage: { ...config.storage, max_db_bytes: bytes } });
    await open();
    puts.length = 0;
    env.byId("cfgRetention").value = "11";
    env.byId("cfgStorageSave").emit("click", {});
    await settle();
    assert.equal(put("/config/storage").max_db_bytes, bytes,
      `a cap of ${bytes} bytes shown as ${env.byId("cfgMaxDb").value} MB was re-rounded`);
  }
});

test("E-1: a cap the user did edit is sent as whole MB, including 0 and a return to the shown value", async () => {
  freshConfig();
  await open();
  assert.equal(String(env.byId("cfgMaxDb").value), "1", "1500000 renders as 1 MB");
  puts.length = 0;
  env.byId("cfgMaxDb").value = "3";
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  assert.equal(put("/config/storage").max_db_bytes, 3 * MiB);

  await open();   // config still says 1500000
  env.byId("cfgMaxDb").value = "0";
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  assert.equal(put("/config/storage").max_db_bytes, 0, "0 is no cap, not the loaded bytes");
});

test("E-1: after a save whose re-read failed, the shown MB is the one just saved", async () => {
  freshConfig();
  await open();
  env.byId("cfgMaxDb").value = "3";
  configDown = true;
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  env.byId("cfgMaxDb").value = "1";   // back to what the stale config rendered
  puts.length = 0;
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  configDown = false;
  assert.equal(put("/config/storage").max_db_bytes, MiB,
    "1 typed over a saved 3 MB means 1 MB; the 1500000 it rendered from is no longer saved");
});

// ---- E-2: a failed re-read after a successful PUT ----------------------------------------

test("E-2: Ports save with a failed re-read keeps the typed baud, says so, and the next save keeps it", async () => {
  freshConfig();
  await open();
  rows()[0]._fields.baudInput.value = "57600";
  env.byId("cfgSecPorts").emit("input", {});
  assert.equal(env.byId("cfgPortsSave").textContent, "Save *");
  configDown = true;
  env.byId("cfgPortsSave").emit("click", {});
  await settle();
  assert.equal(rows()[0]._fields.baudInput.value, "57600", "the stale 921600 was rendered back");
  assert.equal(env.byId("cfgPortsErr").textContent, "saved; could not re-read the config");
  assert.equal(env.byId("cfgPortsSave").textContent, "Save", "what is shown is what was saved");
  puts.length = 0;
  env.byId("cfgPortsSave").emit("click", {});
  await settle();
  configDown = false;
  assert.equal(put("/config/ports").ports[0].baud, 57600);
});

test("E-2: Server and Updates saves take the same path", async () => {
  freshConfig();
  await open();
  env.byId("cfgPort").value = "9000";
  env.byId("cfgUpdateCheck").checked = true;
  configDown = true;
  env.byId("cfgServerSave").emit("click", {});
  env.byId("cfgUpdateSave").emit("click", {});
  await settle();
  configDown = false;
  assert.equal(env.byId("cfgPort").value, "9000");
  assert.equal(env.byId("cfgServerErr").textContent, "saved; could not re-read the config");
  assert.equal(env.byId("cfgUpdateCheck").checked, true);
  assert.equal(env.byId("cfgUpdateErr").textContent, "saved; could not re-read the config");
});

test("E-2: a refused PUT still shows the refusal, not the saved note", async () => {
  freshConfig();
  await open();
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opt = {}) => ((opt.method || "GET") === "PUT"
    ? { ok: false, status: 422, json: async () => ({ error: "port out of range" }) } : real(url, opt));
  env.byId("cfgPort").value = "9001";
  env.byId("cfgSecServer").emit("input", {});
  env.byId("cfgServerSave").emit("click", {});
  await settle();
  globalThis.fetch = real;
  assert.equal(env.byId("cfgServerErr").textContent, "port out of range");
  assert.equal(env.byId("cfgServerSave").textContent, "Save *", "a refused edit is still unsaved");
});

test("E-2: attach's save to config does not report a failure when only the re-read fails", async () => {
  freshConfig();
  reported.length = 0;
  puts.length = 0;
  let gets = 0;
  const real = globalThis.fetch;
  globalThis.fetch = async (url, opt = {}) => {
    if (String(url) === "/config" && (opt.method || "GET") === "GET" && ++gets > 1) {
      throw new TypeError("Failed to fetch");
    }
    return real(url, opt);
  };
  await saveAttachedPortToConfig("brd", "COM7", 115200, "crlf", "");
  globalThis.fetch = real;
  assert.ok(put("/config/ports"), "the PUT went out");
  assert.deepEqual(reported, [], "a landed save was reported as failed");

  globalThis.fetch = async (url, opt = {}) => ((opt.method || "GET") === "PUT"
    ? { ok: false, status: 500, json: async () => ({ error: "disk full" }) } : real(url, opt));
  await saveAttachedPortToConfig("brd", "COM7", 115200, "crlf", "");
  globalThis.fetch = real;
  assert.deepEqual(reported, ["save to config failed: disk full"], "a refused PUT is still reported");
});

// ---- E-6: a stalled daemon ---------------------------------------------------------------

test("E-6: against a daemon that never answers, Settings opens at once, loading, then read-only at the deadline", async () => {
  const realTimeout = AbortSignal.timeout;
  const armed = [];
  AbortSignal.timeout = (ms) => { const ac = new AbortController(); armed.push({ ms, ac }); return ac.signal; };
  stall = "/";
  const dlg = env.byId("settingsDlg");
  dlg.removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(dlg.hasAttribute("open"), true, "the dialog waited for the daemon before opening");
  assert.equal(env.byId("cfgPath").textContent, "loading...");
  assert.equal(env.byId("cfgPortsSave").disabled, true, "a save is possible before the config landed");
  assert.ok(armed.length >= 1 && armed.every((a) => a.ms > 0 && a.ms < 5000),
    "the deadline must be under the 5 s poll interval");
  for (const a of armed) a.ac.abort(new DOMException("timed out", "TimeoutError"));
  await settle();
  stall = null;
  AbortSignal.timeout = realTimeout;
  assert.equal(dlg.hasAttribute("open"), true, "the dialog never opened against a stalled daemon");
  assert.match(env.byId("cfgOffline").textContent, /daemon unreachable: settings are read-only/);
  assert.equal(env.byId("cfgPortsSave").disabled, true);
});

// ---- overlapping opens and fills --------------------------------------------------------

test("two clicks before /config answers open the dialog once and list each session once", async () => {
  freshConfig();
  sessions = [{ id: 1, name: "a", started_ts: 1, ended_ts: 2, lines: 3, auto: false },
              { id: 2, name: "b", started_ts: 3, ended_ts: null, lines: 4, auto: true }];
  const dlg = env.byId("settingsDlg");
  let shows = 0;
  dlg.showModal = function () { shows++; this.attrs.set("open", ""); };
  dlg.removeAttribute("open");
  hold.add("GET /config");
  env.byId("settingsBtn").emit("click", {});
  env.byId("settingsBtn").emit("click", {});
  await settle();
  hold.delete("GET /config");
  for (const h of held.splice(0)) h.release();
  await settle();
  delete dlg.showModal;
  assert.equal(shows, 1, "a superseded open still called showModal");
  assert.equal(env.byId("cfgSessionsBody").children.length, 2);
});

test("a Storage save while the open's session list is loading lists each session once", async () => {
  freshConfig();
  hold.add("GET /sessions");
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  hold.delete("GET /sessions");
  const pending = held.splice(0);
  assert.equal(pending.length, 2, "both fills are in flight");
  for (const h of pending) h.release();
  await settle();
  assert.equal(env.byId("cfgSessionsBody").children.length, sessions.length);
});

test("a superseded session fill that fails late leaves the newer list and no error", async () => {
  freshConfig();
  hold.add("GET /sessions");
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  hold.delete("GET /sessions");
  const [older, newer] = held.splice(0);
  newer.release();
  await settle();
  older.fail(new TypeError("Failed to fetch"));
  await settle();
  assert.equal(env.byId("cfgSessionsBody").children.length, sessions.length);
  assert.equal(env.byId("cfgSessionsErr").textContent, "", "a stale failure beside a good list");
});

test("the positive control: the current session fill failing does write cfgSessionsErr", async () => {
  freshConfig();
  hold.add("GET /sessions");
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  hold.delete("GET /sessions");
  const pending = held.splice(0);
  assert.equal(pending.length, 1);
  pending[0].fail(new TypeError("Failed to fetch"));
  await settle();
  assert.notEqual(env.byId("cfgSessionsErr").textContent, "", "a failed fill shows nothing here");
});

// ---- class 61: the PlotJuggler answer and a dest typed during the PUT --------------------

test("class 61: the PUT /plotjuggler answer does not overwrite a dest typed while it was out", async () => {
  freshConfig();
  await open();
  env.byId("cfgPjDest").value = "";
  env.byId("cfgPjEnabled").checked = true;
  hold.add("PUT /plotjuggler");
  env.byId("cfgPjEnabled").emit("change", {});
  await settle();
  env.byId("cfgPjDest").value = "10.0.0.5:9870";
  hold.delete("PUT /plotjuggler");
  for (const h of held.splice(0)) h.release();
  await settle();
  assert.equal(env.byId("cfgPjDest").value, "10.0.0.5:9870", "the late answer ate the typing");
  assert.equal(env.byId("cfgPjEnabled").checked, true);
});

test("class 61: an untouched blank dest still shows the daemon's kept destination", async () => {
  freshConfig();
  await open();
  env.byId("cfgPjDest").value = "  ";
  env.byId("cfgPjEnabled").checked = true;
  env.byId("cfgPjEnabled").emit("change", {});
  await settle();
  assert.equal(env.byId("cfgPjDest").value, "127.0.0.1:9870");
});

// ---- sweep-stage ruling: open from the click, fill when answered --------------------------

test("Settings closed before /config answers stays closed and unfilled when the answer lands", async () => {
  freshConfig();
  const dlg = env.byId("settingsDlg");
  dlg.removeAttribute("open");
  hold.add("GET /config");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(dlg.hasAttribute("open"), true, "the dialog waited for /config before opening");
  assert.equal(env.byId("cfgServerSave").disabled, true, "a save is live before the config landed");
  env.byId("setClose").emit("click", {});
  assert.equal(dlg.hasAttribute("open"), false);
  hold.delete("GET /config");
  for (const h of held.splice(0)) h.release();
  await settle();
  assert.equal(dlg.hasAttribute("open"), false, "the late answer reopened Settings");
  assert.equal(env.byId("cfgPath").textContent, "loading...", "the late answer filled a closed dialog");
  await open();
  assert.equal(env.byId("cfgServerSave").disabled, false, "positive control: a fresh open fills");
  assert.notEqual(env.byId("cfgPath").textContent, "loading...");
});
