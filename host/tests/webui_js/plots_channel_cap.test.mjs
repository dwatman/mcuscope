// plots.js: MAX_CHANNELS is the DOM and heap bound against a device that emits rotating
// channel names, and no test drove it - the cap could have been raised or dropped silently.
// Its own file, because the cap counts channels across every chart and any earlier test
// would spend part of the budget.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

const env = installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

await import(webuiUrl("state.js"));
const { charts, plotIngest } = await import(webuiUrl("plots.js"));

const MAX_CHANNELS = 64;   // plots.js is not required to export it; a change must fail here

test("a device emitting more names than the cap creates exactly MAX_CHANNELS channels", () => {
  const warned = [];
  const real = console.warn;
  console.warn = (...a) => warned.push(a);
  try {
    for (let i = 0; i < MAX_CHANNELS + 5; i++) {
      plotIngest({ id: i + 1, ts: 1000 + i, port: "p1", chan: "event",
                   raw: `!p ${i + 1} ch${i}=${i}` });
    }
  } finally {
    console.warn = real;
  }
  const chart = charts.get("adhoc");
  assert.equal(chart.ys.size, MAX_CHANNELS,
    "every name past the cap must be dropped, not charted");
  assert.equal(chart.names.length, MAX_CHANNELS);
  assert.ok(!chart.ys.has(`ch${MAX_CHANNELS}`), "the first name past the cap is the one dropped");
  assert.equal(chart.xsHost.length, MAX_CHANNELS + 5,
    "the samples themselves still land: the cap drops the channel, not the row");
  assert.equal(warned.length, 1, "the cap is reported once, not once per dropped sample");
  assert.equal(env.byId("plotCount").textContent, `${MAX_CHANNELS} channels (limit ${MAX_CHANNELS} reached)`,
    "and it is visible in the panel, not only in the console");
});
