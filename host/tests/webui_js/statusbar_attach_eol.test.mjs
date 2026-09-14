// statusbar.js attach dialog: the line ending and serial number picked there reach the
// POST /ports body, or the port takes PortAttach's defaults and the pick is silently lost.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

let config = { ports: [], restart_required: false };
const requests = [];        // [method, path, body]
globalThis.fetch = async (path, opt = {}) => {
  const method = opt.method || "GET";
  const p = String(path);
  requests.push([method, p, opt.body ? JSON.parse(opt.body) : null]);
  if (p === "/devices") return { ok: true, status: 200, json: async () => ({ devices: [] }) };
  if (p === "/config") return { ok: true, status: 200, json: async () => config };
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

const { initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();

test("the line-ending select is filled with every choice the daemon accepts", () => {
  assert.deepEqual(env.byId("attachEol").children.map((o) => o.value), ["lf", "crlf", "none"]);
});

async function attach({ alias, device, eol, serial, save = false }) {
  requests.length = 0;
  env.byId("attachBtn").emit("click", {});
  await tick(0);
  env.byId("devSel").value = "custom";
  env.byId("devSel").emit("change", {});
  env.byId("devCustom").value = device;
  env.byId("baudSel").value = "115200";     // the stub <select> has no default selection
  env.byId("aliasInput").value = alias;
  if (eol !== undefined) env.byId("attachEol").value = eol;
  if (serial !== undefined) env.byId("attachSerial").value = serial;
  env.byId("saveToConfig").checked = save;
  env.byId("dlgAttach").emit("click", {});
  await tick(0);
  await tick(0);
  return requests;
}

const posted = (path, method = "POST") =>
  requests.find(([m, p]) => m === method && p === path)?.[2];

test("the chosen line ending rides on the attach", async () => {
  await attach({ alias: "brd", device: "socket://127.0.0.1:9900", eol: "crlf" });
  assert.deepEqual(posted("/ports"), { alias: "brd", device: "socket://127.0.0.1:9900",
                                       baud: 115200, eol: "crlf" });
});

test("eol is sent even when it is the default, so the field is never a lie", async () => {
  await attach({ alias: "brd", device: "COM7", eol: "lf" });
  assert.equal(posted("/ports").eol, "lf");
  await attach({ alias: "brd", device: "COM7", eol: "none" });
  assert.equal(posted("/ports").eol, "none");
});

test("a blank serial number is omitted, not sent as an empty string", async () => {
  await attach({ alias: "brd", device: "COM7", eol: "lf", serial: "   " });
  assert.equal(Object.hasOwn(posted("/ports"), "serial_number"), false,
    "\"\" is not \"no serial number\" to the daemon: it would match no device at all");

  await attach({ alias: "brd", device: "COM7", eol: "lf", serial: " 0031A " });
  assert.equal(posted("/ports").serial_number, "0031A", "and a real one goes out trimmed");
});

test("save to config writes the values the attach just used", async () => {
  // The 2026-09-04 defect in one line: attaching as crlf and saving lf means the port comes
  // back on the next daemon start with the line ending the user did not pick.
  await attach({ alias: "brd", device: "COM7", eol: "crlf", serial: "0031A", save: true });
  const saved = posted("/config/ports", "PUT");
  assert.ok(saved, "the save-to-config box must PUT the ports list");
  assert.deepEqual(saved.ports, [{ alias: "brd", device: "COM7", baud: 115200, autoconnect: true,
                                   eol: "crlf", serial_number: "0031A" }]);
});

test("the dialog does not carry the last attach's values into the next one", async () => {
  await attach({ alias: "brd", device: "COM7", eol: "crlf", serial: "0031A" });
  env.byId("attachBtn").emit("click", {});
  await tick(0);
  assert.equal(env.byId("attachSerial").value, "",
    "a serial number left over from the last attach would bind the wrong device");
  assert.equal(env.byId("attachEol").value, "lf", "and the eol resets to the daemon's default");
});
