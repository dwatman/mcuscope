// Hovering a line in a paused pane places the shared cursor at the tick its stamp reads. A line
// with no tick of its own reads `~N` from the anchors snapshotted at the pause (terminal.js
// anchorsFor); the cursor mapped it through the live store instead, which a long pause rotates
// past, so the cursor vanished off a line whose stamp still read an estimate.

import test from "node:test";
import assert from "node:assert/strict";
import { installDom, webuiUrl, makePane, makeRow } from "./dom_stub.mjs";

const env = installDom();
const { state, buffer, pushBuffer, tickAnchors } = await import(webuiUrl("state.js"));
await import(webuiUrl("can.js"));   // publishes the !can tick (hooks.canTick)
const P = await import(webuiUrl("plots.js"));
const D = await import(webuiUrl("digital.js"));
const T = await import(webuiUrl("terminal.js"));
const TW = await import(webuiUrl("timewindow.js"));

let nextId = 0;
const ingest = (raw, ts) => { const r = makeRow(++nextId, { ts, chan: "event", raw }); P.plotIngest(r); return r; };

test("a paused pane's estimated line places the cursor after the anchor store rotated", () => {
  state.timeMode = "tick";
  buffer.length = 0;
  tickAnchors.clear();
  ingest("!pd 3 e:u1:=0=OFF,1=ON", 300);
  ingest("!ps 3 000003E8 00", 300);   // tick 1000: OFF
  ingest("!ps 3 00001388 01", 304);   // tick 5000: ON, the live edge
  const lane = D.digitalLanes.get("p1|e");
  lane.canvas.clientWidth = 300;
  env.byId("digitalWrap").clientWidth = 340;
  pushBuffer(makeRow(++nextId, { ts: 301, chan: "event", raw: "!can 2000 - 100 01" }));
  const debug = makeRow(++nextId, { ts: 301.5, raw: "hello" });   // ~2500: OFF
  pushBuffer(debug);
  state.maxId = nextId;
  const pane = makePane();
  T.rebuild(pane);
  T.setAutoscroll(pane, false);
  for (let i = 0; i <= 10001; i++) TW.noteTickAnchor(tickAnchors, "p1", nextId + 1 + i, 305 + i, 9000 + i * 5000);
  T.render(pane);
  const ln = pane.vlist.children.find((el) => el.__row === debug);
  assert.match(ln.children[0].textContent, /^~/, "setup: the stamp is an estimate");
  try {
    D.redrawDigital();
    assert.equal(lane.valEl.textContent, "ON", "setup: the readout starts at the live edge");
    env.document.elementFromPoint = () => ln.children[0];   // a cell inside the line
    P.paneMouseMove({ clientX: 7, clientY: 7 });
    env.frames.splice(0).forEach((f) => f());
    assert.equal(lane.valEl.textContent, "OFF", "the cursor must sit at the line's estimate, 2500");
  } finally {
    P.paneMouseLeave();
    env.document.elementFromPoint = () => null;
    state.timeMode = "host";
  }
});
