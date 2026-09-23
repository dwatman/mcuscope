// digital.js drawBits / drawEnum: a lane toggling at 100 Hz is about 3000 segments across a
// 230 px lane in a 30 s window, redrawn 5 times a second. Every sub-pixel segment was drawn,
// and an enum lane paid a beginPath/stroke per segment. Runs of segments narrower than
// MIN_SEG_PX now draw as one "busy" block (timewindow.mergeNarrow), and an enum lane strokes
// one path. The canvas call counts below are the structural check.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl } from "./dom_stub.mjs";

installDom();
const { state } = await import(webuiUrl("state.js"));
const { mergeNarrow } = await import(webuiUrl("timewindow.js"));
const dg = await import(webuiUrl("digital.js"));

// A 2d context that counts every call by name.
function countingCtx() {
  const n = {};
  const ctx = new Proxy({ n, measureText: () => ({ width: 10 }) }, {
    get(t, k) {
      if (k in t) return t[k];
      return (...a) => { n[k] = (n[k] || 0) + 1; };
    },
    set() { return true; },
  });
  return ctx;
}

function laneWith(name, ch, values, dt) {
  dg.clearAllDigital();
  state.timeMode = "host";
  values.forEach((v, i) => dg.digitalIngest("p1", [[name, v, ch]], { host: 1000 + i * dt, tick: i }, "p1|s0"));
  const lane = dg.digitalLanes.get("p1|" + name);
  const ctx = countingCtx();
  lane.canvas.getContext = () => ctx;
  lane.canvas.clientWidth = 230;
  dg.redrawDigital();
  return ctx.n;
}

const BITS = { kind: "bits", name: "io", labels: null };
const ENUM = { kind: "enum", name: "st", labels: [[0, "IDLE"], [1, "RUN"], [2, "FAULT"]] };

test("a bits lane toggling far faster than its pixels draws a bounded number of edges", () => {
  const n = laneWith("clk", BITS, Array.from({ length: 3000 }, (_, i) => i % 2), 0.01);
  assert.ok((n.lineTo || 0) < 60, `lineTo ${n.lineTo} for a 230 px lane`);
  assert.ok((n.fillRect || 0) < 30, `fillRect ${n.fillRect}`);
  assert.equal(n.stroke, 2, "one stroke for the gridlines, one for the waveform");
});

test("an enum lane strokes one path, however many segments it holds", () => {
  const n = laneWith("st", ENUM, Array.from({ length: 3000 }, (_, i) => i % 3), 0.01);
  assert.equal(n.stroke, 2, "one stroke for the gridlines, one for the whole bus");
  assert.equal(n.save || 0, 0, "a sub-pixel segment has no label, so no clip");
  assert.ok((n.lineTo || 0) < 60, `lineTo ${n.lineTo}: every sub-pixel crossing was drawn`);
});

test("a slow lane still draws every transition and labels its segments", () => {
  // Three segments over the 30 s window (the newest level starts at the right edge).
  const n = laneWith("st", ENUM, [0, 1, 2, 1], 7);
  assert.equal(n.stroke, 2);
  assert.equal(n.fillText, 3, "each wide segment carries its label");
  assert.equal(n.save, 3);
  const b = laneWith("io", BITS, [0, 1, 0, 1], 7);
  assert.ok(b.lineTo >= 7, `a slow square wave lost its edges (lineTo ${b.lineTo})`);
});

test("mergeNarrow merges runs of two or more narrow segments, never across a break", () => {
  const seg = (i, x0, x1) => ({ i, x0, x1 });
  const vs = [0, 1, 0, 1, null, 1, 0, 1];
  const segs = [seg(0, 0, 0.5), seg(1, 0.5, 1), seg(2, 1, 10), seg(3, 10, 10.5),
                seg(4, 10.5, 11), seg(5, 11, 11.4), seg(6, 11.4, 11.8), seg(7, 11.8, 30)];
  assert.deepEqual(mergeNarrow(segs, vs, 1.5), [
    { busy: true, i: 1, x0: 0, x1: 1 },
    seg(2, 1, 10),
    seg(3, 10, 10.5),                     // a lone narrow segment is still drawn as itself
    seg(4, 10.5, 11),                     // a break is never merged
    { busy: true, i: 6, x0: 11, x1: 11.8 },
    seg(7, 11.8, 30),
  ]);
});
