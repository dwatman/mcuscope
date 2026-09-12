// The per-alias maps /status fills are keyed by wire data, and `constructor`, `toString` and
// `valueOf` are legal aliases (config.ALIAS_RE `^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$`).
//
// Built as plain objects they answer Object.prototype members for those keys, so a port that
// has never spoken the monitor protocol read as "has a target" and the command bar posted
// /cmd - seq, wait and a timeout - to a plain console. Driven end to end here: a real /status
// answer through pollStatus, then the bar's own send.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let status = {};
const posts = [];
globalThis.fetch = async (path, opt = {}) => {
  if ((opt.method || "GET") !== "GET") posts.push({ url: String(path), body: JSON.parse(opt.body) });
  if (String(path).endsWith("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  return { ok: true, status: 200, json: async () => (posts.length && String(path).includes("/cmd")
    ? { status: "ok", data: "", latency_ms: 1 } : status) };
};

const { state } = await import(webuiUrl("state.js"));
const { refreshStatus } = await import(webuiUrl("statusbar.js"));
const { initCmdBar } = await import(webuiUrl("cmdbar.js"));
initCmdBar();

const port = (alias, over = {}) => ({ alias, device: "socket://x", resolved_device: "socket://x",
  description: "", baud: 115200, connected: true, held: false, eol: "lf", target: null,
  lines_rx: 0, ...over });

async function send(alias, text) {
  posts.length = 0;
  env.byId("cmdPort").value = alias;
  env.byId("cmdPort").emit("change", {});
  env.byId("cmdInput").value = text;
  env.byId("cmdInput").emit("keydown", { key: "Enter", preventDefault() {} });
  await tick(0);
  return posts[0];
}

test("a port aliased constructor is not the Object constructor", async () => {
  status = { version: "1", uptime_s: 0, db_size_bytes: 0, session: null, write_errors: 0,
             ports: [port("constructor", { eol: "crlf" }), port("toString"), port("sbc")] };
  await refreshStatus();

  for (const alias of ["constructor", "toString"]) {
    assert.equal(typeof state.portTarget[alias], "object",
      `portTarget[${alias}] must be the port's own value (null), not a prototype member`);
    assert.equal(state.portTarget[alias], null);
    assert.equal(state.portConnected[alias], true);
  }
  assert.equal(state.portEol.constructor, "crlf", "the port's own eol, not Object's constructor");
  assert.equal(Object.getPrototypeOf(state.portEol), null);
  assert.equal(Object.getPrototypeOf(state.portTarget), null);
  assert.equal(Object.getPrototypeOf(state.portConnected), null);

  // An alias nobody attached must read as absent on all three, not as a function.
  assert.equal(state.portEol.valueOf, undefined);
  assert.equal(state.portTarget.hasOwnProperty, undefined);
  assert.equal(state.portConnected.toLocaleString, undefined);
});

test("and it posts /send, like any other port that has not answered OK monitor", async () => {
  const p = await send("constructor", "ls");
  assert.match(p.url, /\/send$/,
    "a console line must not carry a seq and a timeout because the alias shadows a builtin");
  assert.equal(env.byId("prompt").textContent, "$", "and the bar must show raw, not neither");
});

test("the same alias follows OK monitor once the port answers", async () => {
  status.ports[0].target = "charger";
  await refreshStatus();
  const p = await send("constructor", "i2c scan");
  assert.match(p.url, /\/cmd$/);
  assert.equal(env.byId("prompt").textContent, ">");
});
