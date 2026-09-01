// settings.js: the storage size cap needs both bounds, like the two fields beside it.
//
// retention_days and min_sessions each name their full range in the dialog's error slot; the
// size cap named only "0 or more MB", so a value above ConfigStorageBody's 2**42 byte bound
// went to the daemon and came back as a raw 422 after a round trip.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
             auto_session: false },
  ports: [],
  update: { check: false },
};

const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  if ((opt.method || "GET") === "PUT") puts.push({ url: String(url), body: JSON.parse(opt.body) });
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));

const MAX_MB = 2 ** 42 / (1024 * 1024);   // 4194304 MB, the daemon's max_db_bytes in MB

test("open the dialog", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
});

test("a size cap above the daemon's bound is refused by name, and saves nothing", async () => {
  puts.length = 0;
  env.byId("cfgMaxDb").value = String(MAX_MB + 1);
  env.byId("cfgStorageSave").emit("click", {});
  await tick(0);
  assert.equal(puts.length, 0, "the dialog must not send a value the daemon answers 422 for");
  assert.match(env.byId("cfgStorageErr").textContent, new RegExp(`0-${MAX_MB} MB`),
    "the refusal names the range, as retention and sessions do");
});

test("the bound itself saves", async () => {
  puts.length = 0;
  env.byId("cfgMaxDb").value = String(MAX_MB);
  env.byId("cfgStorageSave").emit("click", {});
  await tick(0);
  await tick(0);
  const put = puts.find((p) => p.url.includes("/config/storage"));
  assert.ok(put, "the bound is inclusive");
  assert.equal(put.body.max_db_bytes, 2 ** 42);
});
