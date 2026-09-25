// settings.js: the Storage fields take config.py's bounds, the ones the loader and PUT
// /config/storage share (D-17): each field's floor, and 2**63 - 1 above. A narrower dialog
// bound refused every Storage save once the file held a wider value, even a save of another
// field (R71-1).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let storage;
const CONFIG = () => ({
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage,
  ports: [],
  update: { check: false },
});

const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  if ((opt.method || "GET") === "PUT") puts.push({ url: String(url), body: JSON.parse(opt.body) });
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG() };
};

const { initSettings } = await import(webuiUrl("settings.js"));
initSettings();

const MB = 1024 * 1024;
const MAX_MB = 2 ** 43 - 1;   // the largest whole MB below 2**63 bytes

async function openFresh(over = {}) {
  storage = { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
              auto_session: false, ...over };
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  puts.length = 0;
}

async function save() {
  env.byId("cfgStorageSave").emit("click", {});
  await tick(0);
  await tick(0);
  return puts.find((p) => p.url === "/config/storage");
}

test("a loaded value past the old dialog bounds does not block a save of another field", async () => {
  await openFresh({ retention_days: 5000, min_sessions: 2000 });
  env.byId("cfgMinSessions").value = "3000";
  const put = await save();
  assert.equal(env.byId("cfgStorageErr").textContent, "");
  assert.ok(put, "editing Keep newest sessions did not save");
  assert.equal(put.body.retention_days, 5000);
  assert.equal(put.body.min_sessions, 3000);
});

test("a size cap at 2**63 bytes is refused by name, and saves nothing", async () => {
  await openFresh();
  env.byId("cfgMaxDb").value = String(MAX_MB + 1);
  assert.equal(await save(), undefined, "the dialog must not send a value the daemon answers 422 for");
  assert.equal(env.byId("cfgStorageErr").textContent, "Size cap must be a whole number of MB, 0 for none");
});

test("the largest cap below 2**63 bytes saves", async () => {
  await openFresh();
  env.byId("cfgMaxDb").value = String(MAX_MB);
  const put = await save();
  assert.ok(put, "the bound is inclusive");
  assert.equal(put.body.max_db_bytes, MAX_MB * MB);
});

for (const [id, value, text] of [
  ["cfgRetention", String(2 ** 63), "Retention must be a whole number of days, 1 or more"],
  ["cfgMinSessions", "9223372036854775807", "Keep newest sessions must be a whole number, 0 or more"],
]) {
  test(`${id} at 2**63 is refused by name: a double rounds 2**63 - 1 up to it`, async () => {
    await openFresh();
    env.byId(id).value = value;
    assert.equal(await save(), undefined);
    assert.equal(env.byId("cfgStorageErr").textContent, text);
  });
}

test("positive control: the largest double below 2**63 saves as retention", async () => {
  await openFresh();
  env.byId("cfgRetention").value = "9223372036854774784";   // 2**63 - 1024
  const put = await save();
  assert.ok(put);
  assert.ok(put.body.retention_days < 2 ** 63);
});
