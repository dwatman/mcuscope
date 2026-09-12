// cmdbar.js: the line-ending select sits on "port default" until the user picks one.
//
// The default entry carries the value it will actually produce ("port default (crlf)"), the
// body omits `eol` while it is selected, and picking it again is the way back out of an
// override. Without that entry a single pick pinned the browser-side override for every port
// and every future page load, with clearing localStorage the only escape (W8).

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

const { state, getEol, setEol, setCmdModeFor } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, syncCmdEol } = await import(webuiUrl("cmdbar.js"));
initCmdBar();
for (const a of ["auto", "board", "a", "b"]) setCmdModeFor(a, "cmd");   // bodies under test are /cmd's

const sel = () => env.byId("cmdEol");
// index.html's option list; the stub has no markup of its own.
const options = new Map(["", "none", "lf", "crlf"].map((v) => {
  const o = env.document.createElement("option");
  o.value = v;
  o.textContent = v || "port default";
  sel().appendChild(o);
  return [v, o];
}));
const dflt = options.get("");

function pickEol(v) {
  sel().value = v;
  sel().emit("change", {});
}

async function send(text) {
  posts.length = 0;
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts.find((p) => p.url.includes("/cmd")).body;
}

test("under auto the sole port's eol labels the default and the body still omits eol", async () => {
  setEol("");
  state.portEol = { board: "crlf" };
  setKnownPorts(["board"]);
  syncCmdEol();
  assert.equal(sel().value, "", "no pick has been made, so the override is not set");
  assert.equal(dflt.textContent, "port default (crlf)",
    "the select must say what the port will actually append");
  assert.equal(Object.hasOwn(await send("i2c scan"), "eol"), false,
    "showing the port's value is not the same as overriding it");
});

test("a named port relabels the default; auto with two ports falls back to lf", () => {
  state.portEol = { a: "none", b: "crlf" };
  state.portConnected = { a: true, b: true };
  setKnownPorts(["a", "b"]);
  env.byId("cmdPort").value = "b";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "port default (crlf)");
  env.byId("cmdPort").value = "a";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "port default (none)");
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "port default (lf)",
    "auto over two connected ports has no single answer; lf is the daemon's");
});

test("a pick is explicit, carried on the body, and beats the port's value", async () => {
  pickEol("none");
  assert.equal(getEol(), "none");
  assert.equal((await send("i2c scan")).eol, "none");
  state.portEol = { a: "crlf", b: "crlf" };
  syncCmdEol();
  assert.equal(sel().value, "none", "a status poll must not overwrite the user's pick");
  assert.equal(dflt.textContent, "port default (lf)",
    "the default entry still says what dropping the override would mean");
});

test("picking the default again clears the override, without clearing site data", async () => {
  pickEol("none");
  assert.equal(env.store.get("mcuscope.eol"), "none");
  pickEol("");
  assert.equal(getEol(), "", "the override must be gone, not set to a third value");
  assert.equal(env.store.get("mcuscope.eol"), undefined,
    "and the stored pick with it, or the next page load comes back overridden");
  assert.equal(Object.hasOwn(await send("i2c scan"), "eol"), false);
  syncCmdEol();
  assert.equal(sel().value, "", "and the select stays on the default it was just set to");
});
