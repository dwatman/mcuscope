// settings.js: opened against an unreachable daemon, Settings is read-only except the token.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, webuiDir, tick } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/home/u/.config/mcuscope/config.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  ports: [], update: { check: true },
};

let down = true;
const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  if (down) throw new TypeError("Failed to fetch");
  const u = String(url);
  if ((opt.method || "GET") === "PUT") puts.push(u);
  if (u.includes("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  if (u.includes("/sessions")) return { ok: true, status: 200, json: async () => ({ sessions: [] }) };
  if (u.includes("/status")) return { ok: true, status: 200, json: async () => ({ db_size_bytes: 0 }) };
  if (u.includes("/plotjuggler")) return { ok: true, status: 200, json: async () => ({ enabled: false, dest: "" }) };
  return { ok: true, status: 200, json: async () => CONFIG };
};
let confirms = 0;
globalThis.confirm = () => { confirms += 1; return false; };

const { initSettings, dirtySections } = await import(webuiUrl("settings.js"));
const dlg = env.byId("settingsDlg");
const DAEMON_CONTROLS = ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgPjSave", "cfgPortsSave", "cfgPortAdd"];

test("DAEMON_CONTROLS is every save and add button in the page but the token's", () => {
  const html = readFileSync(webuiDir() + "index.html", "utf8");
  const derived = [...html.matchAll(/id="(cfg\w+(?:Save|Add))"/g)].map((m) => m[1]).filter((id) => id !== "cfgTokenSave");
  assert.ok(derived.length >= 6, derived.join());
  assert.deepEqual(derived.sort(), [...DAEMON_CONTROLS].sort());
});
const disabled = () => DAEMON_CONTROLS.filter((id) => env.byId(id).disabled);

async function open() {
  env.byId("settingsBtn").emit("click", {});
  for (let i = 0; i < 4; i++) await tick(0);
}
async function close() {
  env.byId("setClose").emit("click", {});
}

initSettings();

// Open the dialog fresh with the daemon up or down.
async function openWith(isDown) {
  down = isDown;
  dlg.removeAttribute("open");
  await open();
}

test("daemon down: read-only, said so in the fixed banner, no focus move, token still saves", async () => {
  let focused = false;
  env.byId("cfgToken").focus = () => { focused = true; };
  await openWith(true);
  assert.equal(dlg.hasAttribute("open"), true);
  assert.equal(env.byId("cfgOffline").hidden, false, "the banner stayed hidden");
  assert.equal(env.byId("cfgOffline").textContent,
    "daemon unreachable: settings are read-only; the access token still works");
  assert.equal(env.byId("cfgPath").textContent, "", "the notice was left in the scrolling body");
  assert.deepEqual(disabled(), DAEMON_CONTROLS);
  assert.equal(env.byId("cfgTokenSave").disabled, false);
  assert.equal(focused, false, "a late answer moved the caret to the token box");
});

test("daemon down: an edit to a daemon section is not an unsaved change, a token edit is", async () => {
  await openWith(true);
  env.byId("cfgHost").value = "0.0.0.0";
  env.byId("cfgSecServer").emit("input", {});
  assert.deepEqual(dirtySections(), []);
  assert.equal(env.byId("cfgServerSave").textContent, "Save", "a Save that cannot be pressed is not lit");
  env.byId("cfgToken").value = "tok";
  env.byId("cfgSecToken").emit("input", {});
  assert.deepEqual(dirtySections(), ["Access token"]);
  confirms = 0;
  await close();
  assert.equal(confirms, 1);
  env.byId("cfgToken").value = "";
  env.byId("cfgSecToken").emit("input", {});
  await close();
  assert.equal(dlg.hasAttribute("open"), false);
});

test("daemon back: the next open is editable again", async () => {
  await openWith(true);
  env.byId("cfgHost").value = "0.0.0.0";   // offline typing, discarded on close
  env.byId("cfgSecServer").emit("input", {});
  await close();
  assert.equal(dlg.hasAttribute("open"), false);
  await openWith(false);
  assert.deepEqual(disabled(), []);
  assert.equal(env.byId("cfgPath").textContent, CONFIG.path);
  assert.equal(env.byId("cfgHost").value, "127.0.0.1", "the discarded offline typing is replaced");
  await close();
});

test("daemon down after a good load: the stale config does not make it editable", async () => {
  await openWith(false);
  assert.deepEqual(disabled(), [], "the good load did not happen");
  await close();
  down = true;
  await open();
  assert.deepEqual(disabled(), DAEMON_CONTROLS,
    "a failed fetch must not fall back to the config the previous open loaded");
  assert.match(env.byId("cfgOffline").textContent, /^daemon unreachable/);
  env.byId("cfgHost").value = "0.0.0.0";
  env.byId("cfgSecServer").emit("input", {});
  assert.deepEqual(dirtySections(), [], "an edit that cannot be saved is not held against closing");
  confirms = 0;
  await close();
  assert.equal(confirms, 0);
});
