// plots.js: a chart with no width when the theme changes must still be recoloured later.
//
// redrawPlots skips a zero-width chart (hidden section, collapsed chart) before it looks at
// the theme, and buildUplot advanced the theme stamp. With one stamp shared by every chart,
// a visible chart's rebuild moved it, and the collapsed one compared equal on expand and kept
// the old palette until some unrelated rebuild (a channel appearing, a colour edit) happened
// along. The stamp is per chart now, so the skip cannot swallow the change.
//
// Asserted on the uPlot instance identity rather than on colours: the DOM stub's
// getComputedStyle returns nothing, so both palettes resolve to the same fallbacks.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
globalThis.fetch = async () => { throw new Error("offline in tests"); };

const { root } = await import(webuiUrl("state.js"));
const { charts, plotIngest, redrawPlots, clearAllCharts } = await import(webuiUrl("plots.js"));

let nextId = 1;
let ts = 1000;
const ingest = (raw) => plotIngest({ id: nextId++, ts: (ts += 0.01), port: "p1",
                                     chan: "event", raw });

test("a chart collapsed across a theme toggle is rebuilt when it comes back", () => {
  clearAllCharts();
  root.setAttribute("data-theme", "dark");
  ingest("!pd 0 a:u2");
  ingest("!ps 0 3E8 0064");
  ingest("!pd 1 b:u2");
  ingest("!ps 1 3E8 0064");
  const visible = charts.get("s0"), collapsed = charts.get("s1");
  visible.canvasEl.clientWidth = 400;
  collapsed.canvasEl.clientWidth = 400;
  redrawPlots();
  const builtDark = collapsed.uplot;
  const visibleDark = visible.uplot;
  assert.ok(builtDark && visibleDark, "the fixture built no charts");

  // Collapse one, toggle the theme, let the loop run: only the visible chart can rebuild.
  collapsed.canvasEl.clientWidth = 0;
  root.setAttribute("data-theme", "light");
  redrawPlots();
  assert.notEqual(visible.uplot, visibleDark,
    "precondition: the visible chart must have seen the toggle, or this proves nothing");
  assert.equal(collapsed.uplot, builtDark, "a zero-width chart must not be rebuilt in place");

  // Expand it. The theme it was built for is stale, so it must rebuild now.
  collapsed.canvasEl.clientWidth = 400;
  redrawPlots();
  assert.notEqual(collapsed.uplot, builtDark,
    "the expanded chart kept the palette of the theme it was collapsed under");
  assert.equal(collapsed.theme, "light");
});

test("an unchanged theme rebuilds nothing", () => {
  const before = [...charts.values()].map((c) => c.uplot);
  redrawPlots();
  assert.deepEqual([...charts.values()].map((c) => c.uplot), before,
    "a redraw with no theme change and no new channel must reuse the uPlot instances");
});
