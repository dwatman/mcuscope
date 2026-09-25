// cmdbar.js (D-9): a command and a marker lose their surrounding U+0020 only, as the daemon and
// the firmware trim (SPEC 2.1, 2.5). JS .trim() also took tabs, NBSP and Unicode spaces, a
// third whitespace set, so `ping\t` reached the board as `ping`.

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
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, setCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();
state.portTarget = Object.assign(Object.create(null), { p1: "board" });
setKnownPorts(["p1"]);
setCmdMode("cmd", true);

async function send(id, text) {
  posts.length = 0;
  env.byId(id).value = text;
  env.byId(id).emit("keydown", { key: "Enter", preventDefault() {} });
  for (let i = 0; i < 4; i++) await tick(0);
  return posts;
}

const KEPT = ["\t", " ", "　", "\x0b"];

test("a command keeps every surrounding byte but the space", async () => {
  for (const ws of KEPT) {
    const sent = await send("cmdInput", `  ${ws}ping${ws}  `);
    assert.deepEqual(sent.map((p) => [p.url, p.body.cmd]), [["/cmd", `${ws}ping${ws}`]], JSON.stringify(ws));
  }
  assert.deepEqual(await send("cmdInput", "   "), [], "positive control: spaces alone send nothing");
  assert.equal((await send("cmdInput", "\t"))[0].body.cmd, "\t", "a tab is a command byte");
});

test("a marker keeps every surrounding byte but the space", async () => {
  for (const ws of KEPT) {
    const sent = await send("markerInput", `  ${ws}here${ws}  `);
    assert.deepEqual(sent.map((p) => [p.url, p.body.text]), [["/marker", `${ws}here${ws}`]], JSON.stringify(ws));
  }
  assert.deepEqual(await send("markerInput", "   "), [], "positive control: spaces alone send nothing");
});
