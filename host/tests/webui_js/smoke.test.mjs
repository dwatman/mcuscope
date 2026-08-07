// Import smoke test: every web UI module must load under the DOM stub.
//
// Nothing else executed this code, so a syntax error or a top-level ReferenceError in a
// shipped .js file reached users unchallenged. That is what this catches, cheaply, across
// the whole module graph: an undefined name in an error path (the frozen-stream defect in
// 4d7b4ef) fails here the moment it is referenced at module scope, and the behavioural
// tests alongside cover the ones that only fire at runtime.

import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { installDom, webuiUrl, webuiDir } from "./dom_stub.mjs";

installDom();
// The status poll and the WS handshake fire during app.js's boot; keep them off the network.
globalThis.fetch = async () => { throw new Error("offline in tests"); };

// Leaves first, then the modules that import them, so a failure names the module that broke
// rather than the first importer of it.
const ORDER = ["timewindow.js", "pane.js", "freeze.js", "chrome.js", "state.js", "theme.js", "digital.js", "plots.js", "can.js", "cmdbar.js",
               "terminal.js", "settings.js", "statusbar.js", "api.js", "app.js"];

test("ORDER covers every shipped webui module", () => {
  const shipped = readdirSync(webuiDir()).filter((f) => f.endsWith(".js")).sort();
  assert.deepEqual(shipped, [...ORDER].sort(),
    "webui/*.js and the smoke list disagree; add the new module here");
});

for (const name of ORDER) {
  test(`${name} imports cleanly`, async () => {
    const mod = await import(webuiUrl(name));
    assert.ok(mod, `${name} produced no module namespace`);
  });
}

test("every module exposes its documented exports", async () => {
  const expect = {
    "state.js": ["state", "buffer", "hooks", "api", "pushBuffer", "lineTick", "nearestX"],
    "timewindow.js": ["spanFor", "timeWindow", "visibleRange", "firstAtOrAfter"],
    "pane.js": ["ALL_CHANS", "REGEX_BUDGET_MS", "newPaneModel"],
    "freeze.js": ["registerSurface", "anyLive", "pauseAll", "pauseAllLabel"],
    "chrome.js": ["colorFor", "saveColor", "rgbToHex", "openColorPicker",
                  "buildWindowButtons"],
    "api.js": ["connectWs", "setAuthFailed", "reconnectStream"],
    "plots.js": ["charts", "plotIngest", "clearAllCharts", "initPlots"],
    "can.js": ["canIngest", "renderCan", "canRows", "clearAllCan", "initCan"],
    "terminal.js": ["VIEW_MAX", "panes", "matches", "rebuild", "render", "scheduleFlush"],
    "statusbar.js": ["refreshStatus", "tickUptime", "fmtBytes", "flashDaemonError"],
    "theme.js": ["initTheme"],
  };
  for (const [name, keys] of Object.entries(expect)) {
    const mod = await import(webuiUrl(name));
    for (const k of keys) assert.ok(k in mod, `${name} no longer exports ${k}`);
  }
});
