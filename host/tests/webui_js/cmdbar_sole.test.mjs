// cmdbar.js targetAlias(): the bar must aim where the daemon will actually send.
//
// PortManager.resolve(None) answers the sole port, and among several attached the sole
// CONNECTED one (serial_link.py, SPEC 4). The bar shortcut only knew the first clause, so
// with two managed ports and one connected it seeded its line ending and its send mode from
// the "auto" pseudo-alias while the daemon answered on a real port: the wrong eol on the
// wire, and /send to a target that speaks the monitor protocol.

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
const { initCmdBar, syncCmdEol, syncCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

// The eol select's "port default" entry, as index.html declares it; the stub has no markup.
const dflt = (() => {
  const sel = env.byId("cmdEol");
  const o = env.document.createElement("option");
  o.value = "";
  sel.appendChild(o);
  return o;
})();

// Two managed ports, of which `connected` are up.
function managed(aliases, connected) {
  state.portEol = Object.create(null);
  state.portTarget = Object.create(null);
  state.portConnected = Object.create(null);
  for (const a of aliases) {
    state.portEol[a] = a === "mcu" ? "crlf" : "lf";
    state.portTarget[a] = a === "mcu" ? "charger" : null;
    state.portConnected[a] = connected.includes(a);
  }
  setKnownPorts(aliases);
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
  syncCmdEol();
  syncCmdMode();
}

async function sendUrl(text) {
  posts.length = 0;
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts[0].url;
}

test("under auto a sole connected port among several is what the bar aims at", async () => {
  managed(["mcu", "spare"], ["mcu"]);
  assert.equal(dflt.textContent, "port default (crlf)",
    "the daemon will append mcu's eol, so that is the one the bar must show");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/,
    "mcu answered OK monitor, so the bar must not post a raw line to it");
  assert.equal(env.byId("prompt").textContent, ">");
});

test("with two connected ports auto is ambiguous again, as it is at the daemon", async () => {
  managed(["mcu", "spare"], ["mcu", "spare"]);
  assert.equal(dflt.textContent, "port default (lf)",
    "no single port answers for auto, so lf is shown - the daemon's own default");
  assert.match(await sendUrl("ls"), /\/send$/, "and the mode falls back to the auto alias's");
});

test("with none connected auto is ambiguous too", async () => {
  managed(["mcu", "spare"], []);
  assert.equal(dflt.textContent, "port default (lf)");
});

test("a sole managed port still wins, connected or not", async () => {
  managed(["mcu"], []);
  assert.equal(dflt.textContent, "port default (crlf)",
    "one attached port is never ambiguous, whatever its link state");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/);
});

test("an explicit pick beats every rule", async () => {
  managed(["mcu", "spare"], ["mcu"]);
  env.byId("cmdPort").value = "spare";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "port default (lf)", "spare's own eol, not the connected one's");
  assert.match(await sendUrl("ls"), /\/send$/);
});
