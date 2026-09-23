// cmdbar.js targetAlias(): the bar aims at the pick, else the sole attached port. With several
// attached, a write never goes to a default port, whatever their link state (SPEC 4): auto
// names none, and Enter is refused until a port is picked.

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
const { initCmdBar, syncCmdEol, syncCmdMode, setCmdMode } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

// The eol select's port-default entry, as index.html declares it; the stub has no markup.
const dflt = (() => {
  const sel = env.byId("cmdEol");
  const o = env.document.createElement("option");
  o.value = "";
  sel.appendChild(o);
  return o;
})();

// The attached ports: mcu speaks the monitor protocol with a crlf eol, the rest are consoles.
function managed(aliases) {
  state.portEol = Object.create(null);
  state.portTarget = Object.create(null);
  for (const a of aliases) {
    state.portEol[a] = a === "mcu" ? "crlf" : "lf";
    state.portTarget[a] = a === "mcu" ? "charger" : null;
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
  return posts.length ? posts[0].url : null;
}

test("with several attached, auto sends nothing and says to pick a port", async () => {
  for (const mode of ["cmd", "raw"]) {
    managed(["mcu", "spare"]);
    setCmdMode(mode);
    assert.equal(dflt.textContent, "(LF)", "auto names no port, so the daemon default is shown");
    assert.equal(await sendUrl("gpio set relay 1"), null, `${mode}: a write went to a default port`);
    const box = env.byId("cmdResult");
    assert.equal(box.className, "cmd-result err");
    assert.match(box.textContent, /pick a port: 2 are attached/);
    assert.equal(env.byId("cmdInput").value, "gpio set relay 1", "the line is kept to send after the pick");
  }
  env.byId("cmdPort").value = "mcu";
  env.byId("cmdPort").emit("change", {});
  assert.match(await sendUrl("gpio set relay 1"), /\/(cmd|send)$/, "a picked port sends");
  assert.equal(posts[0].body.port, "mcu");
});

test("a sole attached port still wins", async () => {
  managed(["mcu"]);
  assert.equal(dflt.textContent, "(CRLF)",
    "one attached port is never ambiguous, whatever its link state");
  assert.match(await sendUrl("i2c scan"), /\/cmd$/);
});

test("an explicit pick beats every rule", async () => {
  managed(["mcu", "spare"]);
  env.byId("cmdPort").value = "spare";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "(LF)", "spare's own eol, not the connected one's");
  assert.match(await sendUrl("ls"), /\/send$/);
});

// The auto option names the port it resolves to, the bar refuses to look armed with nothing
// attached, a marker is acknowledged, and the result strip tells the panes their height moved.
const { onResizeRedraw } = await import(webuiUrl("plots.js"));
const autoText = () => env.byId("cmdPort").children.find((o) => o.value === "auto").textContent;

test("auto names its resolution as ports arrive and leave", async () => {
  managed(["mcu"]);
  assert.equal(autoText(), "(mcu)");
  for (const o of env.byId("cmdPort").children.filter((c) => c.value !== "auto")) {
    assert.equal(o.textContent, o.value, "explicit options stay bare aliases, so only auto carries brackets");
  }
  managed(["mcu", "spare"]);
  assert.equal(autoText(), "(auto)", "two attached ports: auto names nothing");
  managed(["spare"]);
  assert.equal(autoText(), "(spare)", "the second port leaving hands auto to the one left");
  assert.equal(env.byId("cmdPort").value, "auto", "only the label moved, not the value");
  posts.length = 0;
  env.byId("cmdInput").value = "ping";
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  assert.equal(posts[0].body.port, null, "auto still sends no port, so the daemon resolves it");

  managed(["mcu", "spare"]);
  env.byId("cmdPort").value = "spare";
  syncCmdMode();
  assert.equal(autoText(), "(auto)", "an explicit pick leaves the auto option saying where auto goes");
});

test("with no port attached the input and the marker button are off, the marker text is not", async () => {
  managed([]);
  const input = env.byId("cmdInput");
  assert.equal(input.disabled, true);
  assert.equal(input.placeholder, "attach a port to send commands");
  assert.equal(autoText(), "(auto)", "nothing attached: the short placeholder, not an empty ()");
  const btn = env.byId("markerBtn");
  const text = env.byId("markerInput");
  assert.equal(btn.disabled, true);
  assert.equal(btn.title, "attach a port to add a marker");
  assert.equal(text.disabled, false, "a label can be typed ahead of the attach");

  // Neither road posts while greyed out: Enter in the text box, or a click the browser let through.
  posts.length = 0;
  text.value = "early label";
  text.emit("keydown", { key: "Enter", preventDefault() {} });
  btn.emit("click", {});
  await tick(0);
  assert.deepEqual(posts.filter((p) => p.url.includes("/marker")), [], "a marker posted with no port attached");
  assert.equal(text.value, "early label", "the typed label is kept for after the attach");

  managed(["mcu"]);
  assert.equal(input.disabled, false, "the first attach re-arms the input");
  assert.match(input.placeholder, /^type a command/);
  assert.equal(btn.disabled, false, "and the marker button");
  assert.equal(btn.title, "");
});

test("a marker that lands is acknowledged in the strip", async () => {
  managed(["mcu"]);   // a marker needs a port attached
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
