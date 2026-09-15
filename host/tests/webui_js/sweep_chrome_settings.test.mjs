// Class 73 sweep, settings.js: an awaited answer is not written into a Settings dialog replaced
// while it was out (reopened, re-rendered, or edited), and an older of two overlapping reads
// never overwrites the newer one.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
globalThis.confirm = () => true;

const MIB = 1024 * 1024;
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
const fail = (status, error) => ({ ok: false, status, headers: { get: () => null }, json: async () => ({ error }) });

// A daemon keeping the revision contract, whose answers can be held and released one by one.
// A released call is answered from the state at release time unless an answer is given.
let d;
const puts = [];
const held = [];
let holdRules = [];
function reset() {
  d = {
    rev: 1, configDown: false,
    config: {
      path: "/cfg/mcuscope.toml", exists: true, restart_required: false, token_set: false,
      server: { host: "127.0.0.1", port: 8558 },
      storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
      ports: [], update: { check: true },
    },
    status: { version: "0.5.0", db_size_bytes: 0, db_content_bytes: 0, update: null },
    devices: [], pj: { enabled: false, dest: "127.0.0.1:9870" }, pjRefuse: false,
  };
  puts.length = 0; held.length = 0; holdRules = [];
}
function holdNext(pred, n = 1) { holdRules.push({ pred, n }); }
async function release(pred, answer) {
  const i = held.findIndex((h) => pred(h.u, h.method));
  assert.ok(i >= 0, "setup: no such request is held");
  held.splice(i, 1)[0].go(answer);
  await settle();
}

function answer(u, method, opt) {
  if (method === "PUT" && u.startsWith("/config/")) {
    const body = JSON.parse(opt.body);
    puts.push({ url: u, body });
    if (body.revision !== undefined && body.revision !== `r${d.rev}`) {
      return fail(409, "config file changed since it was read; reload it and try again");
    }
    const section = u.slice("/config/".length);
    const { revision: _r, ...fields } = body;
    if (section === "ports") d.config.ports = fields.ports;
    else if (section !== "plotjuggler") d.config[section] = { ...d.config[section], ...fields };
    d.rev++;
    return ok({ ok: true, restart_required: false, revision: `r${d.rev}` });
  }
  if (u === "/config") {
    if (d.configDown) throw new TypeError("Failed to fetch");
    return ok(structuredClone({ ...d.config, revision: `r${d.rev}` }));
  }
  if (u === "/plotjuggler") {
    if (method === "PUT") {
      if (d.pjRefuse) return fail(422, "dest: refused");
      const b = JSON.parse(opt.body);
      d.pj = { enabled: b.enabled, dest: b.dest || d.pj.dest };
    }
    return ok({ ...d.pj });
  }
  if (u.startsWith("/status")) return ok({ ...d.status });
  if (u.startsWith("/devices")) return ok({ devices: d.devices });
  if (method === "DELETE") return ok({ ok: true });
  if (u.startsWith("/sessions")) {
    return ok({ sessions: [{ id: 2, name: "r", started_ts: 1, ended_ts: 2, lines: 3, auto: false }] });
  }
  return ok({});
}

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  const method = opt.method || "GET";
  const rule = holdRules.find((r) => r.n > 0 && r.pred(u, method));
  if (rule) {
    rule.n--;
    const given = await new Promise((go) => held.push({ u, method, go }));
    if (given instanceof Error) throw given;
    if (given) return given;
  }
  return answer(u, method, opt);
};

const { initSettings } = await import(webuiUrl("settings.js"));
reset();
initSettings();

async function settle() { for (let i = 0; i < 12; i++) await tick(0); }
async function open() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
}
async function reopen() {
  env.byId("setClose").emit("click", {});
  await open();
}
const click = async (id) => { env.byId(id).emit("click", {}); await settle(); };
const isPut = (section) => (u, m) => m === "PUT" && u === `/config/${section}`;
const isStatus = (u) => u.startsWith("/status");

// ---- section saves --------------------------------------------------------------------

test("a save landing after a reopen adopts no revision: the reopened fields cannot overwrite it", async () => {
  reset();
  await open();
  env.byId("cfgPort").value = "9000";
  holdNext(isPut("server"));
  await click("cfgServerSave");
  await reopen();   // its GET is answered before the PUT is applied: port 8558 at r1
  await release(isPut("server"));
  assert.equal(String(env.byId("cfgPort").value), "8558",
               "the late save re-rendered a dialog its own GET had rendered");
  assert.equal(env.byId("cfgServerSave").textContent, "Save",
               "the late save marked the freshly opened section unsaved");
  env.byId("cfgHost").value = "0.0.0.0";
  await click("cfgServerSave");
  assert.equal(puts.at(-1).body.revision, "r1", "the reopened dialog adopted the late save's revision");
  assert.match(env.byId("cfgServerErr").textContent, /reopen Settings/);
  assert.equal(d.config.server.port, 9000, "the first save was overwritten by fields read before it");
});

test("a save refused after a reopen writes its refusal nowhere", async () => {
  reset();
  await open();
  env.byId("cfgHost").value = "bad host";
  holdNext(isPut("server"));
  await click("cfgServerSave");
  await reopen();
  await release(isPut("server"), fail(422, "host: not a bind address"));
  assert.equal(env.byId("cfgServerErr").textContent, "");
});

test("a storage save landing after a reopen does not move the reopened cap's saved bytes", async () => {
  reset();
  d.config.storage.max_db_bytes = 1.5 * MIB;   // renders as 2 MB, sent back as these bytes
  await open();
  env.byId("cfgMaxDb").value = "5";
  holdNext(isPut("storage"));
  await click("cfgStorageSave");
  await reopen();
  await release(isPut("storage"));
  d.rev = 1;   // let the reopened dialog's save through, to read the bytes it sends
  await click("cfgStorageSave");
  assert.equal(puts.at(-1).body.max_db_bytes, 1.5 * MIB);
});

test("a port typed while the server save was out is kept, and still reads unsaved", async () => {
  reset();
  await open();
  env.byId("cfgPort").value = "9000";
  holdNext(isPut("server"));
  await click("cfgServerSave");
  env.byId("cfgPort").value = "9001";
  env.byId("cfgSecServer").emit("input", {});
  await release(isPut("server"));
  assert.equal(puts[0].body.port, 9000);
  assert.equal(String(env.byId("cfgPort").value), "9001", "the re-render overwrote the typing");
  assert.equal(env.byId("cfgServerSave").textContent, "Save *");
});

test("typing kept when the re-read after a save fails still reads unsaved", async () => {
  reset();
  await open();
  env.byId("cfgPort").value = "9000";
  holdNext(isPut("server"));
  await click("cfgServerSave");
  env.byId("cfgPort").value = "9001";
  d.configDown = true;
  await release(isPut("server"));
  assert.equal(env.byId("cfgServerErr").textContent, "saved; could not re-read the config");
  assert.equal(env.byId("cfgServerSave").textContent, "Save *",
               "9001 was never sent, but was marked as saved");
});

// ---- /status hints --------------------------------------------------------------------

async function openWithStatusHeld() {
  holdNext(isStatus, 2);   // renderDbNow, then renderUpdateNow
  await open();
  assert.equal(held.length, 2, "setup: the open's two /status reads are not both held");
}

test("an older /status read does not overwrite 'checks are off' after checks were saved off", async () => {
  reset();
  await openWithStatusHeld();
  env.byId("cfgUpdateCheck").checked = false;
  await click("cfgUpdateSave");
  const off = "checks are off; the daemon makes no outbound request";
  assert.equal(env.byId("cfgUpdateNow").textContent, off);
  await release(isStatus);
  await release(isStatus);
  assert.equal(env.byId("cfgUpdateNow").textContent, off);
});

test("an older failed /status read does not blank 'checks are off'", async () => {
  reset();
  await openWithStatusHeld();
  env.byId("cfgUpdateCheck").checked = false;
  await click("cfgUpdateSave");
  await release(isStatus, new TypeError("Failed to fetch"));
  await release(isStatus, new TypeError("Failed to fetch"));
  assert.equal(env.byId("cfgUpdateNow").textContent, "checks are off; the daemon makes no outbound request");
});

test("the cap hint keeps the newest capture size when an older read lands after it", async () => {
  reset();
  await openWithStatusHeld();
  d.status.db_content_bytes = 2 * MIB;
  await click("cfgStorageSave");   // re-renders Storage, whose read answers at once
  assert.equal(env.byId("cfgDbNow").textContent, "0 = no cap; now 2.0 MB");
  await release(isStatus, ok({ db_content_bytes: 1 * MIB, config_warnings: ["stale warning"] }));
  assert.equal(env.byId("cfgDbNow").textContent, "0 = no cap; now 2.0 MB");
  assert.equal(env.byId("cfgWarnings").hidden, true);
});

test("an older failed read does not reset the cap hint the newer read wrote", async () => {
  reset();
  await openWithStatusHeld();
  d.status.db_content_bytes = 2 * MIB;
  await click("cfgStorageSave");
  await release(isStatus, new TypeError("Failed to fetch"));
  assert.equal(env.byId("cfgDbNow").textContent, "0 = no cap; now 2.0 MB");
});

test("a read from an earlier open does not list warnings in the read-only dialog", async () => {
  reset();
  await openWithStatusHeld();
  env.byId("setClose").emit("click", {});
  d.configDown = true;
  await open();
  assert.match(env.byId("cfgPath").textContent, /read-only/);
  await release(isStatus, ok({ db_content_bytes: 0, config_warnings: ["stale warning"] }));
  assert.equal(env.byId("cfgWarnings").hidden, true);
  assert.equal(env.byId("cfgWarnings").children.length, 0);
});

// ---- PlotJuggler ------------------------------------------------------------------------

const isPjGet = (u, m) => u === "/plotjuggler" && m === "GET";
async function toggle(on) {
  env.byId("cfgPjEnabled").checked = on;
  env.byId("cfgPjEnabled").emit("change", {});
  await settle();
}

test("the open's GET answered after two toggles does not put back the state from before them", async () => {
  reset();
  env.byId("cfgPjEnabled").checked = false;
  d.pj.enabled = true;   // enabled elsewhere; the open's GET leaves with that
  holdNext(isPjGet);
  await open();
  await toggle(true);
  await toggle(false);
  assert.equal(d.pj.enabled, false);
  await release(isPjGet, ok({ enabled: true, dest: "127.0.0.1:9870" }));
  assert.equal(env.byId("cfgPjEnabled").checked, false, "the checkbox shows a stream the daemon is not running");
});

test("the open's GET failing after a toggle writes no error", async () => {
  reset();
  holdNext(isPjGet);
  await open();
  await toggle(true);
  await release(isPjGet, fail(500, "plotjuggler: stale read failed"));
  assert.equal(env.byId("cfgPjErr").textContent, "");
});

test("a refused toggle superseded by a newer one shows no refusal", async () => {
  reset();
  holdNext((u, m) => u === "/plotjuggler" && m === "PUT");
  await open();
  await toggle(true);
  await toggle(false);
  await release((u, m) => u === "/plotjuggler" && m === "PUT", fail(422, "dest: stale refusal"));
  assert.equal(env.byId("cfgPjErr").textContent, "");
});

test("the re-sync after a refusal does not undo two toggles made while it was out", async () => {
  reset();
  env.byId("cfgPjEnabled").checked = false;
  await open();
  d.pjRefuse = true;
  holdNext(isPjGet);
  await toggle(true);   // refused; its re-sync GET is held
  d.pjRefuse = false;
  await toggle(false);
  await toggle(true);
  assert.equal(d.pj.enabled, true);
  await release(isPjGet, ok({ enabled: false, dest: "127.0.0.1:9870" }));
  assert.equal(env.byId("cfgPjEnabled").checked, true);
});

test("a PlotJuggler default save refused after a reopen writes its refusal nowhere", async () => {
  reset();
  await open();
  holdNext(isPut("plotjuggler"));
  await click("cfgPjSave");
  await reopen();
  await release(isPut("plotjuggler"), fail(500, "config save failed: disk full"));
  assert.equal(env.byId("cfgPjErr").textContent, "");
});

// ---- opening ----------------------------------------------------------------------------

test("an overlapping open renders from its own config and devices, not the earlier open's", async () => {
  reset();
  d.config.server.host = "a.example";
  const oldConfig = ok({ ...structuredClone(d.config), revision: "r1" });
  const oldDevices = ok({ devices: [{ device: "/dev/A" }] });
  holdNext((u) => u === "/config");
  holdNext((u) => u.startsWith("/devices"), 2);
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  d.config.server.host = "b.example";
  d.devices = [{ device: "/dev/B" }];
  env.byId("settingsBtn").emit("click", {});
  await settle();
  await release((u) => u === "/config", oldConfig);   // lands between the second open's two reads
  const firstDevices = held.splice(held.findIndex((h) => h.u.startsWith("/devices")), 1)[0];
  await release((u) => u.startsWith("/devices"));   // the second open's, which then renders
  assert.equal(env.byId("cfgHost").value, "b.example");
  firstDevices.go(oldDevices);   // the first open's lands last
  await settle();
  env.byId("cfgPortAdd").emit("click", {});
  const rows = env.byId("cfgPortsBody").children;
  const options = rows.at(-1)._fields.devSel.children.map((o) => o.value);
  assert.deepEqual(options, ["/dev/B", "custom"]);
});

test("a session delete refused after a reopen writes its refusal nowhere", async () => {
  reset();
  await open();
  holdNext((u, m) => m === "DELETE");
  const row = env.byId("cfgSessionsBody").children[0];
  row.children[3].children[2].emit("click", {});   // delete
  await settle();
  await reopen();
  await release((u, m) => m === "DELETE", fail(500, "database is locked"));
  assert.equal(env.byId("cfgSessionsErr").textContent, "");
});
