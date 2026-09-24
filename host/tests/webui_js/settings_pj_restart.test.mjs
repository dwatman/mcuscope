// settings.js: late PlotJuggler answers (FW-5), and the restart badge after a failed re-read
// (FW-6).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";
import { installExportDaemon } from "./exportdlg_guards.mjs";

const env = installDom();
installExportDaemon(env);

// `route` answers a request first; returning undefined falls through to the export double.
const daemon = globalThis.fetch;
let route = null;
globalThis.fetch = async (url, opt = {}) => {
  const r = route && route(String(url), opt);
  return (r && (await r)) || daemon(url, opt);
};
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null },
                        json: async () => body, blob: async () => new Blob(["x"]) });

const { initSettings } = await import(webuiUrl("settings.js"));

async function settle() { for (let i = 0; i < 10; i++) await tick(0); }

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
