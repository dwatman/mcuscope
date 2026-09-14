// settings.js: a saved port's line ending is editable (SPEC 9.1 "fully configurable from the
// browser"), plus the Settings copy each refusal and status line uses.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  ports: [{ alias: "crlfb", device: "/dev/ttyACM0", baud: 115200, eol: "crlf", autoconnect: true, identify: true },
          { alias: "raw", device: "/dev/ttyUSB0", baud: 9600, eol: "none", autoconnect: false, identify: false },
          { alias: "odd", device: "/dev/ttyUSB1", baud: 9600, eol: "CRLF", autoconnect: false, identify: true }],
  update: { check: true },
};

const puts = [];
let status = { db_size_bytes: 20 * 1024 * 1024, db_content_bytes: 9 * 1024 * 1024, lines_trimmed: 0 };
let sessions = [];
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "PUT") {
    puts.push({ url: u, body: JSON.parse(opt.body) });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  if (u.includes("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  if (u.includes("/sessions")) return { ok: true, status: 200, json: async () => ({ sessions }) };
  if (u.includes("/status")) return { ok: true, status: 200, json: async () => status };
  if (u.includes("/plotjuggler")) return { ok: true, status: 200, json: async () => ({ enabled: false, dest: "" }) };
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));
const rows = () => env.byId("cfgPortsBody").querySelectorAll("tr");
async function settle() { for (let i = 0; i < 4; i++) await tick(0); }

test("each row's EOL select shows the saved value; an unknown one reads as lf", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.deepEqual(rows().map((tr) => tr._fields.eolSel.value), ["crlf", "none", "lf"]);
  assert.deepEqual(rows()[0]._fields.eolSel.children.map((o) => o.value), ["lf", "crlf", "none"]);
});

test("a save sends every row's eol, so a picked value lands and an untouched one is kept", async () => {
  rows()[2]._fields.eolSel.value = "crlf";
  env.byId("cfgPortAdd").emit("click", {});
  const added = rows()[3]._fields;
  added.aliasInput.value = "new";
  added.devSel.value = "custom";
  added.devCustom.value = "socket://127.0.0.1:9900";
  puts.length = 0;
  env.byId("cfgPortsSave").emit("click", {});
  await settle();
  const put = puts.find((p) => p.url === "/config/ports");
  assert.ok(put, "nothing was saved");
  assert.deepEqual(Object.fromEntries(put.body.ports.map((p) => [p.alias, p.eol])),
    { crlfb: "crlf", raw: "none", odd: "crlf", new: "lf" });
});

// The hint's figure is the one the cap is enforced against (db_content_bytes, SPEC 3.4); the
// file on disk keeps freed pages after a trim, so it is only the title's aside.
test("storage: the cap hint carries the current content, file size and trimmed lines go in its title", async () => {
  assert.equal(env.byId("cfgDbNow").textContent, "0 = no cap; now 9.0 MB");
  assert.equal(env.byId("cfgDbNow").title, "Past the cap the oldest lines are trimmed; 20 MB on disk");
  status = { db_size_bytes: 5 * 1024 * 1024, db_content_bytes: 1024, lines_trimmed: 42 };
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(env.byId("cfgDbNow").textContent, "0 = no cap; now 1.0 kB");
  assert.equal(env.byId("cfgDbNow").title,
    "Past the cap the oldest lines are trimmed; 5.0 MB on disk; 42 trimmed so far");
});

test("the update line says a check has not run, without guessing at an env var", () => {
  assert.equal(env.byId("cfgUpdateNow").textContent, "not checked yet in this daemon run");
});

test("the empty sessions row names the control that exists", () => {
  const td = env.byId("cfgSessionsBody").children[0].children[0];
  assert.equal(td.textContent, "no sessions yet: the session button in the status bar starts one");
});

test("each refusal names the field by its label", async () => {
  const refusal = async (id, value, save, errId) => {
    const keep = env.byId(id).value;
    env.byId(id).value = value;
    puts.length = 0;
    env.byId(save).emit("click", {});
    await settle();
    const text = env.byId(errId).textContent;
    env.byId(id).value = keep;
    assert.equal(puts.length, 0, `${id} refused yet saved`);
    return text;
  };
  assert.equal(await refusal("cfgHost", "  ", "cfgServerSave", "cfgServerErr"), "Bind host is required");
  assert.equal(await refusal("cfgPort", "0", "cfgServerSave", "cfgServerErr"), "Port must be 1-65535");
  assert.equal(await refusal("cfgRetention", "0", "cfgStorageSave", "cfgStorageErr"), "Retention must be 1-3650 days");
  assert.equal(await refusal("cfgMaxDb", "-1", "cfgStorageSave", "cfgStorageErr"), "Size cap must be 0-4194304 MB");
  assert.equal(await refusal("cfgMinSessions", "1001", "cfgStorageSave", "cfgStorageErr"), "Keep newest sessions must be 0-1000");
});
