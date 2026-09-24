// plots.js: clearAllCharts moves the seed generation (plotSeedGen) that api.js's backfill gate
// reads.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();

const P = await import(webuiUrl("plots.js"));

test("clearAllCharts still moves it on its own", () => {
  const before = P.plotSeedGen();
  P.clearAllCharts();
  assert.notEqual(P.plotSeedGen(), before, "the chart clear's own bump was lost");
});
