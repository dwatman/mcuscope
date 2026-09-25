// settings.js: the dialog opens before /config answers (SPEC 9.1), so what it leaves live
// while loading is pinned here. FD2-1: the fields the daemon owns are held
// with the Saves, or the answer overwrites what was typed into one and marks the section clean.
// FD2-2: the deferred failure branch does not pull focus out of the dialog the user is in.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { installDom, webuiUrl, webuiDir, tick } from "./dom_stub.mjs";

const env = installDom();
// index.html has both of these inside <dialog id="settingsDlg">; ids resolve to detached
// elements here, so the ones the focus rule asks about are attached as the markup has them.
env.byId("settingsDlg").appendChild(env.byId("setClose"));
env.byId("settingsDlg").appendChild(env.byId("cfgToken"));

const ok = (b) => ({ ok: true, status: 200, json: async () => b });
const heldConfig = [];
let holdConfig = false;
let failConfig = false;

const CFG = () => ({
  path: "/cfg/mcuscope.toml", exists: true, revision: "r1", restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  update: { check: true }, plotjuggler: { enabled: false, dest: "127.0.0.1:9870" },
  ports: [{ alias: "keep", device: "/dev/ttyACM0", baud: 921600, eol: "lf", autoconnect: true, identify: true }],
});

globalThis.fetch = async (url) => {
  const u = String(url);
  if (u === "/config") {
    // Decided when answered, so a held read can land as either branch.
    const answer = () => (failConfig ? { ok: false, status: 503, json: async () => ({ error: "down" }) }
      : ok(CFG()));
    if (holdConfig) return new Promise((res) => heldConfig.push(() => res(answer())));
    return answer();
  }
  if (u === "/devices") return ok({ devices: [] });
  if (u === "/status") return ok({ version: "0.5.0", db_content_bytes: 0, db_size_bytes: 0, config_warnings: [] });
  if (u.startsWith("/sessions")) return ok({ sessions: [], active: null });
  if (u === "/plotjuggler") return ok({ enabled: false, dest: "127.0.0.1:9870" });
  return ok({ ok: true });
};

const { setToken } = await import(webuiUrl("state.js"));
const { initSettings } = await import(webuiUrl("settings.js"));
initSettings();
const dlg = env.byId("settingsDlg");
const settle = async () => { for (let i = 0; i < 8; i++) await tick(0); };

// Open with /config in flight; returns once the dialog is up and the answer is still out.
async function openHeld() {
  dlg.removeAttribute("open");
  heldConfig.length = 0;
  holdConfig = true; failConfig = false;
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(dlg.hasAttribute("open"), true, "the dialog did not open from the click");
  assert.equal(env.byId("cfgPath").textContent, "loading...");
  assert.equal(heldConfig.length, 1, "/config did not go out");
}
async function landConfig() {
  holdConfig = false;
  heldConfig.shift()();
  await settle();
}

// ---- FD2-1 --------------------------------------------------------------------------------

// Every settings field index.html declares, less the browser-side token: a field added there is
// held here without this list being edited.
const FIELDS = [...readFileSync(webuiDir() + "index.html", "utf8")
  .matchAll(/<(?:input|select|textarea)\b[^>]*\bid="(cfg[^"]*)"/g)].map((m) => m[1])
  .filter((id) => id !== "cfgToken");
assert.ok(FIELDS.length >= 10 && FIELDS.includes("cfgPjDest"), FIELDS.join());

test("FD2-1: every daemon-owned field is held while /config is out, and live once it lands", async () => {
  await openHeld();
  for (const id of FIELDS) {
    assert.equal(env.byId(id).disabled, true, `${id} was editable while the file was still loading`);
  }
  assert.equal(env.byId("cfgServerSave").disabled, true, "the Saves are held too");
  assert.equal(env.byId("cfgToken").disabled, false,
               "the browser-side token is not the daemon's and stays usable");
  // The stub cannot refuse typing into a disabled input, so this is what the disabled flag
  // above prevents in a browser: it lands in the field and the answer writes over it.
  env.byId("cfgHost").value = "0.0.0.0";
  await landConfig();
  assert.equal(env.byId("cfgHost").value, "127.0.0.1", "the answer renders the file's own value");
  assert.equal(env.byId("cfgServerSave").textContent, "Save",
               "the section reads clean, so nothing would warn that the edit was dropped");
  // Positive control: the same field, the same typing, once the answer has landed.
  for (const id of FIELDS) assert.equal(env.byId(id).disabled, false, `${id} stayed held after the answer`);
  env.byId("cfgHost").value = "0.0.0.0";
  env.byId("cfgSecServer").emit("input", {});
  assert.equal(env.byId("cfgServerSave").textContent, "Save *", "an edit after the answer is unsaved");
});

test("FD2-1: a reopen holds the port rows it is still showing, and the answer re-renders them free",
  async () => {
    dlg.removeAttribute("open");
    holdConfig = false; failConfig = false;
    env.byId("settingsBtn").emit("click", {});
    await settle();
    const row = () => env.byId("cfgPortsBody").querySelectorAll("tr")[0]._fields;
    assert.equal(row().baudInput.disabled, false, "the loaded dialog holds nothing");
    env.byId("setClose").emit("click", {});
    await openHeld();   // the rows of the previous open are still on screen
    assert.equal(row().baudInput.disabled, true, "a port row was editable while the file was loading");
    assert.equal(row().eolSel.disabled, true, "the row's select too");
    await landConfig();
    assert.equal(row().baudInput.disabled, false, "the answer's own rows came back held");
  });

// ---- R61-1: the token field is live while loading, so the load keeps what was typed there ----

for (const branch of ["answered", "unreachable"]) {
  test(`R61-1: a token typed while /config is out survives the ${branch} load`, async () => {
    setToken(null);
    await openHeld();
    env.byId("cfgToken").value = "typed-secret";
    env.byId("cfgSecToken").emit("input", {});
    failConfig = branch === "unreachable";
    await landConfig();
    assert.equal(env.byId("cfgToken").value, "typed-secret", "the load wrote over the typing");
    assert.equal(env.byId("cfgTokenSave").textContent, "Save *", "and it still reads unsaved");
    env.byId("setClose").emit("click", {});
  });
}

test("R61-1 positive control: an untouched token field takes the token stored meanwhile", async () => {
  setToken(null);
  env.byId("cfgToken").value = "left from before";
  await openHeld();
  assert.equal(env.byId("cfgToken").value, "", "rendered at the click");
  setToken("stored-meanwhile");
  await landConfig();
  assert.equal(env.byId("cfgToken").value, "stored-meanwhile");
  setToken(null);
});

// ---- FD2-2 --------------------------------------------------------------------------------

// Records the focus() calls made on the token box while keeping the stub's activeElement.
const tokenFocus = [];
{
  const el = env.byId("cfgToken");
  const orig = el.focus.bind(el);
  el.focus = () => { tokenFocus.push(1); orig(); };
}

// A daemon that refuses: the read-only branch, up to 4 s after the click.
async function openUnreachable() {
  dlg.removeAttribute("open");
  holdConfig = false; failConfig = true;
  tokenFocus.length = 0;
  env.byId("settingsBtn").emit("click", {});
}

test("FD2-2: the unreachable answer leaves focus where the user put it inside the dialog", async () => {
  await openUnreachable();
  env.byId("setClose").focus();   // the user has tabbed to Close while the daemon hangs
  await settle();
  assert.match(env.byId("cfgOffline").textContent, /^daemon unreachable/, "not the read-only branch");
  assert.deepEqual(tokenFocus, [], "the late answer pulled the caret to the token box");
  assert.equal(env.document.activeElement, env.byId("setClose"));
});

test("FD2-2: with nothing in the dialog focused, the unreachable answer still moves no focus", async () => {
  // Owner check 2026-09-15: with the fields held, the moved caret landed on the token box
  // below the fold and hid the banner that said why nothing worked.
  await openUnreachable();
  env.byId("setClose").blur();   // focus sits on the page body, outside the dialog
  await settle();
  assert.match(env.byId("cfgOffline").textContent, /^daemon unreachable/, "not the read-only branch");
  assert.equal(env.byId("cfgOffline").hidden, false);
  assert.deepEqual(tokenFocus, [], "the late answer pulled the caret to the token box");
});

test("FD2-2 control: a successful load keeps the banner hidden", async () => {
  dlg.removeAttribute("open");
  holdConfig = false; failConfig = false;
  env.byId("settingsBtn").emit("click", {});
  await settle();
  assert.equal(env.byId("cfgOffline").hidden, true, "the banner shows against a live daemon");
});
