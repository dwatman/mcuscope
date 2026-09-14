// statusbar.js: the /status poll hands the daemon's clock to the CAN table (D-5), so a page
// loaded onto a board silent for minutes ages its rows from now, not from the newest row.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, tick } from "./dom_stub.mjs";

const env = installDom();

const T0 = 50000;
globalThis.fetch = async (path) => {
  const body = String(path).includes("/status")
    ? { version: "1", uptime_s: 1, db_size_bytes: 0, session: null, write_errors: 0,
        ports: [], now: T0 + 600 }
    : {};
  return { ok: true, status: 200, json: async () => body };
};

const { state } = await import(webuiUrl("state.js"));
const C = await import(webuiUrl("can.js"));
const { refreshStatus } = await import(webuiUrl("statusbar.js"));
env.byId("sidebar").setAttribute("data-view", "both");
C.initCan();

test("a status poll's now ages a silent 10 Hz id to dead", async () => {
  for (let i = 0; i < 20; i++) {
    state.maxId = i + 1;
    C.canIngest({ id: i + 1, ts: T0 + i * 0.1, port: "p1", chan: "event", raw: `!can ${i} - 100 0${i % 10}` });
  }
  await refreshStatus();
  await tick(0);
  C.renderCan();
  const row = env.byId("canWrap").querySelectorAll("tr")
    .find((t) => !t.className.includes("bus-hdr") && t.querySelectorAll("td").length > 1);
  assert.equal(row.children[4].className, "age-dead", "the age was anchored on the newest row");
});
