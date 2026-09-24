// index.html read as text: the accessible roles and names on the status and error surfaces
// (E-11). The stub has no accessibility tree; what these announce needs a real browser.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { webuiDir } from "./dom_stub.mjs";

const html = readFileSync(join(webuiDir(), "index.html"), "utf8");

const tagWithId = (id) => {
  const m = new RegExp(`<[a-z]+\\b[^>]*\\bid="${id}"[^>]*>`).exec(html);
  assert.ok(m, `no element with id ${id}`);
  return m[0];
};

test("E-11: every inline error slot is an alert", () => {
  const slots = [...html.matchAll(/<[a-z]+\b[^>]*class="[^"]*\binline-err\b[^"]*"[^>]*>/g)].map((m) => m[0]);
  assert.ok(slots.length >= 9, `found only ${slots.length} inline-err slots`);
  const silent = slots.filter((t) => !/\brole="alert"/.test(t));
  assert.deepEqual(silent, [], "a refusal written here is not announced");
});

test("E-11: the command result is a status region; the two bar inputs have names", () => {
  assert.match(tagWithId("cmdResult"), /\brole="status"/);
  for (const id of ["cmdInput", "markerInput"]) {
    const m = /\baria-label="([^"]*)"/.exec(tagWithId(id));
    assert.ok(m && m[1].trim(), `${id} is named by its placeholder only`);
  }
});
