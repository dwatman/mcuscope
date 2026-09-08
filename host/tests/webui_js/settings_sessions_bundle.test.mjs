// settings.js: each session row offers export (db), bundle (zip) and delete, and each
// button fetches its own path. A shared handler would have both downloads hit one endpoint.

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

const SESSIONS = {
  sessions: [{ id: 7, name: "run 1", note: null, started_ts: 1700000000, ended_ts: 1700000060,
               auto: false, lines: 3 }],
  active: null,
};

const gets = [];
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  gets.push(u);
  if (u.includes("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  if (u.includes("/sessions")) return { ok: true, status: 200, json: async () => SESSIONS };
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));

function rowButtons() {
  return env.byId("cfgSessionsBody").querySelectorAll("button");
}

test("open the dialog", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  await tick(0);
});

test("a session row offers export, bundle and delete, in that order", () => {
  assert.deepEqual(rowButtons().map((b) => b.textContent), ["export", "bundle", "delete"]);
});

test("the bundle button downloads the zip endpoint, not the db export", async () => {
  gets.length = 0;
  rowButtons().find((b) => b.textContent === "bundle").emit("click", {});
  await tick(0);
  assert.ok(gets.some((u) => u.includes("/sessions/7/bundle")), "fetched the bundle path");
  assert.ok(!gets.some((u) => u.includes("/sessions/7/export")), "and not the db export");
});
