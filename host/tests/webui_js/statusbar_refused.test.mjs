// statusbar.js pollStatus: a daemon that answers /status with a refusal is not "unreachable".
//
// Every failure of the poll painted the chip "daemon unreachable", and the command input with
// it. A daemon bound to the LAN with a token answers a page that has none with 401, and one
// locked out after wrong tokens with 429: it is answering, and saying "unreachable" sent the
// user to look for a dead process instead of at the token (REVIEW class 70).

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

let answer = null;   // {status, error} to refuse with, "down" for a network failure, else 200
globalThis.fetch = async () => {
  if (answer === "down") throw new TypeError("Failed to fetch");
  if (answer) {
    const { status, error } = answer;
    return { ok: false, status, headers: { get: () => null }, json: async () => ({ error }) };
  }
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ version: "0.1.0", uptime_s: 0, db_size_bytes: 0, ports: [],
                                write_errors: 0, session: null }) };
};

const { refreshStatus } = await import(webuiUrl("statusbar.js"));
const { userText } = await import(webuiUrl("state.js"));
// The reason is the daemon's text, shown as all outside text is (state.js userText).
const refused = (why) => "daemon refused: " + userText(why);
const text = (id) => env.byId(id).textContent;
const placeholder = () => env.byId("cmdInput").placeholder;

test("a 401 says the daemon refused, with its reason, not that it is unreachable", async () => {
  answer = { status: 401, error: "missing or invalid access token" };
  await refreshStatus();
  assert.equal(text("daemonVer"), refused("missing or invalid access token"));
  assert.equal(env.byId("daemonDot").className, "dot crit", "nothing it reports can be trusted");
  assert.equal(placeholder(), refused("missing or invalid access token"));
});

test("a 429 lockout says so too", async () => {
  answer = { status: 429, error: "too many failed token attempts; try again later" };
  await refreshStatus();
  assert.equal(text("daemonVer"), refused("too many failed token attempts; try again later"));
});

test("a network failure still reads unreachable, and an answer clears both", async () => {
  answer = "down";
  await refreshStatus();
  assert.equal(text("daemonVer"), "daemon unreachable");
  assert.equal(placeholder(), "daemon unreachable");
  answer = null;
  await refreshStatus();
  assert.doesNotMatch(placeholder(), /daemon/, "a healthy poll left the offline text behind");
});
