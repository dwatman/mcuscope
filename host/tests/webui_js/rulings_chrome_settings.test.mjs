// Owner rulings E-8, A-5 and E-9 in Settings (2026-09-15 pre-release): every config save sends
// the revision the dialog loaded and adopts the one each save answers, a file changed since is
// a 409 shown as the daemon wrote it over the typing, /status config_warnings are listed, and a
// session row's .db export checks the session before navigating.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, webuiDir, tick } from "./dom_stub.mjs";

const env = installDom();

const CHANGED = "config file changed since it was read; reload it and try again";

// A daemon keeping the revision contract: GET /config carries it, a PUT carrying a stale one is
// 409 and writes nothing, a PUT carrying none is not checked, and each accepted PUT answers the
// new one. `revisioned: false` is an older daemon with no revision anywhere.
let d;
function reset(over = {}) {
  d = {
    rev: 1, revisioned: true, warnings: undefined, statusDown: false, configDown: false,
    onGetConfig: null, onPut: null, holdName: null, sessionsByName: [{ id: 2, name: "r" }],
    config: {
      path: "/cfg/mcuscope.toml", exists: true, restart_required: false, token_set: false,
      server: { host: "127.0.0.1", port: 8558 },
      storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
      ports: [{ alias: "keep", device: "/dev/ttyACM0", baud: 921600, eol: "lf", autoconnect: true, identify: true }],
      update: { check: true },
    },
    ...over,
  };
  puts.length = 0; names.length = 0; navigations.length = 0; reported.length = 0;
}
const puts = [];
const names = [];
const navigations = [];
const reported = [];
const rev = () => (d.revisioned ? { revision: `r${d.rev}` } : {});
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
const fail = (status, body) => ({ ok: false, status, headers: { get: () => null }, json: async () => body });

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  const method = opt.method || "GET";
  if (method === "PUT" && u.startsWith("/config/")) {
    const body = JSON.parse(opt.body);
    puts.push({ url: u, body });
    if (d.onPut) { const r = d.onPut(u, body); if (r) return r; }
    if (d.revisioned && body.revision !== undefined && body.revision !== `r${d.rev}`) {
      return fail(409, { error: CHANGED });
    }
    if (u === "/config/server") d.config.server = { host: body.host, port: body.port };
    if (u === "/config/ports") d.config.ports = body.ports;
    d.rev++;
    return ok({ ok: true, restart_required: false, ...rev() });
  }
  if (u === "/config") {
    if (d.configDown) throw new TypeError("Failed to fetch");
    const answer = ok({ ...d.config, ...rev() });
    if (d.onGetConfig) d.onGetConfig();
    return answer;
  }
  if (u.startsWith("/status")) {
    if (d.statusDown) throw new TypeError("Failed to fetch");
    return ok({ db_size_bytes: 0, db_content_bytes: 0,
                ...(d.warnings === undefined ? {} : { config_warnings: d.warnings }) });
  }
  if (u.startsWith("/sessions?name=")) {
    names.push(u);
    const answer = ok({ sessions: d.sessionsByName, active: null });
    return d.holdName ? new Promise((r) => d.holdName.push(() => r(answer))) : answer;
  }
  if (u.startsWith("/sessions")) return ok({ sessions: [{ id: 2, name: "r", started_ts: 1, ended_ts: 2, lines: 3, auto: false }] });
  if (u.startsWith("/devices")) return ok({ devices: [] });
  if (u === "/plotjuggler") {
    if (method === "PUT") { const b = JSON.parse(opt.body); return ok({ enabled: b.enabled, dest: b.dest || "127.0.0.1:9870" }); }
    return ok({ enabled: false, dest: "127.0.0.1:9870" });
  }
  return ok({});
};

const create = env.document.createElement;
env.document.createElement = (tag) => {
  const el = create(tag);
  if (String(tag).toLowerCase() === "a") el.click = () => navigations.push(el.href);
  return el;
};

const { hooks, setToken } = await import(webuiUrl("state.js"));
const { initSettings, saveAttachedPortToConfig } = await import(webuiUrl("settings.js"));
reset();
initSettings();
hooks.reportError = (m) => reported.push(m);
setToken(null);

async function settle() { for (let i = 0; i < 8; i++) await tick(0); }
async function open() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
}
async function save(id) { env.byId(id).emit("click", {}); await settle(); }
const revisions = () => puts.map((p) => [p.url, p.body.revision]);

// ---- E-8 ---------------------------------------------------------------------------------

test("E-8: every section saved in a row sends the revision the previous save answered", async () => {
  const saves = ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgPortsSave", "cfgPjSave"];
  const html = readFileSync(webuiDir() + "index.html", "utf8");
  const derived = [...html.matchAll(/id="(cfg\w+Save)"/g)].map((m) => m[1]).filter((id) => id !== "cfgTokenSave");
  assert.ok(derived.length >= 5, derived.join());
  assert.deepEqual(derived.sort(), [...saves].sort(), "a section save this test does not click");
  reset();
  await open();
  for (const id of saves) await save(id);
  assert.deepEqual(revisions(), [["/config/server", "r1"], ["/config/storage", "r2"],
    ["/config/update", "r3"], ["/config/ports", "r4"], ["/config/plotjuggler", "r5"]]);
  for (const e of ["cfgServerErr", "cfgStorageErr", "cfgUpdateErr", "cfgPortsErr", "cfgPjErr"]) {
    assert.equal(env.byId(e).textContent, "", `${e} shows a refusal`);
  }
});

test("E-8: a file changed while the dialog is open is refused with the daemon's own text, typing kept", async () => {
  reset();
  await open();
  d.rev = 9;   // another tab or a hand edit
  env.byId("cfgHost").value = "0.0.0.0";
  env.byId("cfgSecServer").emit("input", {});
  await save("cfgServerSave");
  assert.equal(env.byId("cfgServerErr").textContent, CHANGED);
  assert.equal(env.byId("cfgHost").value, "0.0.0.0", "the refused save must not re-render the field");
  assert.equal(env.byId("cfgServerSave").textContent, "Save *", "the edit is still unsaved");
  assert.equal(d.config.server.host, "127.0.0.1", "nothing was written");
});

test("E-8: a change landing between two saves refuses the second, not the first", async () => {
  reset();
  await open();
  await save("cfgServerSave");
  assert.equal(env.byId("cfgServerErr").textContent, "");
  d.rev++;
  env.byId("cfgPortsBody").querySelectorAll("tr")[0]._fields.baudInput.value = "115200";
  await save("cfgPortsSave");
  assert.equal(env.byId("cfgPortsErr").textContent, CHANGED);
  assert.equal(env.byId("cfgPortsBody").querySelectorAll("tr")[0]._fields.baudInput.value, "115200");
  assert.equal(d.config.ports[0].baud, 921600);
});

test("E-8: the re-read after a save does not adopt a file written after that save", async () => {
  reset();
  await open();
  let bump = false;
  d.onGetConfig = () => { if (bump) { d.rev++; bump = false; } };
  d.onPut = () => { bump = true; };
  await save("cfgServerSave");   // answers r2; its re-read sees another writer's r3
  d.onPut = null;
  await save("cfgStorageSave");
  assert.deepEqual(revisions().at(-1), ["/config/storage", "r2"]);
  assert.equal(env.byId("cfgStorageErr").textContent, CHANGED,
    "the storage fields were rendered from r1, so r3 must not be overwritten from them");
});

test("E-8: a refusal other than 409 shows the daemon's own text too", async () => {
  reset({ onPut: () => fail(400, { error: "bad host" }) });
  await open();
  await save("cfgServerSave");
  assert.equal(env.byId("cfgServerErr").textContent, "bad host");
});

test("E-8: an older daemon without revision gets no revision field and saves twice", async () => {
  reset({ revisioned: false });
  await open();
  await save("cfgServerSave");
  await save("cfgStorageSave");
  assert.equal(puts.length, 2);
  for (const p of puts) assert.equal("revision" in p.body, false, `${p.url} sent a revision`);
  assert.equal(env.byId("cfgStorageErr").textContent, "");
});

test("E-8: the attach dialog's save to config sends the revision it just read", async () => {
  reset({ rev: 7 });
  await saveAttachedPortToConfig("brd", "COM7", 115200, "crlf", "");
  assert.deepEqual(revisions(), [["/config/ports", "r7"]]);
  assert.deepEqual(reported, []);

  let once = true;
  d.onGetConfig = () => { if (once) { d.rev++; once = false; } };   // a write between its GET and PUT
  await saveAttachedPortToConfig("brd2", "COM8", 115200, "", "");
  assert.deepEqual(reported, ["save to config failed: " + CHANGED]);
  assert.equal(d.config.ports.some((p) => p.alias === "brd2"), false);
});

// ---- A-5 ---------------------------------------------------------------------------------

const W = ["config: unknown key 'prot' in [server], ignored; did you mean 'port'?",
           "config: unknown key 'storge' in the config file, ignored; did you mean 'storage'?"];
const box = () => env.byId("cfgWarnings");
const shown = () => (box().hidden ? null : box().children.map((li) => li.textContent));

test("A-5: /status config_warnings are listed one per line", async () => {
  reset({ warnings: W });
  await open();
  assert.deepEqual(shown(), W);
});

test("A-5: an empty or absent config_warnings shows nothing", async () => {
  reset({ warnings: W });
  await open();
  d.warnings = [];
  await open();
  assert.equal(shown(), null);
  assert.equal(box().children.length, 0);
  d.warnings = W;
  await open();
  d.warnings = undefined;   // an older daemon
  await open();
  assert.equal(shown(), null);
  assert.ok(env.byId("cfgDbNow").textContent.startsWith("0 = no cap; now"),
    "an absent field must not fail the rest of the /status render");
});

test("A-5: warnings from an earlier open are cleared when /status or the daemon is down", async () => {
  reset({ warnings: W });
  await open();
  d.statusDown = true;
  await open();
  assert.equal(shown(), null, "a failed /status left the last warnings up");

  d.statusDown = false;
  await open();
  assert.deepEqual(shown(), W);
  d.configDown = true;
  await open();
  assert.equal(shown(), null, "the read-only dialog left the last warnings up");
});

// ---- E-9 from the sessions list -----------------------------------------------------------

const exportBtn = () => env.byId("cfgSessionsBody").children[0].children[3].children[0];

test("E-9: a session row's export of a deleted session is reported, not navigated", async () => {
  reset({ sessionsByName: [] });
  await open();
  exportBtn().emit("click", {});
  await settle();
  assert.deepEqual(reported, ["session export failed: no such session: 2"]);
  assert.deepEqual(navigations, []);
});

test("E-9: a double click on a session row's export checks and downloads once", async () => {
  reset({ holdName: [] });
  await open();
  const btn = exportBtn();
  btn.emit("click", {});
  btn.emit("click", {});
  await settle();
  assert.equal(names.length, 1);
  d.holdName.forEach((r) => r());
  await settle();
  assert.deepEqual(navigations, ["/sessions/2/export"]);
  assert.equal(btn.getAttribute("aria-disabled"), "true", "held after the navigation while the daemon builds the copy");
});

// ---- sweep-stage ruling: section saves are queued ------------------------------------------

test("two section saves fired inside one PUT's round trip: the second waits and sends the first's revision", async () => {
  reset();
  await open();
  const heldPuts = [];
  d.onPut = () => new Promise((r) => heldPuts.push(r));   // answered below, in order
  env.byId("cfgServerSave").emit("click", {});
  env.byId("cfgUpdateSave").emit("click", {});
  await settle();
  assert.equal(puts.length, 1, "the second save went out before the first was answered");
  d.onPut = null;
  d.rev++;
  heldPuts.shift()(ok({ ok: true, restart_required: false, revision: `r${d.rev}` }));
  await settle();
  assert.deepEqual(revisions(), [["/config/server", "r1"], ["/config/update", "r2"]]);
  assert.equal(env.byId("cfgUpdateErr").textContent, "", "the second save got a 409 of its own making");
});

test("a refused save does not stop the save queued behind it", async () => {
  reset();
  await open();
  let first = true;
  d.onPut = () => (first ? ((first = false), fail(500, { error: "disk full" })) : null);
  env.byId("cfgServerSave").emit("click", {});
  env.byId("cfgUpdateSave").emit("click", {});
  await settle();
  d.onPut = null;
  assert.equal(env.byId("cfgServerErr").textContent, "disk full");
  assert.deepEqual(puts.map((p) => p.url), ["/config/server", "/config/update"],
    "the save behind a refused one never went out");
});
