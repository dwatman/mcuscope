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

// The eol select's port-default entry, as index.html declares it; the stub has no markup.
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
  assert.equal(dflt.textContent, "(CRLF)",
    "the daemon will append mcu's eol, so that is the one the bar must show");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/,
    "mcu answered OK monitor, so the bar must not post a raw line to it");
  assert.equal(env.byId("prompt").textContent, ">");
});

test("with two connected ports auto is ambiguous again, as it is at the daemon", async () => {
  managed(["mcu", "spare"], ["mcu", "spare"]);
  assert.equal(dflt.textContent, "(LF)",
    "no single port answers for auto, so lf is shown - the daemon's own default");
  assert.match(await sendUrl("ls"), /\/send$/, "and the mode falls back to the auto alias's");
});

test("with none connected auto is ambiguous too", async () => {
  managed(["mcu", "spare"], []);
  assert.equal(dflt.textContent, "(LF)");
});

test("a sole managed port still wins, connected or not", async () => {
  managed(["mcu"], []);
  assert.equal(dflt.textContent, "(CRLF)",
    "one attached port is never ambiguous, whatever its link state");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/);
});

test("an explicit pick beats every rule", async () => {
  managed(["mcu", "spare"], ["mcu"]);
  env.byId("cmdPort").value = "spare";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "(LF)", "spare's own eol, not the connected one's");
  assert.match(await sendUrl("ls"), /\/send$/);
});

// The auto option names the port it resolves to, the bar refuses to look armed with nothing
// attached, a marker is acknowledged, and the result strip tells the panes their height moved.
const { onResizeRedraw } = await import(webuiUrl("plots.js"));
const autoText = () => env.byId("cmdPort").children.find((o) => o.value === "auto").textContent;

test("auto names its resolution as ports arrive, connect and leave", async () => {
  managed(["mcu"], ["mcu"]);
  assert.equal(autoText(), "auto (mcu)");
  managed(["mcu", "spare"], ["mcu", "spare"]);
  assert.equal(autoText(), "auto", "two connected ports: auto names nothing, as the daemon refuses");
  state.portConnected.spare = false;   // a poll where only the link state moved
  syncCmdMode();
  assert.equal(autoText(), "auto (mcu)", "a connect-state change alone must relabel");
  managed(["spare"], []);
  assert.equal(autoText(), "auto (spare)", "the second port leaving hands auto to the one left");
  assert.equal(env.byId("cmdPort").value, "auto", "only the label moved, not the value");
  posts.length = 0;
  env.byId("cmdInput").value = "ping";
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  assert.equal(posts[0].body.port, null, "auto still sends no port, so the daemon resolves it");

  managed(["mcu", "spare"], ["mcu"]);
  env.byId("cmdPort").value = "spare";
  syncCmdMode();
  assert.equal(autoText(), "auto (mcu)", "an explicit pick leaves the auto option saying where auto goes");
});

test("with no port attached the input says so and the marker still works", async () => {
  managed([], []);
  const input = env.byId("cmdInput");
  assert.equal(input.disabled, true);
  assert.equal(input.placeholder, "attach a port to send commands");
  assert.equal(autoText(), "auto");
  assert.equal(env.byId("markerBtn").disabled, false, "a marker needs no port (SPEC 3.5)");

  managed(["mcu"], ["mcu"]);
  assert.equal(input.disabled, false, "the first attach re-arms the input");
  assert.match(input.placeholder, /^type a command/);
});

test("a marker that lands is acknowledged in the strip", async () => {
  env.byId("markerInput").value = "flash done";
  env.byId("markerBtn").emit("click", {});
  await tick(0);
  const box = env.byId("cmdResult");
  assert.equal(box.hidden, false);
  assert.equal(box.className, "cmd-result ok");
  assert.equal(box.textContent, "flash donemarker", "query then code, as every strip reads");
  assert.equal(env.byId("markerInput").value, "");
});

test("the result strip redraws the panes when it opens or closes, not on every update", async () => {
  let redraws = 0;
  onResizeRedraw(() => { redraws += 1; });
  const runFrames = () => env.frames.splice(0).forEach((f) => f());
  const box = env.byId("cmdResult");
  box.emit("click", {});   // hide whatever the last test left
  runFrames();
  redraws = 0;

  env.byId("cmdInput").value = "ping";
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });   // pending, then ok
  await tick(0);
  runFrames();
  assert.equal(box.hidden, false);
  assert.equal(redraws, 1, "opening the strip changes the workspace height");

  env.byId("cmdInput").value = "ping";   // a second result while the strip is already open
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  runFrames();
  assert.equal(redraws, 1, "replacing the strip's content moves nothing, so it must not redraw");

  box.emit("click", {});
  runFrames();
  assert.equal(box.hidden, true);
  assert.equal(redraws, 2, "closing it gives the height back, which the cached viewH never learned");

  box.emit("click", {});
  runFrames();
  assert.equal(redraws, 2, "hiding a hidden strip moves nothing");
});
