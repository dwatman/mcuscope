// style.css and index.html, read as text: the contrast the theme promises and the accessible
// names the dialogs carry. Nothing here needs a DOM; a stub could not compute a colour anyway.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { webuiDir } from "./dom_stub.mjs";

const css = readFileSync(join(webuiDir(), "style.css"), "utf8");
const html = readFileSync(join(webuiDir(), "index.html"), "utf8");

function themeVars(name) {
  const block = css.split(`:root[data-theme="${name}"] {`)[1].split("}")[0];
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}
function lum(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function ratio(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

for (const theme of ["light", "dark"]) {
  test(`${theme}: faint text passes AA on every surface and stays quieter than dim`, () => {
    const v = themeVars(theme);
    for (const bg of ["bg", "panel", "panel-2"]) {
      const r = ratio(v["text-faint"], v[bg]);
      assert.ok(r >= 4.5, `--text-faint on --${bg} is ${r.toFixed(2)}:1`);
    }
    assert.ok(ratio(v["text-dim"], v.panel) > ratio(v["text-faint"], v.panel) * 1.15,
      "a hint must not read as loud as a label");
  });
}

test("the light accent passes AA as text on white and under white text", () => {
  const v = themeVars("light");
  assert.ok(ratio(v.accent, v.panel) >= 4.5, `accent on panel ${ratio(v.accent, v.panel).toFixed(2)}`);
  assert.ok(ratio("#ffffff", v.accent) >= 4.5);
});

test("a lit window button or zoom chip is dark text on the dark accent, white on the light one", () => {
  assert.match(css, /\.plot-win button\.zoom \{ color: #04222a;/);
  assert.match(css, /\.plot-win button\.on \{ background: var\(--accent\); color: #04222a; \}/);
  assert.match(css, /:root\[data-theme="light"\] \.plot-win button\.on, :root\[data-theme="light"\] \.plot-win button\.zoom \{ color: #fff; \}/);
  assert.ok(ratio("#04222a", themeVars("dark").accent) >= 4.5);
});

test("the light theme has its own dialog scrim, and the chip hover card uses --shadow", () => {
  assert.match(css, /:root\[data-theme="light"\] dialog::backdrop \{ background: rgba\(20,30,40,\.35\); \}/);
  const tip = css.split(".chip[data-tip]:hover")[1].split("}")[0];
  assert.match(tip, /box-shadow: var\(--shadow\)/);
});

test("every dialog is named by its heading, and every describedby id exists", () => {
  const dialogs = [...html.matchAll(/<dialog id="(\w+)"([^>]*)>/g)];
  assert.deepEqual(dialogs.map((m) => m[1]), ["attachDlg", "sessionDlg", "settingsDlg", "exportDlg"]);
  for (const [, id, attrs] of dialogs) {
    const by = (attrs.match(/aria-labelledby="(\w+)"/) || [])[1];
    assert.ok(by, `${id} has no aria-labelledby`);
    assert.match(html, new RegExp(`<h3 id="${by}">`), `${id} points at a missing heading`);
  }
  const described = [...html.matchAll(/aria-describedby="(\w+)"/g)].map((m) => m[1]);
  assert.ok(described.length >= 12);
  for (const id of described) assert.match(html, new RegExp(`id="${id}"`), `aria-describedby="${id}" names nothing`);
});

test("the dialogs autofocus their first field, and the session button does not use window.prompt", () => {
  for (const id of ["devSel", "sesName", "cfgHost"]) assert.match(html, new RegExp(`id="${id}"[^>]*autofocus`));
  const statusbar = readFileSync(join(webuiDir(), "statusbar.js"), "utf8");
  assert.doesNotMatch(statusbar, /window\.prompt/);
});

test("section Saves start plain, the token confirmation is a note, the divider is focusable", () => {
  for (const id of ["cfgServerSave", "cfgStorageSave", "cfgUpdateSave", "cfgTokenSave", "cfgPortsSave"]) {
    assert.match(html, new RegExp(`<button class="btn" id="${id}">Save</button>`), id);
  }
  assert.match(html, /<div class="inline-note" id="cfgTokenNote" role="status"><\/div>/);
  assert.match(html, /id="resizer" tabindex="0" role="separator"/);
  assert.match(html, /<button class="reopen" id="reopenBtn"/);
});
