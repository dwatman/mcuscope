// settings.js: the browser-side line ending must be shown even when the daemon is not there.
//
// The "could not load config" branch renders the token and returns, so the Line ending select
// kept its markup default ("port default") while CRLF was in force - a display lie on the one
// screen whose job is to show it. getEol() is a pure module read and needs no daemon.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

globalThis.fetch = async (url) => {
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  }
  throw new Error("connection refused");   // GET /config, as a restarting daemon answers
};

const { setEol } = await import(webuiUrl("state.js"));
const { initSettings } = await import(webuiUrl("settings.js"));

test("an unreachable daemon still shows the stored line ending", async () => {
  setEol("crlf");
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0);
  await tick(0);
  assert.match(env.byId("cfgPath").textContent, /could not load config/,
    "this test is only meaningful on the branch that gave up on the daemon");
  assert.equal(env.byId("cfgEol").value, "crlf",
    "the select showed its markup default while CRLF was in force");
});
