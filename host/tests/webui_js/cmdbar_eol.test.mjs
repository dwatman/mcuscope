// cmdbar.js: the line-ending select shows the port's own setting until the user picks one.
//
// There is no "port default" entry, so the select must be seeded from /status (the sole port
// under auto, the named one otherwise) while the body still omits `eol`; a pick is explicit
// and beats the port's value from then on.

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

const { state, getEol, setEol } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, syncCmdEol } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

const sel = () => env.byId("cmdEol");
async function send(text) {
  posts.length = 0;
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts.find((p) => p.url.includes("/cmd")).body;
}

test("under auto the sole port's eol is shown and the body still omits eol", async () => {
  setEol("");
  state.portEol = { board: "crlf" };
  setKnownPorts(["board"]);
  syncCmdEol();
  assert.equal(sel().value, "crlf", "the select must show what the port will actually append");
  assert.equal(Object.hasOwn(await send("i2c scan"), "eol"), false,
    "showing the port's value is not the same as overriding it");
});

test("a named port shows its own eol; auto with two ports falls back to lf", () => {
  state.portEol = { a: "none", b: "crlf" };
  setKnownPorts(["a", "b"]);
  env.byId("cmdPort").value = "b";
  env.byId("cmdPort").emit("change", {});
  assert.equal(sel().value, "crlf");
  env.byId("cmdPort").value = "a";
  env.byId("cmdPort").emit("change", {});
  assert.equal(sel().value, "none");
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
  assert.equal(sel().value, "lf", "auto over two ports has no single answer; lf is the daemon's");
});

test("a pick is explicit, carried on the body, and beats the port's value", async () => {
  sel().value = "none";
  sel().emit("change", {});
  assert.equal(getEol(), "none");
  assert.equal((await send("i2c scan")).eol, "none");
  state.portEol = { a: "crlf", b: "crlf" };
  syncCmdEol();
  assert.equal(sel().value, "none", "a status poll must not overwrite the user's pick");
});
