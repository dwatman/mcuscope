// dom_stub.mjs: the browser behaviour the stub promises, where a laxer fake let UI defects pass.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, FakeEl } from "./dom_stub.mjs";

const env = installDom();

function select(values) {
  const sel = new FakeEl("select");
  for (const v of values) { const o = new FakeEl("option"); o.value = v; o.textContent = v; sel.appendChild(o); }
  return sel;
}

test("a <select> reads a value no option carries as empty, as a browser does", () => {
  const sel = select(["a", "b"]);
  assert.equal(sel.value, "a", "the first option is selected by default");
  sel.value = "b";
  assert.equal(sel.value, "b");
  sel.value = "gone";
  assert.equal(sel.value, "");
  sel.appendChild(Object.assign(new FakeEl("option"), { value: "c" }));
  assert.equal(sel.value, "a", "a change to the options selects the first again");
  sel.textContent = "";
  assert.equal(sel.value, "");
});

test("index.html's selects come with their static options", () => {
  assert.equal(env.byId("cmdEol").tagName, "SELECT");
  assert.deepEqual(env.byId("cmdEol").options.map((o) => o.value), [""]);
  assert.equal(env.byId("baudSel").value, "115200", "the option marked selected");
  assert.equal(env.byId("baudSel").options.at(-1).value, "custom");
  assert.equal(env.byId("baudSel").options[0].value, "9600", "an option with no value reads its text");
});

test("closest() walks up from the element itself and matches its selector", () => {
  const sec = new FakeEl("section");
  sec.className = "cfg-sec";
  const field = sec.appendChild(new FakeEl("div")).appendChild(new FakeEl("input"));
  assert.equal(field.closest(".cfg-sec"), sec);
  assert.equal(field.closest(".other"), null);
  assert.equal(sec.closest("section.cfg-sec"), sec);
});

test("cloneNode keeps data-* and attributes", () => {
  const b = new FakeEl("button");
  b.dataset.chan = "debug";
  b.setAttribute("aria-pressed", "true");
  const c = new FakeEl("div");
  c.appendChild(b);
  const copy = c.cloneNode(true).children[0];
  assert.equal(copy.dataset.chan, "debug");
  assert.equal(copy.getAttribute("aria-pressed"), "true");
  copy.dataset.chan = "cmd";
  assert.equal(b.dataset.chan, "debug", "the copy's data-* is its own");
});
