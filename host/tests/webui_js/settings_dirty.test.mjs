// settings.js: unsaved edits are marked per section and never discarded without asking.
//
// The stub has no event bubbling, so an edit is "set the field, emit input on its section",
// which is what the browser delivers to the section's listener.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick, FakeEl } from "./dom_stub.mjs";

const env = installDom();

const CONFIG = {
  path: "/tmp/mcuscope.toml", exists: true, restart_required: false, token_set: false,
  server: { host: "127.0.0.1", port: 8558 },
  storage: { db_path: "", retention_days: 7, max_db_bytes: 0, min_sessions: 1, auto_session: true },
  ports: [{ alias: "mcu", device: "/dev/ttyACM0", baud: 115200, eol: "lf", autoconnect: true, identify: true }],
  update: { check: true },
};

const puts = [];
let failPut = null;
globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);
  if ((opt.method || "GET") === "PUT") {
    puts.push({ url: u, body: JSON.parse(opt.body) });
    if (failPut && u.includes(failPut)) {
      return { ok: false, status: 500, json: async () => ({ error: "disk full" }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  if (u.includes("/devices")) return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  if (u.includes("/sessions")) return { ok: true, status: 200, json: async () => ({ sessions: [] }) };
  if (u.includes("/status")) return { ok: true, status: 200, json: async () => ({ db_size_bytes: 0 }) };
  if (u.includes("/plotjuggler")) return { ok: true, status: 200, json: async () => ({ enabled: false, dest: "127.0.0.1:9870" }) };
  return { ok: true, status: 200, json: async () => CONFIG };
};

let confirms = [];
let answer = false;
globalThis.confirm = (msg) => { confirms.push(msg); return answer; };

const { initSettings, dirtySections } = await import(webuiUrl("settings.js"));
const dlg = env.byId("settingsDlg");
const isOpen = () => dlg.hasAttribute("open");

function edit(sec, id, value, prop = "value") {
  env.byId(id)[prop] = value;
  env.byId(sec).emit("input", {});
}
const saveBtn = (id) => ({ text: env.byId(id).textContent, primary: env.byId(id).classList.contains("primary") });

async function open() {
  env.byId("settingsBtn").emit("click", {});
  for (let i = 0; i < 4; i++) await tick(0);
}
async function settle() { for (let i = 0; i < 4; i++) await tick(0); }

initSettings();

// Each test starts from a dialog just opened on the fixture config: opening re-renders every
// section, which is what clears the dirty marks an earlier test left.
async function fresh() {
  CONFIG.storage.retention_days = 7;
  puts.length = 0;
  failPut = null;
  confirms = []; answer = false;
  dlg.removeAttribute("open");
  await open();
}

test("a freshly opened dialog has nothing unsaved and plain Save buttons", async () => {
  await fresh();
  assert.equal(isOpen(), true);
  assert.deepEqual(dirtySections(), []);
  for (const id of ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgTokenSave", "cfgPortsSave"]) {
    assert.deepEqual(saveBtn(id), { text: "Save", primary: false }, id);
  }
});

test("an edit marks its section, and typing the saved value back clears it", async () => {
  await fresh();
  edit("cfgSecServer", "cfgHost", "0.0.0.0");
  assert.deepEqual(dirtySections(), ["Server"]);
  assert.deepEqual(saveBtn("cfgServerSave"), { text: "Save *", primary: true });
  assert.equal(env.byId("cfgSecServer").classList.contains("dirty"), true);
  assert.deepEqual(saveBtn("cfgStorageSave"), { text: "Save", primary: false }, "only the edited section");
  edit("cfgSecServer", "cfgHost", "127.0.0.1");
  assert.deepEqual(dirtySections(), []);
  assert.deepEqual(saveBtn("cfgServerSave"), { text: "Save", primary: false });
});

test("saving one section leaves another's unsaved edit marked", async () => {
  await fresh();
  edit("cfgSecServer", "cfgHost", "0.0.0.0");
  edit("cfgSecStorage", "cfgRetention", "30");
  assert.deepEqual(dirtySections(), ["Server", "Storage"]);
  CONFIG.storage.retention_days = 30;   // what the save wrote, as the re-read reports it
  env.byId("cfgStorageSave").emit("click", {});
  await settle();
  assert.ok(puts.some((p) => p.url.includes("/config/storage")));
  assert.deepEqual(dirtySections(), ["Server"], "the Storage save must not clean Server");
  assert.deepEqual(saveBtn("cfgStorageSave"), { text: "Save", primary: false });
  edit("cfgSecServer", "cfgHost", "127.0.0.1");
});

test("a failed save keeps the section marked", async () => {
  await fresh();
  edit("cfgSecUpdate", "cfgUpdateCheck", false, "checked");
  failPut = "/config/update";
  env.byId("cfgUpdateSave").emit("click", {});
  await settle();
  failPut = null;
  assert.equal(env.byId("cfgUpdateErr").textContent, "disk full");
  assert.deepEqual(dirtySections(), ["Updates"]);
  assert.deepEqual(saveBtn("cfgUpdateSave"), { text: "Save *", primary: true });
  edit("cfgSecUpdate", "cfgUpdateCheck", true, "checked");
});

test("removing a port row is an unsaved edit; an untouched new row is not", async () => {
  await fresh();
  env.byId("cfgPortAdd").emit("click", {});
  env.byId("cfgSecPorts").emit("input", {});
  assert.deepEqual(dirtySections(), [], "a blank + port row is dropped on save, so nothing is unsaved");
  const rows = env.byId("cfgPortsBody").querySelectorAll("tr");
  rows[1]._fields.aliasInput.value = "sbc";
  env.byId("cfgSecPorts").emit("input", {});
  assert.deepEqual(dirtySections(), ["Ports"]);
  rows[1]._fields.aliasInput.value = "";
  env.byId("cfgSecPorts").emit("input", {});
  assert.deepEqual(dirtySections(), []);
  const rm = rows[0].children[7].children[0];
  assert.equal(rm.textContent, "remove");
  rm.emit("click", {});
  assert.deepEqual(dirtySections(), ["Ports"], "the row is gone on screen but still saved");
  assert.deepEqual(saveBtn("cfgPortsSave"), { text: "Save *", primary: true });
});

// Unsaved edits in two sections: a removed port row and a typed token.
function editPortsAndToken() {
  env.byId("cfgPortsBody").querySelectorAll("tr")[0].children[7].children[0].emit("click", {});
  edit("cfgSecToken", "cfgToken", "s3cret");
}

test("the x with unsaved edits asks, names the sections, and stays open when declined", async () => {
  await fresh();
  editPortsAndToken();
  confirms = []; answer = false;
  env.byId("setClose").emit("click", {});
  assert.equal(confirms.length, 1);
  assert.equal(confirms[0], "Close Settings and discard unsaved changes to Access token, Ports?");
  assert.equal(isOpen(), true, "declining keeps the edits on screen");
});

test("Escape with unsaved edits asks too, and accepting closes", async () => {
  await fresh();
  editPortsAndToken();
  confirms = []; answer = true;
  let prevented = false;
  dlg.emit("cancel", { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true, "the native close must not bypass the question");
  assert.equal(confirms.length, 1);
  assert.equal(isOpen(), false);
});

test("reopening re-renders from the config, so nothing is unsaved and closing does not ask", async () => {
  await fresh();
  editPortsAndToken();
  answer = true;
  env.byId("setClose").emit("click", {});   // discard the edits
  assert.equal(isOpen(), false);
  await open();
  assert.deepEqual(dirtySections(), []);
  confirms = [];
  env.byId("setClose").emit("click", {});
  dlg.emit("cancel", { preventDefault() {} });
  assert.equal(confirms.length, 0);
  assert.equal(isOpen(), false);
});

test("Enter saves the section the field is in, and nothing from a section without a Save", async () => {
  await fresh();
  // A field inside its section, as index.html nests it; closest(".cfg-sec") walks up to it.
  const inSec = (tag, sec) => {
    const s = env.byId(sec);
    s.className = "cfg-sec";
    return s.appendChild(new FakeEl(tag));
  };
  const loose = new FakeEl("div").appendChild(new FakeEl("input"));   // in no section
  puts.length = 0;
  dlg.emit("keydown", { key: "Enter", target: inSec("INPUT", "cfgSecServer"), preventDefault() {} });
  await settle();
  assert.deepEqual(puts.map((p) => p.url), ["/config/server"]);
  puts.length = 0;
  dlg.emit("keydown", { key: "Enter", target: loose, preventDefault() {} });
  dlg.emit("keydown", { key: "Enter", target: inSec("BUTTON", "cfgSecStorage"), preventDefault() {} });
  await settle();
  assert.deepEqual(puts, [], "PlotJuggler has no section Save, and Enter on a button is that button's");
});

test("a saved token is confirmed in the note style, not the error slot", async () => {
  await fresh();
  edit("cfgSecToken", "cfgToken", "abc");
  env.byId("cfgTokenSave").emit("click", {});
  assert.equal(env.byId("cfgTokenNote").textContent, "saved; reconnecting stream");
  assert.deepEqual(dirtySections(), []);
  env.byId("cfgTokenClear").emit("click", {});
});
