// settings.js ports table: the device dropdown saves what was picked. It used to swap every
// enumerated device for its by-id path, so a config row meant to open "whatever is on
// /dev/ttyACM0" was silently pinned to one board; and a saved by-id path then had no
// option to match, so the row fell to "custom...".

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();
const BY_ID = "/dev/serial/by-id/usb-STMicroelectronics_STLINK-V3PWR_0031-if01";
const CONFIG = {
  path: "/tmp/config.toml",
  server: { bind: "127.0.0.1", port: 8558, token: "" },
  storage: { db_path: "/tmp/x.db", retention_days: 30, max_db_bytes: 0, auto_session: false },
  ports: [{ alias: "bound", device: BY_ID, baud: 115200, autoconnect: true },
          { alias: "loose", device: "/dev/ttyACM0", baud: 115200, autoconnect: true }],
  update: { check: false },
};
const puts = [];
globalThis.fetch = async (url, opt = {}) => {
  if ((opt.method || "GET") === "PUT") puts.push(JSON.parse(opt.body));
  if (String(url).includes("/devices")) {
    return { ok: true, status: 200, json: async () => ({ devices: [
      { device: "/dev/ttyACM0", by_id: BY_ID, description: "STLINK-V3PWR" },
      { device: "COM3", by_id: null, description: "" }] }) };
  }
  return { ok: true, status: 200, json: async () => CONFIG };
};
const { initSettings } = await import(webuiUrl("settings.js"));

test("a by-id device is listed plain and bound, and a saved row matches whichever it holds", async () => {
  initSettings();
  env.byId("settingsBtn").emit("click", {});
  await tick(0); await tick(0);
  const rows = env.byId("cfgPortsBody").querySelectorAll("tr");
  const [boundSel, looseSel] = rows.map((r) => r._fields.devSel);
  assert.deepEqual(boundSel.children.map((o) => o.value), ["/dev/ttyACM0", BY_ID, "COM3", "custom"]);
  assert.match(boundSel.children[1].textContent, /bound to this device/);
  assert.equal(boundSel.value, BY_ID, "the saved by-id row matches its bound option, not custom");
  assert.equal(looseSel.value, "/dev/ttyACM0", "the saved port-name row matches the plain option");
  env.byId("cfgPortsSave").emit("click", {});
  await tick(0);
  assert.deepEqual(puts[0].ports.map((p) => p.device), [BY_ID, "/dev/ttyACM0"],
    "saving rewrites neither: what was picked is what is saved");
});
