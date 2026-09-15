// cmdbar.js: the line-ending select sits on "port default" until the user picks one.
//
// The default entry carries the value it will actually produce ("(CRLF)"), the
// body omits `eol` while it is selected, and picking it again is the way back out of an
// override. Without that entry a single pick pinned the browser-side override for every port
// and every future page load, with clearing localStorage the only escape (W8).

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, webuiDir, tick } from "./dom_stub.mjs";

const env = installDom();

const posts = [];
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "POST") posts.push({ url: u, body: JSON.parse(opt.body) });
  return { ok: true, status: 200, json: async () => ({ status: "ok", data: "", latency_ms: 1 }) };
};

// A browser <select>, which the stub's is not: a value no option carries selects nothing, and
// an option appended while nothing is selected becomes the selection.
function browserSelect(el) {
  let selected = null;
  Object.defineProperty(el, "value", {
    configurable: true,
    get: () => (selected ? selected.value : ""),
    set: (v) => { selected = el.children.find((o) => o.value === String(v)) || null; },
  });
  const append = el.appendChild.bind(el);
  el.appendChild = (o) => { const r = append(o); if (!selected) selected = o; return r; };
}

const sel = () => env.byId("cmdEol");
browserSelect(sel());
// index.html's port-default entry, present before any script runs.
const dflt = env.document.createElement("option");
dflt.value = "";
dflt.textContent = "(LF)";
sel().appendChild(dflt);

env.localStorage.setItem("mcuscope.eol", "crlf");   // a pick saved by an earlier page load
const { state, getEol, setEol, setCmdModeFor } = await import(webuiUrl("state.js"));
const { setKnownPorts } = await import(webuiUrl("terminal.js"));
const { initCmdBar, syncCmdEol } = await import(webuiUrl("cmdbar.js"));
initCmdBar();
const shownAtInit = sel().value;
for (const a of ["auto", "board", "a", "b"]) setCmdModeFor(a, "cmd");   // bodies under test are /cmd's

test("initCmdBar offers every line ending the daemon accepts, once each, after the default", () => {
  assert.deepEqual(sel().children.map((o) => o.value), ["", "lf", "crlf", "none"]);
  // "Every" from the daemon's own table, not from this list.
  const py = readFileSync(webuiDir() + "../protocol.py", "utf8");
  const table = py.match(/^EOL_BYTES\b[^=]*=\s*\{([^}]*)\}/m);
  assert.ok(table, "EOL_BYTES not found in protocol.py");
  const accepted = [...table[1].matchAll(/"(\w+)":/g)].map((m) => m[1]).sort();
  assert.ok(accepted.length >= 3, accepted.join());
  assert.deepEqual(sel().children.map((o) => o.value).slice(1).sort(), accepted);
});

test("a saved pick shows from the first paint, before any /status poll", () => {
  assert.equal(getEol(), "crlf");
  assert.equal(shownAtInit, "crlf", "the bar showed the port default while sends appended CRLF");
});

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
  assert.equal(dflt.textContent, "(CRLF)",
    "the select must say what the port will actually append");
  assert.equal(Object.hasOwn(await send("i2c scan"), "eol"), false,
    "showing the port's value is not the same as overriding it");
});

// Ports a (none) and b (crlf), both connected, the bar on auto.
function twoPorts() {
  state.portEol = { a: "none", b: "crlf" };
  state.portConnected = { a: true, b: true };
  setKnownPorts(["a", "b"]);
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
}

test("a named port relabels the default; auto with two ports falls back to lf", () => {
  twoPorts();
  env.byId("cmdPort").value = "b";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "(CRLF)");
  env.byId("cmdPort").value = "a";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "(none)");
  env.byId("cmdPort").value = "auto";
  env.byId("cmdPort").emit("change", {});
  assert.equal(dflt.textContent, "(LF)",
    "auto over two connected ports has no single answer; lf is the daemon's");
});

test("a pick is explicit, carried on the body, and beats the port's value", async () => {
  twoPorts();
  pickEol("none");
  assert.equal(getEol(), "none");
  assert.equal((await send("i2c scan")).eol, "none");
  state.portEol = { a: "crlf", b: "crlf" };
  syncCmdEol();
  assert.equal(sel().value, "none", "a status poll must not overwrite the user's pick");
  assert.equal(dflt.textContent, "(LF)",
    "the default entry still says what dropping the override would mean");
});

test("picking the default again clears the override, without clearing site data", async () => {
  twoPorts();
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
