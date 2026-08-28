// cmdbar.js: both ends of the two bounds it owns.
//
// CMD_HISTORY_MAX was applied on save and on submit but not on load, so a localStorage value
// written by an older build (or edited by hand) was held whole for the life of the page.
//
// The timeout field carried its lower bound only. The daemon refuses timeout_ms above
// MAX_TIMEOUT_MS, and the client arms AbortSignal.timeout(timeout + 5000) from the same
// value, so an over-large entry left the strip on "..." for its whole window after the
// daemon had already refused it.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const posts = [];
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "POST") posts.push({ url: u, body: JSON.parse(opt.body) });
  if (u.includes("/cmd")) {
    return { ok: true, status: 200, json: async () => ({ status: "ok", data: "", latency_ms: 1 }) };
  }
  return { ok: true, status: 200, json: async () => ({ ports: [] }) };
};

// 150 entries, written before this build's cap existed.
const stored = Array.from({ length: 150 }, (_, i) => `cmd${i}`);
localStorage.setItem("cmdHistory", JSON.stringify(stored));

const { initCmdBar } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

const input = () => env.byId("cmdInput");

test("a stored history longer than the cap is trimmed on load, newest kept", () => {
  const seen = [];
  input().value = "";
  for (let i = 0; i < 200; i++) {
    input().emit("keydown", { key: "ArrowUp", preventDefault() {} });
    if (seen[seen.length - 1] !== input().value) seen.push(input().value);
  }
  assert.equal(seen[0], "cmd149", "ArrowUp starts at the newest entry");
  assert.equal(seen[seen.length - 1], "cmd50",
    "the oldest reachable entry must be the 100th from the end, not cmd0");
  assert.equal(seen.length, 100, `the load path must apply the cap (walked ${seen.length})`);
});

test("a timeout above the daemon's bound falls back to the default, visibly", async () => {
  posts.length = 0;
  input().value = "";
  env.byId("cmdTimeout").value = "400000";     // MAX_TIMEOUT_MS is 300000
  input().value = "i2c scan";
  input().emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  const put = posts.find((p) => p.url.includes("/cmd"));
  assert.ok(put, "the command must still be sent");
  assert.equal(put.body.timeout_ms, 1000,
    "an over-large timeout must not reach the daemon, which answers 422 for it");
  assert.equal(env.byId("cmdTimeout").value, "1000",
    "and the fallback is shown in the field, as it is for a zero or a blank");
});

test("a timeout inside the bound is sent as typed", async () => {
  posts.length = 0;
  env.byId("cmdTimeout").value = "300000";     // exactly MAX_TIMEOUT_MS
  input().value = "i2c scan";
  input().emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  const put = posts.find((p) => p.url.includes("/cmd"));
  assert.equal(put.body.timeout_ms, 300000, "the bound itself must be accepted");
});
