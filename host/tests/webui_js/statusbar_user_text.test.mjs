// statusbar.js: text from outside the page (a session name from REST, a device string and
// description) goes through state.js userText wherever the bar shows it. A U+202E must not turn
// `gpj.exe` into a displayed `exe.jpg`, and a long session name must not wrap the header.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { installDom, webuiUrl, webuiDir, tick } from "./dom_stub.mjs";

const env = installDom();
let status = null;
let devices = [];
globalThis.fetch = async (path) => ({ ok: true, status: 200,
  json: async () => (String(path).endsWith("/devices") ? { devices } : status) });

const { refreshStatus, initStatusbar } = await import(webuiUrl("statusbar.js"));
initStatusbar();
const btn = env.byId("sessionBtn");
const SPOOF = "\u202egpj.exe";
const SHOWN = "\u2068<U+202E>gpj.exe\u2069";

async function poll(over) {
  status = { version: "1", uptime_s: 0, db_size_bytes: 0, ports: [], write_errors: 0,
             session: null, ...over };
  await refreshStatus();
}

test("a named session's chip and title show the override instead of obeying it", async () => {
  await poll({ session: { id: 3, name: SPOOF, auto: false } });
  assert.equal(btn.textContent, "\u25a0 " + SHOWN);
  assert.ok(btn.title.includes(`"${SHOWN}" (id 3)`), btn.title);
  assert.ok(!btn.textContent.includes("\u202e") && !btn.title.includes("\u202e"));
});

test("the automatic run's title does too", async () => {
  await poll({ session: { id: 4, name: SPOOF, auto: true } });
  assert.equal(btn.textContent, "\u25cf session");
  assert.ok(btn.title.includes(`"${SHOWN}". Click`), btn.title);
});

test("a port chip's device text and hover, and the attach dialog's device list", async () => {
  await poll({ ports: [{ alias: "b1", device: SPOOF, resolved_device: SPOOF,
                         description: "desc\u200b", baud: 9600, connected: true }] });
  const chip = env.byId("ports").children[0];
  assert.ok(chip.textContent.includes(SHOWN), chip.textContent);
  assert.equal(chip.dataset.tip, "\u2068desc<U+200B>\u2069\n@9600");

  devices = [{ device: SPOOF, by_id: null, description: "x\u202ey" }];
  env.byId("attachBtn").emit("click", {});
  await tick(0);
  const opt = env.byId("devSel").children[0];
  assert.equal(opt.textContent, `${SHOWN}  -  \u2068x<U+202E>y\u2069`);
  assert.equal(opt.value, SPOOF, "the value attached is the device as listed, not its display");
});

test("the chip is one clipped row however long the name", () => {
  const css = readFileSync(join(webuiDir(), "style.css"), "utf8");
  const rule = /#sessionBtn\s*\{([^}]*)\}/.exec(css);
  assert.ok(rule, "no #sessionBtn rule");
  for (const decl of [/max-width:\s*\d/, /overflow:\s*hidden/, /text-overflow:\s*ellipsis/,
                      /white-space:\s*nowrap/]) {
    assert.match(rule[1], decl);
  }
});
