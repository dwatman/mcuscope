// digital.js (registry class 76): the lane repaint key and the gutter readout cache.
//
// - A lane repainted only when its own samples marked it dirty, but every lane draws against
//   one shared right edge: a lane whose stream went quiet kept its old picture while a sibling
//   stream scrolled the edge on, and a theme toggle never reached a lane with nothing new.
// - The readout's cached value was not updated while paused, so a resume showed the value at
//   the pause beside a waveform that had moved on.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { state, root } = await import(webuiUrl("state.js"));
const D = await import(webuiUrl("digital.js"));

// A recording 2d context: the stub's own one draws nothing it can report.
function record(lane) {
  const calls = [];
  lane.canvas.getContext = () => new Proxy({}, {
    get: (t, k) => (k in t ? t[k] : (...a) => calls.push([k, ...a])),
    set: (t, k, v) => { t[k] = v; return true; },
  });
  return calls;
}

const A = { kind: "enum", name: "a", labels: [[0, "IDLE"], [1, "RUN"]] };
const B = { kind: "enum", name: "b", labels: [[0, "OFF"], [1, "ON"]] };

function fresh() {
  D.setDigitalPaused(false);
  D.clearAllDigital();
  state.timeMode = "host";
}

test("a lane whose stream went quiet redraws when a sibling stream moves the shared edge", () => {
  fresh();
  D.digitalIngest("p1", [["a", 0, A]], { host: 1000, tick: 1 });
  D.digitalIngest("p1", [["b", 1, B]], { host: 1000, tick: 1 });
  const la = D.digitalLanes.get("p1|a"), lb = D.digitalLanes.get("p1|b");
  la.canvas.clientWidth = 300; lb.canvas.clientWidth = 300;
  D.redrawDigital();
  const calls = record(lb);
  // Stream a keeps sending, b has stopped: 30 s window on 300 px, so 10 px a second.
  D.digitalIngest("p1", [["a", 1, A]], { host: 1020, tick: 20001 });
  D.redrawDigital();
  const labels = calls.filter((c) => c[0] === "fillText");
  assert.equal(labels.length, 1, "the quiet lane was not redrawn against the moved edge");
  assert.equal(labels[0][1], "ON");
  assert.equal(labels[0][2], 200, "its held level must run from 1000 s (100 px) to the edge (300 px)");
});

test("a lane with nothing new redraws on a theme toggle, paused or not", () => {
  fresh();
  root.setAttribute("data-theme", "dark");
  D.digitalIngest("p1", [["a", 1, A]], { host: 1000, tick: 1 });
  const la = D.digitalLanes.get("p1|a");
  la.canvas.clientWidth = 300;
  D.setDigitalPaused(true);
  const calls = record(la);
  D.redrawDigital();
  const before = calls.filter((c) => c[0] === "clearRect").length;
  root.setAttribute("data-theme", "light");
  D.redrawDigital();
  const after = calls.filter((c) => c[0] === "clearRect").length;
  assert.equal(after, before + 1, "the lane kept the old theme's gridlines");
  D.redrawDigital();
  assert.equal(calls.filter((c) => c[0] === "clearRect").length, after,
    "and an idle tick after that repaint must not paint again");
  D.setDigitalPaused(false);
});

test("a resume shows the value that arrived while paused, not the value at the pause", () => {
  fresh();
  D.digitalIngest("p1", [["a", 0, A]], { host: 2000, tick: 1 });
  const la = D.digitalLanes.get("p1|a");
  la.canvas.clientWidth = 300;
  D.redrawDigital();
  assert.equal(la.valEl.textContent, "IDLE");
  D.setDigitalPaused(true);
  D.digitalIngest("p1", [["a", 1, A]], { host: 2001, tick: 1001 });   // then the stream stops
  D.redrawDigital();
  assert.equal(la.valEl.textContent, "IDLE", "paused, the readout holds the frozen value");
  D.setDigitalPaused(false);
  assert.equal(la.valEl.textContent, "RUN", "resumed, the readout lags the waveform");
});

test("a lane born while paused reads its value once resumed", () => {
  fresh();
  D.digitalIngest("p1", [["a", 0, A]], { host: 3000, tick: 1 });
  D.setDigitalPaused(true);
  D.digitalIngest("p1", [["b", 1, B]], { host: 3001, tick: 1001 });
  const lb = D.digitalLanes.get("p1|b");
  lb.canvas.clientWidth = 300;
  D.setDigitalPaused(false);
  assert.equal(lb.valEl.textContent, "ON", "a lane born while paused stayed blank after resume");
});
