// cmdbar.js: the send mode follows the targeted port, remembered per alias, and defaults
// to raw until the port has answered OK monitor (state.portTarget from /status).
//
// The toggle buttons sit behind a descendant selector the stub cannot resolve, so the mode
// is read off the prompt glyph and the posted URL, and a click is the exported setter.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const posts = [];
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "POST") posts.push({ url: u, body: JSON.parse(opt.body) });
  return { ok: true, status: 200, json: async () => ({ status: "ok", data: "", latency_ms: 1 }) };
};

const { state, getCmdMode } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, syncCmdMode, setCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

const mode = () => (env.byId("prompt").textContent === "$" ? "raw" : "cmd");
const click = (m) => setCmdMode(m, true);
function pick(alias) {
  env.byId("cmdPort").value = alias;
  env.byId("cmdPort").emit("change", {});
}
async function sendUrl(text) {
  posts.length = 0;
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts[0].url;
}

test("a port that never spoke the protocol defaults to raw and posts /send", async () => {
  state.portTarget = { sbc: null };
  setKnownPorts(["sbc"]);
  syncCmdMode();
  assert.equal(mode(), "raw");
  assert.match(await sendUrl("ls"), /\/send$/, "a console line must not carry a seq or wait");
});

test("OK monitor arriving on a status poll flips a never-picked port to cmd", async () => {
  state.portTarget = { sbc: "charger" };
  syncCmdMode();
  assert.equal(mode(), "cmd");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/);
});

test("a click is remembered per alias and beats the default", () => {
  state.portTarget = { mcu: "charger", sbc: null };
  setKnownPorts(["mcu", "sbc"]);
  pick("mcu");
  assert.equal(mode(), "cmd");
  click("raw");
  assert.equal(getCmdMode("mcu"), "raw");
  pick("sbc");
  assert.equal(mode(), "raw");
  click("cmd");
  pick("mcu");
  assert.equal(mode(), "raw", "mcu keeps its own pick");
  pick("sbc");
  assert.equal(mode(), "cmd", "sbc keeps its own pick");
  state.portTarget = { mcu: "charger", sbc: "other" };
  pick("mcu");
  assert.equal(mode(), "raw", "a status poll must not overwrite the user's pick");
  assert.equal(JSON.parse(localStorage.getItem("mcuscope.cmdMode")).mcu, "raw");
});

test("a hand-edited store with an unknown value is ignored, not sent", async () => {
  localStorage.setItem("mcuscope.cmdMode", JSON.stringify({ x: "bogus" }));
  const fresh = await import(webuiUrl("state.js") + "?reload=1");
  assert.equal(fresh.getCmdMode("x"), "raw");
});
