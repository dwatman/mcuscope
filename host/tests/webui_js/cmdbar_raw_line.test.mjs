// cmdbar.js: raw mode writes the line exactly as typed, empty included; cmd mode trims.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const posts = [];
globalThis.fetch = async (url, opt = {}) => {
  if ((opt.method || "GET") === "POST") posts.push({ url: String(url), body: JSON.parse(opt.body) });
  return { ok: true, status: 200, json: async () => ({ status: "ok", data: "", latency_ms: 1 }) };
};

const { state } = await import(webuiUrl("state.js"));
const { initCmdBar, populateCmdPort, setCmdMode } = await import(webuiUrl("cmdbar.js"));
state.knownAliases = ["b"];
initCmdBar();
populateCmdPort();

async function enter(mode, typed) {
  setCmdMode(mode);
  posts.length = 0;
  env.byId("cmdInput").value = typed;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts;
}

test("raw mode sends the line as typed", async () => {
  for (const typed of ["    print(x)", "abc  ", "\tx", ""]) {
    const sent = await enter("raw", typed);
    assert.equal(sent.length, 1, `${JSON.stringify(typed)} was not sent`);
    assert.match(sent[0].url, /\/send$/);
    assert.equal(sent[0].body.line, typed);
  }
});

test("an empty raw line is not added to the history, an indented one is, verbatim", async () => {
  await enter("raw", "  indented");
  await enter("raw", "");
  env.byId("cmdInput").emit("keydown", { key: "ArrowUp", preventDefault() {} });
  assert.equal(env.byId("cmdInput").value, "  indented");
});

test("cmd mode trims, and a blank command sends nothing", async () => {
  const sent = await enter("cmd", "  ping  ");
  assert.equal(sent.length, 1);
  assert.match(sent[0].url, /\/cmd$/);
  assert.equal(sent[0].body.cmd, "ping");
  assert.deepEqual(await enter("cmd", "   "), [], "a blank command reached the daemon");
});
