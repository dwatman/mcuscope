// theme.js must survive a localStorage that throws.
//
// Chromium with "block site data" set (and Safari private mode) throws SecurityError from the
// localStorage property itself, not just from setItem. initTheme runs during boot, so an
// unguarded read there took the whole page down before any panel rendered - no theme, no
// terminal, no plots. state.js, chrome.js and cmdbar.js all wrap their storage access; these
// were the two sites that did not.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();

// Both access paths throw, as the browser does when site data is blocked.
globalThis.localStorage = {
  getItem() { throw new Error("SecurityError: access is denied for this document"); },
  setItem() { throw new Error("SecurityError: access is denied for this document"); },
  removeItem() { throw new Error("SecurityError: access is denied for this document"); },
};

const { root } = await import(webuiUrl("state.js"));
const { initTheme } = await import(webuiUrl("theme.js"));

test("boot completes and still picks a theme when storage is blocked", () => {
  initTheme();
  assert.equal(root.getAttribute("data-theme"), "light",
    "with no readable saved choice the OS preference must drive the theme (stub: light)");
  assert.equal(env.byId("themeBtn").textContent, "☀");
});

test("toggling the theme still applies it when the write is refused", () => {
  env.byId("themeBtn").emit("click", {});
  assert.equal(root.getAttribute("data-theme"), "dark",
    "the failed persist took the applied theme down with it");
  assert.equal(env.byId("themeBtn").textContent, "☾");
});
