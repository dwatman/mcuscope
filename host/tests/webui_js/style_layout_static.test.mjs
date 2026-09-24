// style.css read as text: the sidebar buttons the narrow layout cannot honour (E-12), and the
// marker divider text that must shrink to an ellipsis (D-11). The stub has no layout; what
// these render as needs a real browser.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { webuiDir } from "./dom_stub.mjs";

const css = readFileSync(join(webuiDir(), "style.css"), "utf8");

// The body of `@media <query> {`, braces matched.
function mediaBody(query) {
  const start = css.indexOf(`@media ${query} {`);
  assert.ok(start >= 0, `no @media ${query}`);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return [start, i + 1];
  }
  throw new Error("unbalanced @media block");
}
// [selector list, declarations] for each plain rule in `text`.
const rules = (text) => [...text.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  .map((m) => [m[1].trim(), m[2]]);
const hides = (decl) => /display\s*:\s*none/.test(decl);

test("E-12: the narrow layout hides the collapse and expand buttons, and only the narrow layout", () => {
  const [a, b] = mediaBody("(max-width: 860px)");
  const narrow = rules(css.slice(a, b));
  for (const id of ["#collapseBtn", "#popoutBtn"]) {
    assert.ok(narrow.some(([sel, decl]) => sel.split(",").map((s) => s.trim()).includes(id) && hides(decl)),
      `${id} is clickable where the single column ignores it`);
  }
  const wide = rules(css.slice(0, a) + css.slice(b));
  assert.ok(!wide.some(([sel, decl]) => /#collapseBtn|#popoutBtn/.test(sel) && hides(decl)),
    "hidden in the wide layout too, where they work");
});

test("D-11: the marker divider can shrink and its text ends in an ellipsis", () => {
  const all = rules(css);
  const decl = (sel) => {
    const r = all.find(([s]) => s === sel);
    assert.ok(r, `no rule for ${sel}`);
    return r[1];
  };
  assert.match(decl(".ln.marker .divider"), /min-width\s*:\s*0/,
    "a flex item's min-width auto keeps it as wide as its text");
  const text = decl(".ln.marker .divider-text");
  assert.match(text, /min-width\s*:\s*0/);
  assert.match(text, /overflow\s*:\s*hidden/);
  assert.match(text, /text-overflow\s*:\s*ellipsis/);
});
