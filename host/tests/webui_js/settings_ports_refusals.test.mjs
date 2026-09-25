// settings.js, the Ports section. R12-1: a failed GET /devices is said in the section, not
// shown as an empty device list. O-70a: a saved port whose alias was cleared is refused by
// name instead of being deleted by the save; only an untouched "+ port" row is dropped.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let devices = { ok: true, status: 200, json: async () => ({ devices: [{ device: "/dev/ttyACM0" }] }) };
const puts = [];
const ok = (b) => ({ ok: true, status: 200, json: async () => b });
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "PUT") { puts.push({ url: u, body: JSON.parse(opt.body) }); return ok({ ok: true }); }
  if (u === "/devices") return devices;
  if (u === "/config") {
    return ok({ path: "/c.toml", exists: true, revision: "r1", restart_required: false, token_set: false,
                server: { host: "127.0.0.1", port: 8558 },
                storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
                update: { check: true },
                ports: [{ alias: "keep", device: "/dev/ttyACM0", baud: 115200, eol: "lf", autoconnect: true,
                          identify: true }] });
  }
  if (u.startsWith("/sessions")) return ok({ sessions: [] });
  if (u === "/status") return ok({ db_content_bytes: 0, db_size_bytes: 0 });
  return ok({ enabled: false, dest: "127.0.0.1:9870" });
};

const { initSettings } = await import(webuiUrl("settings.js"));
initSettings();
const settle = async () => { for (let i = 0; i < 8; i++) await tick(0); };
const err = () => env.byId("cfgPortsErr").textContent;
const rows = () => env.byId("cfgPortsBody").querySelectorAll("tr");

async function open() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  puts.length = 0;
}
async function save() { env.byId("cfgPortsSave").emit("click", {}); await settle(); }

test("R12-1: a /devices refusal is named in the Ports section", async () => {
  devices = { ok: false, status: 500, json: async () => ({ error: "enumeration crashed" }) };
  await open();
  assert.equal(err(), "could not list devices: enumeration crashed");
  devices = { ok: true, status: 200, json: async () => ({ devices: [{ device: "/dev/ttyACM0" }] }) };
  await open();
  assert.equal(err(), "", "a good answer leaves no note");
});

test("O-70a: clearing a saved port's alias refuses the save, naming the port", async () => {
  await open();
  rows()[0]._fields.aliasInput.value = "";
  env.byId("cfgSecPorts").emit("input", {});
  assert.equal(env.byId("cfgPortsSave").textContent, "Save *", "a cleared alias is an unsaved edit");
  await save();
  assert.deepEqual(puts, [], "the save deleted the port");
  assert.equal(err(), 'port "⁨keep⁩": the alias is empty; use remove to delete a port');
});

test("O-70a: an untouched + port row is dropped; one typed into without an alias is refused", async () => {
  await open();
  env.byId("cfgPortAdd").emit("click", {});
  assert.equal(env.byId("cfgPortsSave").textContent, "Save", "an untouched new row is no edit");
  await save();
  assert.deepEqual(puts.map((p) => p.body.ports.map((x) => x.alias)), [["keep"]],
                   "positive control: the save goes out without the blank row");
  await open();
  env.byId("cfgPortAdd").emit("click", {});
  rows()[1]._fields.snInput.value = "ABC123";
  env.byId("cfgSecPorts").emit("input", {});
  assert.equal(env.byId("cfgPortsSave").textContent, "Save *", "typing into a new row is an unsaved edit");
  await save();
  assert.deepEqual(puts, [], "a typed row was dropped for want of an alias");
  assert.equal(err(), "a new port row has no alias");
});

test("O-70a: remove is still the way to delete a saved port", async () => {
  await open();
  rows()[0].children[7].children[0].emit("click", {});
  await save();
  assert.deepEqual(puts.map((p) => p.body.ports), [[]]);
});
