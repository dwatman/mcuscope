// state.js: a 401 on a background request (the 5 s status poll) opens no token prompt. A
// window.prompt takes the keys of whatever is being typed, a marker say, and could store them
// as the token. The token badge shows instead; a click on it, or the next request a user action
// makes, asks.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
const S = await import(webuiUrl("state.js"));
const badge = env.byId("tokenBadge");

let prompts = 0, answer = null;
globalThis.prompt = () => { prompts += 1; return answer; };
let accept = null;   // the token the daemon takes
let locked = false;  // the token guard's lockout (server.py _deny_rate_limited): 429 for everyone
globalThis.fetch = async (path, opt) => {
  if (locked) return { ok: false, status: 429, json: async () => ({ error: "too many failed tokens" }) };
  const auth = (opt.headers || {}).Authorization;
  const ok = accept !== null && auth === "Bearer " + accept;
  return { ok, status: ok ? 200 : 401, json: async () => (ok ? {} : { error: "unauthorized" }) };
};

function fresh() {
  S.setToken(null);
  S.resetTokenPrompt();
  prompts = 0; answer = null; accept = "sekrit"; locked = false;
}

test("a background 401 shows the badge and prompts nothing", async () => {
  fresh();
  await assert.rejects(S.api("GET", "/status", undefined, undefined, { background: true }), /unauthorized/);
  assert.equal(prompts, 0, "the poll opened a prompt over whatever the user was typing");
  assert.equal(badge.hidden, false);
});

test("the next user request prompts, and its success hides the badge", async () => {
  fresh();
  await assert.rejects(S.api("GET", "/status", undefined, undefined, { background: true }));
  answer = "sekrit";
  await S.api("POST", "/marker", { text: "x" });
  assert.equal(prompts, 1);
  assert.equal(S.getToken(), "sekrit");
  assert.equal(badge.hidden, true);
});

test("a click on the badge prompts, even after an earlier cancel", async () => {
  fresh();
  answer = null;
  await assert.rejects(S.api("POST", "/marker", { text: "x" }));   // the user cancels
  assert.equal(prompts, 1);
  assert.equal(badge.hidden, false, "a cancelled prompt leaves the way back in");
  answer = "sekrit";
  badge.emit("click");
  assert.equal(prompts, 2);
  assert.equal(S.getToken(), "sekrit");
  await S.api("GET", "/status", undefined, undefined, { background: true });
  assert.equal(badge.hidden, true);
});

test("the token guard's 429 lockout leaves the badge showing; the next success hides it", async () => {
  fresh();
  await assert.rejects(S.api("GET", "/status", undefined, undefined, { background: true }), /unauthorized/);
  assert.equal(badge.hidden, false, "setup: the stored token was refused");
  locked = true;
  await assert.rejects(S.api("GET", "/status", undefined, undefined, { background: true }), /too many/);
  assert.equal(badge.hidden, false, "the lockout hid the badge the stream warning points at");
  locked = false;
  S.setToken("sekrit");
  await S.api("GET", "/status", undefined, undefined, { background: true });
  assert.equal(badge.hidden, true);
});
