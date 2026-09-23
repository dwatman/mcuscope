// cmdbar.js: a picked port that detaches stays picked. Falling back to auto sent the next
// command to whichever board remained (SPEC 4: a write never goes to a default port).

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
const { initCmdBar, populateCmdPort } = await import(webuiUrl("cmdbar.js"));
state.knownAliases = ["boardA", "boardB"];
initCmdBar();

async function send(text) {
  posts.length = 0;
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts;
}

test("the pick survives its port detaching, and the next command still names it", async () => {
  for (const remaining of [["boardB"], ["boardB", "boardC"]]) {
    state.knownAliases = ["boardA", ...remaining];
    populateCmdPort();
    env.byId("cmdPort").value = "boardA";
    env.byId("cmdPort").emit("change", {});
    state.knownAliases = remaining;   // boardA detached; a status poll repopulates
    populateCmdPort();
    assert.equal(env.byId("cmdPort").value, "boardA", "the select fell back to auto");
    const sent = await send("reset");
    assert.equal(sent.length, 1);
    assert.equal(sent[0].body.port, "boardA",
      "the command must go to the picked port, where the daemon refuses it, not to the one left");
  }
});

test("positive control: auto over the one board left sends no port", async () => {
  state.knownAliases = ["boardB"];
  populateCmdPort();
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
  const sent = await send("reset");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.port, null);
});
