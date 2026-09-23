// settings.js sessions list: a session name and note are user text, so they go through
// state.js userText: a bidi override shows as <U+202E> instead of turning `gpj.exe` into a
// displayed `exe.jpg`, in the list, the note's tooltip and the delete confirmation.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const NAME = "\u202egpj.exe";
const isolated = "\u2068<U+202E>gpj.exe\u2069";

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "/tmp/db", retention_days: 7, max_db_bytes: 0, min_sessions: 1,
             auto_session: false },
  ports: [{ alias: "b1", device: "\u202egpj.exe", baud: 115200, autoconnect: true }],
  update: { check: false },
};
const SESSIONS = {
  // Ended and named: no tag span, since the stub drops an element's own text on appendChild.
  sessions: [{ id: 7, name: NAME, note: "see\u2066 log", started_ts: 1700000000, ended_ts: 1700000060,
               auto: false, lines: 3 }],
  active: null,
};
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.includes("/devices")) {
    return { ok: true, status: 200,
             json: async () => ({ devices: [{ device: NAME, by_id: null, description: "x\u200fy" }] }) };
  }
  if (u.includes("/sessions")) return { ok: true, status: 200, json: async () => SESSIONS };
  return { ok: true, status: 200, json: async () => CONFIG };
};

const { initSettings } = await import(webuiUrl("settings.js"));
initSettings();

async function openSettings() {
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  for (let i = 0; i < 3; i++) await tick(0);
}

test("the name cell, its note and the delete confirmation show the override", async () => {
  await openSettings();
  const cell = env.byId("cfgSessionsBody").querySelectorAll("td")[0];
  assert.ok(cell, "no session row rendered");
  assert.equal(cell.textContent, isolated);
  assert.equal(cell.title, "\u2068see<U+2066> log\u2069");

  let asked = null;
  globalThis.confirm = (msg) => { asked = msg; return false; };
  env.byId("cfgSessionsBody").querySelectorAll("button").find((b) => b.textContent === "delete")
    .emit("click", {});
  assert.ok(asked && asked.startsWith(`Delete "${isolated}" and its 3 captured lines?`), asked);
});

test("the ports list's device dropdown shows the override, and keeps the device as its value", async () => {
  await openSettings();
  const sel = env.byId("cfgPortsBody").querySelectorAll("tr")[0]._fields.devSel;
  const opt = sel.children[0];
  assert.equal(opt.textContent, `${isolated}  -  \u2068x<U+200F>y\u2069`);
  assert.equal(opt.value, NAME);
  assert.equal(sel.value, NAME, "the saved row still matches its device");
});
