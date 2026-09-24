// cmdbar.js as the status poll drives it: a status poll must not move focus, and the command
// bar must not keep a resolved port while the daemon is unreachable.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

let status = null;
let statusDown = false;
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

globalThis.fetch = async (url) => {
  const u = String(url);
  if (u === "/status") {
    if (statusDown) throw new TypeError("Failed to fetch");
    return ok(status);
  }
  return ok({ ok: true });
};

const { initCmdBar } = await import(webuiUrl("cmdbar.js"));
const { initStatusbar, refreshStatus } = await import(webuiUrl("statusbar.js"));
initCmdBar();
initStatusbar();

function port(alias, over = {}) {
  return { alias, device: "socket://127.0.0.1:9900", baud: 115200, connected: true, eol: "lf",
           target: null, ...over };
}
function setStatus(ports) {
  status = { version: "0.4.0", uptime_s: 1, db_size_bytes: 0, db_content_bytes: 0, ports,
             write_errors: 0, session: null };
}
const autoLabel = () => env.byId("cmdPort").children.find((o) => o.value === "auto").textContent;
const prompt = () => env.byId("prompt").textContent;

// ---- E-4 --------------------------------------------------------------------------------

test("E-4: OK monitor arriving on a poll flips the mode without focusing the command input", async () => {
  const focused = [];
  env.byId("cmdInput").focus = () => focused.push("cmdInput");
  setStatus([port("sim")]);
  await refreshStatus();
  assert.equal(prompt(), "$", "a port that never answered OK monitor is raw");
  setStatus([port("sim", { target: "sim" })]);
  await refreshStatus();
  assert.equal(prompt(), ">", "the poll did flip the mode");
  assert.deepEqual(focused, [], "a background poll moved focus into the command input");
});

test("E-4: a second port making auto ambiguous changes mode without focus; a click still focuses", async () => {
  const focused = [];
  env.byId("cmdInput").focus = () => focused.push("cmdInput");
  setStatus([port("sim", { target: "sim" }), port("sbc", { target: null })]);
  await refreshStatus();
  assert.equal(autoLabel(), "(auto)");
  assert.equal(prompt(), "$", "an ambiguous auto falls back to raw");
  assert.deepEqual(focused, []);
  const { setCmdMode } = await import(webuiUrl("cmdbar.js"));
  setCmdMode("cmd", true);   // what the mode toggle's click handler calls
  assert.deepEqual(focused, ["cmdInput"], "the user's own pick still puts the caret in the input");
});

// ---- E-7 --------------------------------------------------------------------------------

test("E-7: offline, auto names no port and the input says why; back online it resolves again", async () => {
  setStatus([port("sim", { target: "sim" })]);
  await refreshStatus();
  assert.equal(autoLabel(), "(sim)");
  statusDown = true;
  await refreshStatus();
  assert.equal(env.byId("daemonVer").textContent, "daemon unreachable");
  assert.equal(autoLabel(), "(offline)", "the bar still claimed a resolved port");
  assert.equal(env.byId("cmdInput").placeholder, "daemon unreachable");
  assert.equal(env.byId("cmdInput").disabled, false,
    "one failed poll must not disable (and so blur) a line being typed");
  await refreshStatus();
  assert.equal(autoLabel(), "(offline)", "a second failed poll keeps it");
  statusDown = false;
  await refreshStatus();
  assert.equal(autoLabel(), "(sim)");
  assert.match(env.byId("cmdInput").placeholder, /type a command/);
});

test("E-7: offline with no port ever known still says offline, not 'attach a port'", async () => {
  setStatus([]);
  await refreshStatus();
  assert.equal(env.byId("cmdInput").placeholder, "attach a port to send commands");
  statusDown = true;
  await refreshStatus();
  statusDown = false;
  assert.equal(env.byId("cmdInput").placeholder, "daemon unreachable");
  assert.equal(autoLabel(), "(offline)");
});
