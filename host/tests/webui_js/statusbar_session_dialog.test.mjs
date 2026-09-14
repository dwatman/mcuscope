// statusbar.js: naming a session is a dialog with a note (not window.prompt), and the attach
// dialog's alias defaults the way the CLI's does.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let status = { version: "0.4.0", uptime_s: 0, db_size_bytes: 0, ports: [], write_errors: 0, session: null };
let posts = [];
let refuseWith = null;   // an error body for POST /sessions
let unreachable = false;
let hold = null;
globalThis.fetch = async (path, opt = {}) => {
  const p = String(path);
  if (opt.method === "POST") {
    posts.push([p, JSON.parse(opt.body || "{}")]);
    if (unreachable) throw new TypeError("Failed to fetch");
    if (hold) await hold;
    if (refuseWith) return { ok: false, status: 422, json: async () => ({ error: refuseWith }) };
    return { ok: true, status: 200, json: async () => ({ session: { id: 3, name: "x" } }) };
  }
  if (p.endsWith("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  return { ok: true, status: 200, json: async () => status };
};
let prompts = 0;
globalThis.prompt = () => { prompts += 1; return "from-prompt"; };

const { initStatusbar, refreshStatus, flashDaemonError, deriveAlias } = await import(webuiUrl("statusbar.js"));
initStatusbar();
const dlg = env.byId("sessionDlg");
const isOpen = () => dlg.hasAttribute("open");
const sessionPosts = () => posts.filter(([p]) => p === "/sessions");
async function settle() { for (let i = 0; i < 4; i++) await tick(0); }

async function openDialog() {
  env.byId("sessionBtn").emit("click", {});
  await settle();
}
function enter(tagName) {
  dlg.emit("keydown", { key: "Enter", target: { tagName }, preventDefault() {} });
}

test("the session button opens the dialog with a local-time default and sends nothing", async () => {
  await refreshStatus();
  posts = [];
  await openDialog();
  assert.equal(isOpen(), true);
  assert.equal(prompts, 0, "no browser prompt");
  assert.match(env.byId("sesName").value, /^run-\d{4}-\d\d-\d\d_\d\d-\d\d$/);
  assert.equal(env.byId("sesNote").value, "");
  assert.deepEqual(posts, []);
});

test("an empty or whitespace name is refused in the dialog and sends nothing", async () => {
  for (const name of ["", "   \t"]) {
    env.byId("sesName").value = name;
    env.byId("sesStart").emit("click", {});
    await settle();
    assert.equal(env.byId("sesErr").textContent, "Name is required");
  }
  assert.deepEqual(sessionPosts(), []);
  assert.equal(isOpen(), true);
});

test("Enter in the note or on Cancel starts nothing; Enter in the name starts it with the note", async () => {
  env.byId("sesName").value = "  boot-test ";
  env.byId("sesNote").value = " flashed v2 \n";
  enter("TEXTAREA");
  enter("BUTTON");
  await settle();
  assert.deepEqual(sessionPosts(), []);
  flashDaemonError("detach mcu failed: gone");
  enter("INPUT");
  await settle();
  assert.deepEqual(sessionPosts(), [["/sessions", { name: "boot-test", note: "flashed v2" }]]);
  assert.equal(isOpen(), false, "a started session closes the dialog");
  assert.equal(env.byId("actionErr").hidden, true, "and clears the failed-action strip, like any action that worked");
});

test("a daemon refusal stays in the dialog, not in the status bar strip", async () => {
  await openDialog();
  posts = [];
  refuseWith = "name: String should have at most 128 characters";
  env.byId("sesName").value = "x".repeat(129);
  env.byId("sesStart").emit("click", {});
  await settle();
  refuseWith = null;
  assert.equal(env.byId("sesErr").textContent, "name: String should have at most 128 characters");
  assert.equal(isOpen(), true);
  assert.equal(env.byId("actionErr").hidden, true);
  assert.equal(env.byId("sesStart").disabled, false, "a refusal re-arms Start");
});

test("an unreachable daemon is reported in the dialog, which stays open with the typing", async () => {
  unreachable = true;
  env.byId("sesName").value = "keep-me";
  env.byId("sesStart").emit("click", {});
  await settle();
  unreachable = false;
  assert.equal(env.byId("sesErr").textContent, "Failed to fetch");
  assert.equal(env.byId("sesName").value, "keep-me");
  assert.equal(isOpen(), true);
});

test("a held Enter while the start is in flight posts once", async () => {
  posts = [];
  let release;
  hold = new Promise((r) => { release = r; });
  env.byId("sesName").value = "once";
  enter("INPUT");
  enter("INPUT");
  env.byId("sesStart").emit("click", {});
  await settle();
  hold = null;
  release();
  await settle();
  assert.equal(sessionPosts().length, 1);
});

test("reopening clears the last refusal; Escape and Cancel close without starting", async () => {
  await openDialog();
  assert.equal(env.byId("sesErr").textContent, "");
  posts = [];
  dlg.emit("cancel", { preventDefault() {} });
  assert.equal(isOpen(), false);
  await openDialog();
  env.byId("sesCancel").emit("click", {});
  assert.equal(isOpen(), false);
  assert.deepEqual(sessionPosts(), []);
});

test("with a named session running the button stops it and opens no dialog", async () => {
  status = { ...status, session: { id: 3, name: "boot-test", auto: false } };
  await refreshStatus();
  posts = [];
  env.byId("sessionBtn").emit("click", {});
  await settle();
  assert.deepEqual(posts.map(([p]) => p), ["/sessions/stop"]);
  assert.equal(isOpen(), false);
});

// ---- attach dialog alias --------------------------------------------------------------

test("deriveAlias matches cli._derive_alias", () => {
  // Expected values printed by mcuscope.cli._derive_alias for these inputs.
  const cases = [
    ["/dev/ttyACM0", "ttyACM0"], ["COM7", "COM7"], ["\\\\.\\COM7", "COM7"],
    ["socket://127.0.0.1:9900", "board"], ["rfc2217://h:1", "board"],
    ["/dev/serial/by-id/usb-STMicro_STLINK-V3_0031-if01", "usb-STMicro_STLINK-V3_0031-if01"],
    ["/dev/tty USB0/", "tty-USB0"], ["/", "board"], ["", "board"], ["___x", "x"],
    ["a".repeat(40), "a".repeat(32)], ["/dev/caf\u00e9\u{1F600}x", "caf--x"], ["C:\\dev\\board\\", "board"],
  ];
  for (const [dev, want] of cases) assert.equal(deriveAlias(dev), want, JSON.stringify(dev));
});

test("the alias follows the device until one is typed, and again once it is cleared", async () => {
  env.byId("attachBtn").emit("click", {});
  await settle();
  const sel = env.byId("devSel"), alias = env.byId("aliasInput");
  sel.value = "/dev/ttyUSB3";
  sel.emit("change", {});
  assert.equal(alias.value, "ttyUSB3");
  sel.value = "custom";
  sel.emit("change", {});
  env.byId("devCustom").value = "socket://10.0.0.2:4000";
  env.byId("devCustom").emit("input", {});
  assert.equal(alias.value, "board", "a URL defaults to board, as mcu attach does");
  alias.value = "bench";
  alias.emit("input", {});
  sel.value = "/dev/ttyUSB3";
  sel.emit("change", {});
  assert.equal(alias.value, "bench", "a typed alias is never overwritten");
  alias.value = "";
  alias.emit("input", {});
  sel.emit("change", {});
  assert.equal(alias.value, "ttyUSB3");
  alias.value = "typed-then-cancelled";
  alias.emit("input", {});
  env.byId("dlgCancel").emit("click", {});
  env.byId("attachBtn").emit("click", {});
  await settle();
  sel.value = "COM4";
  sel.emit("change", {});
  assert.equal(alias.value, "COM4", "a new open forgets the last typed alias");
});
