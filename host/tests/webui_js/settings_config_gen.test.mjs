// settings.js R73-1: only the newest GET /config issued writes `cfg` and the restart badge, so
// an older read answering late cannot hide a restart the file now needs.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let restart = false;
const held = [];
let holdNextConfig = 0;
const CFG = (restart_required) => ({
  path: "/cfg/mcuscope.toml", exists: true, revision: "r1", restart_required, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  update: { check: true }, ports: [],
});
const ok = (b) => ({ ok: true, status: 200, json: async () => b });
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if (u === "/config") {
    const answer = ok(CFG(restart));   // the file as it is when the read is made
    if (holdNextConfig > 0) { holdNextConfig--; return new Promise((r) => held.push(() => r(answer))); }
    return answer;
  }
  if (u === "/devices") return ok({ devices: [] });
  if (u.startsWith("/sessions")) return ok({ sessions: [] });
  if (u === "/status") return ok({ db_content_bytes: 0, db_size_bytes: 0 });
  if (u === "/plotjuggler") return ok({ enabled: false, dest: "127.0.0.1:9870" });
  if ((opt.method || "GET") === "PUT") return ok({ ok: true, revision: "r2" });
  return ok({});
};

const settle = async () => { for (let i = 0; i < 8; i++) await tick(0); };
// Every value the badge takes, so a hide that a later read puts right again still shows.
const badgeEl = env.byId("restartBadge");
let badgeHidden = true;
const badgeLog = [];
Object.defineProperty(badgeEl, "hidden", {
  get: () => badgeHidden, set: (v) => { badgeHidden = !!v; badgeLog.push(!!v); },
});
const badge = () => !badgeHidden;

holdNextConfig = 1;
const { initSettings, saveAttachedPortToConfig } = await import(webuiUrl("settings.js"));
initSettings();   // its badge prime is the read held above: the file needed no restart then

test("a prime answering after the dialog's newer read leaves that read's badge", async () => {
  assert.equal(held.length, 1, "setup: the prime is out");
  restart = true;   // the file changes to need a restart
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(badge(), true, "positive control: the dialog's read raised the badge");
  held.shift()();
  await settle();
  assert.equal(badge(), true, "the older prime hid a restart the file needs");
});

test("an attach save's read answering late does not undo a newer read either", async () => {
  while (held.length) held.shift()();   // the prime, when this test runs alone
  await settle();
  restart = false;
  badgeEl.hidden = true;
  holdNextConfig = 1;
  const save = saveAttachedPortToConfig("b", "/dev/ttyACM0", 115200, "lf", "");
  await settle();
  restart = true;
  env.byId("setClose").emit("click", {});
  env.byId("settingsDlg").removeAttribute("open");
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(badge(), true);
  badgeLog.length = 0;
  held.shift()();
  await save;
  await settle();
  assert.deepEqual(badgeLog.filter((h) => h), [], "the attach's older read hid the badge");
  assert.equal(badge(), true);
});
