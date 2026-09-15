// Sweep-stage ruling: Settings and Attach open from the click in a loading state and fill when
// the daemon answers, so a slow daemon can no longer move focus seconds after the click.
// These drive what the loading state must refuse, and a close before the answer lands.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const held = [];
const posts = [];
let holdPost = false;
const heldPosts = [];

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if (u === "/devices") {
    const body = { devices: [{ device: "/dev/ttyACM0", description: "STLINK" }] };
    return new Promise((res) => held.push(() => res(ok(body))));
  }
  if ((opt.method || "GET") === "POST" && u === "/ports") {
    posts.push(JSON.parse(opt.body));
    if (holdPost) return new Promise((res) => heldPosts.push(() => res(ok({ ok: true }))));
  }
  if (u === "/status") return ok({ version: "0.5.0", uptime_s: 1, ports: [], session: null });
  return ok({ ok: true });
};

const { initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();
async function settle() { for (let i = 0; i < 6; i++) await tick(0); }
const dlg = env.byId("attachDlg");
const optionTexts = () => env.byId("devSel").children.map((o) => o.textContent);

function fillValidForm() {
  env.byId("aliasInput").value = "mcu0";
  env.byId("devSel").value = "custom";
  env.byId("devCustom").value = "socket://127.0.0.1:9900";
  env.byId("baudSel").value = "115200";   // the stub's select starts with no value
}

test("Enter or a click on Attach while the device list is loading sends nothing", async () => {
  dlg.removeAttribute("open");
  held.length = 0; posts.length = 0;
  env.byId("attachBtn").emit("click", {});
  await settle();
  assert.equal(dlg.hasAttribute("open"), true, "the dialog did not open from the click");
  fillValidForm();
  env.byId("dlgAttach").emit("click", {});
  await settle();
  assert.deepEqual(posts, [], "an attach went out before the device list landed");
  for (const release of held.splice(0)) release();
  await settle();
  assert.equal(env.byId("dlgAttach").disabled, false);
  fillValidForm();
  env.byId("dlgAttach").emit("click", {});
  await settle();
  assert.equal(posts.length, 1, "positive control: once loaded, the same form attaches: " + env.byId("dlgErr").textContent);
});

test("a close before /devices answers leaves the late list out of the closed dialog", async () => {
  dlg.removeAttribute("open");
  held.length = 0;
  env.byId("attachBtn").emit("click", {});
  await settle();
  assert.deepEqual(optionTexts(), ["loading devices..."]);
  env.byId("dlgCancel").emit("click", {});
  assert.equal(dlg.hasAttribute("open"), false);
  assert.equal(env.byId("dlgAttach").disabled, false, "a close left Attach held for the next open");
  for (const release of held.splice(0)) release();
  await settle();
  assert.equal(dlg.hasAttribute("open"), false, "the late answer reopened the dialog");
  assert.deepEqual(optionTexts(), ["loading devices..."], "the late answer filled a closed dialog");
});

test("an attach from an earlier opening finishing during a reopen's load does not unhold Attach", async () => {
  dlg.removeAttribute("open");
  held.length = 0; posts.length = 0;
  env.byId("attachBtn").emit("click", {});
  await settle();
  for (const release of held.splice(0)) release();
  await settle();
  fillValidForm();
  holdPost = true;
  env.byId("dlgAttach").emit("click", {});
  await settle();
  assert.equal(posts.length, 1);
  env.byId("dlgCancel").emit("click", {});
  env.byId("attachBtn").emit("click", {});   // reopen: the device list is loading again
  await settle();
  for (const release of heldPosts.splice(0)) release();   // the first attach lands now
  await settle();
  holdPost = false;
  fillValidForm();
  env.byId("dlgAttach").emit("click", {});
  await settle();
  assert.equal(posts.length, 1, "a second attach went out while the reopened list was loading");
  for (const release of held.splice(0)) release();
  await settle();
});
