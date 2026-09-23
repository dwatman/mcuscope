// cmdbar.js: the "pick a port" refusal supersedes a command still in flight, so that command's
// late answer cannot overwrite the refusal in the result strip.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let release = null;
globalThis.fetch = async (url, opt = {}) => {
  if (String(url).endsWith("/cmd")) await new Promise((r) => { release = r; });
  return { ok: true, status: 200, json: async () => ({ status: "ok", data: "pong", latency_ms: 1 }) };
};

const { state } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, setCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

function attach(aliases) {
  state.portEol = Object.create(null);
  state.portTarget = Object.create(null);
  for (const a of aliases) { state.portEol[a] = "lf"; state.portTarget[a] = "charger"; }
  setKnownPorts(aliases);
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
}

async function enter(text) {
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
}

test("a refusal made while a command is in flight survives that command's answer", async () => {
  attach(["mcu"]);
  setCmdMode("cmd");
  await enter("ping");
  assert.ok(release, "setup: the command is in flight");
  const box = env.byId("cmdResult");
  assert.equal(box.className, "cmd-result pending");

  attach(["mcu", "spare"]);
  await enter("gpio set relay 1");
  assert.match(box.textContent, /pick a port: 2 are attached/);

  release();
  for (let i = 0; i < 4; i++) await tick(0);
  assert.equal(box.className, "cmd-result err", "the late answer replaced the refusal");
  assert.match(box.textContent, /pick a port/);
});
