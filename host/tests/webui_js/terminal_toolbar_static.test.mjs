// The pane toolbar's one-row budget. Two panes beside the default sidebar at a 1600 px window
// are about 585 px, and the row needed about 600, so `clear` dropped to a row of its own. The
// stub cannot lay it out; measured in headless Firefox, the row now breaks below about 531 px.
// These pin what that figure rests on, and that a narrower pane wraps export and clear together.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (f) => readFileSync(new URL("../../mcuscope/webui/" + f, import.meta.url), "utf8");
const css = read("style.css");
const tpl = read("index.html").match(/<template id="paneTpl">([\s\S]*?)<\/template>/)[1];
const rule = (sel) => {
  const m = css.match(new RegExp("^" + sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " \\{([^}]*)\\}", "m"));
  assert.ok(m, sel + " rule");
  return m[1];
};

test("the row's spacing: 6 px gaps and a 10 px left padding, the close button's reserve kept", () => {
  const tb = rule(".term-toolbar");
  assert.match(tb, /gap: 6px;/);
  assert.match(tb, /padding: 7px 10px;/);
  assert.match(tb, /flex-wrap: wrap;/, "a pane narrower than the budget must wrap, not overflow");
  assert.match(rule(".pane .term-toolbar"), /padding-right: 32px;/);
  assert.match(rule(".pane .closepane"), /position: absolute;/);
  assert.match(rule(".chk"), /padding: 2px 5px;/);
});

test("the regex box breaks lines at 60 px, rests at 90 and widens only into free space on focus", () => {
  const g = rule(".match-group");
  assert.match(g, /flex: 1000000 0 60px;/, "the basis is what flex-wrap breaks on");
  assert.match(g, /max-width: 90px;/);
  assert.match(rule(".match-group:focus-within"), /max-width: 260px;/);
});

test("export and clear are one flex item, right-aligned when it wraps; close stays outside it", () => {
  const group = tpl.match(/<div class="pane-actions">([\s\S]*?)<\/div>/);
  assert.ok(group, "the template's action group");
  const buttons = [...group[1].matchAll(/class="iconbtn (\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(buttons, ["exportpane", "clear"]);
  assert.ok(!/closepane/.test(group[1]), "the close button is pinned, not wrapped");
  assert.match(tpl, /<\/div>\s*<button class="iconbtn closepane"/, "close follows the group directly");
  const actions = rule(".pane-actions");
  assert.match(actions, /display: flex;/);
  assert.match(actions, /margin-left: auto;/);
  assert.match(css, /\.term-toolbar \.exportpane[^{]*\{ padding: 4px 7px; \}/);
});
