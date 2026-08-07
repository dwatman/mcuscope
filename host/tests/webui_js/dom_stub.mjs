// Minimal browser stand-ins so the web UI's ESM modules can be imported under `node --test`.
//
// The webui is plain ESM with no build step, but several modules touch the DOM at module
// scope (state.js takes document.documentElement, statusbar.js and settings.js resolve a
// <dialog> by id). installDom() must therefore run BEFORE the first dynamic import; the
// import statements in a test file are hoisted, so every test loads the UI with
// `await import(webuiUrl("..."))` instead.
//
// The fakes are deliberately thin: enough shape for the modules to load and for the pure
// logic to be driven end to end, not a DOM implementation. Where a real DOM would return
// null (querySelector with no match) these return a detached element instead, because the
// UI code assumes the elements declared in index.html exist and a null there would only
// test the stub.

import { fileURLToPath } from "node:url";

// pane.js is DOM-free, so it is safe to import statically here - before installDom().
import { newPaneModel } from "../../mcuscope/webui/pane.js";

const WEBUI = new URL("../../mcuscope/webui/", import.meta.url);

// URL of a webui module, for `await import(webuiUrl("api.js"))`.
export function webuiUrl(name) {
  return new URL(name, WEBUI).href;
}

export function webuiDir() {
  return fileURLToPath(WEBUI);
}

class FakeClassList {
  constructor() { this.set = new Set(); }
  add(...cs) { for (const c of cs) this.set.add(c); }
  remove(...cs) { for (const c of cs) this.set.delete(c); }
  contains(c) { return this.set.has(c); }
  toggle(c, force) {
    const on = force === undefined ? !this.set.has(c) : !!force;
    if (on) this.set.add(c); else this.set.delete(c);
    return on;
  }
  get value() { return [...this.set].join(" "); }
}

class FakeStyle {
  setProperty(k, v) { this[k] = v; }
  removeProperty(k) { delete this[k]; }
  getPropertyValue(k) { return this[k] === undefined ? "" : String(this[k]); }
}

// A "2d context": every drawing call is a no-op, so digital.js can render its lanes.
function fakeContext() {
  const noop = () => {};
  return new Proxy({ canvas: null, fillStyle: "", strokeStyle: "", lineWidth: 1, font: "",
                     textAlign: "", textBaseline: "", globalAlpha: 1 }, {
    get(t, k) {
      if (k in t) return t[k];
      return noop;                       // beginPath, moveTo, stroke, fillText, ...
    },
    set(t, k, v) { t[k] = v; return true; },
  });
}

// Matches only what the UI actually uses: ".class", "tag", "#id" and "tag.class".
function selectorTest(sel) {
  const parts = sel.trim().split(/(?=[.#])/);
  return (el) => parts.every((p) => {
    if (p[0] === ".") return el.className.split(/\s+/).includes(p.slice(1));
    if (p[0] === "#") return el.id === p.slice(1);
    return el.tagName === p.toUpperCase();
  });
}

export class FakeEl {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.id = "";
    this.className = "";
    this.children = [];
    this.parentNode = null;
    this.style = new FakeStyle();
    this.classList = new FakeClassList();
    this.dataset = {};
    this.attrs = new Map();
    this.handlers = new Map();
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.title = "";
    this.href = "";
    this.download = "";
    this.clientWidth = 0;
    this.clientHeight = 300;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this._text = "";
    if (this.tagName === "CANVAS") {
      this.width = 300; this.height = 150;
      const ctx = fakeContext(); ctx.canvas = this;
      this.getContext = () => ctx;
    }
  }

  // Assigning textContent is the UI's "empty me" idiom, so it clears children too; reading it
  // back concatenates the subtree, which is how the CAN table assertions read rendered cells.
  set textContent(v) { this._text = v === undefined || v === null ? "" : String(v); this.children = []; }
  get textContent() {
    if (!this.children.length) return this._text;
    return this.children.map((c) => c.textContent).join("");
  }

  appendChild(c) {
    this._text = "";
    // Appending a DocumentFragment splices its children in and leaves the fragment empty,
    // which is how render() gets one reflow per pane.
    if (c.tagName === "#DOCUMENT-FRAGMENT") {
      const kids = c.children;
      c.children = [];
      for (const k of kids) { k.parentNode = this; this.children.push(k); }
      return c;
    }
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs) { this.children = []; this._text = ""; for (const c of cs) this.appendChild(c); }
  remove() {
    const p = this.parentNode;
    if (!p) return;
    const i = p.children.indexOf(this);
    if (i >= 0) p.children.splice(i, 1);
    this.parentNode = null;
  }

  get firstElementChild() {
    // <template>.content.firstElementChild is cloned for each terminal pane; there is no
    // markup here, so hand back an element rather than undefined.
    return this.children[0] || new FakeEl("div");
  }
  get content() {
    if (!this._content) this._content = new FakeEl("#document-fragment");
    return this._content;
  }

  descendants(out = []) {
    for (const c of this.children) { out.push(c); c.descendants(out); }
    return out;
  }
  querySelectorAll(sel) { return this.descendants().filter(selectorTest(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || new FakeEl("div"); }

  cloneNode(deep) {
    const c = new FakeEl(this.tagName);
    c.className = this.className;
    c.id = this.id;
    c._text = this._text;
    if (deep) for (const ch of this.children) c.appendChild(ch.cloneNode(true));
    return c;
  }

  addEventListener(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const a = this.handlers.get(type) || [];
    const i = a.indexOf(fn);
    if (i >= 0) a.splice(i, 1);
  }
  // Test-side trigger for a handler the UI registered.
  emit(type, ev = {}) { for (const fn of [...(this.handlers.get(type) || [])]) fn(ev); }

  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  removeAttribute(k) { this.attrs.delete(k); }
  hasAttribute(k) { return this.attrs.has(k); }

  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  closest() { return null; }
  focus() {}
  blur() {}
  click() { this.emit("click", {}); }
  setPointerCapture() {}
  releasePointerCapture() {}
  scrollTo() {}
  showModal() { this.attrs.set("open", ""); }
  close() { this.attrs.delete("open"); }
}

// Install document / localStorage / matchMedia / timers and friends on globalThis.
// Returns the handles a test needs to drive or inspect the UI.
export function installDom() {
  const els = new Map();        // id -> element, so $("x") is stable across calls
  const byId = (id) => {
    if (!els.has(id)) { const el = new FakeEl("div"); el.id = id; els.set(id, el); }
    return els.get(id);
  };

  const documentElement = new FakeEl("html");
  const body = new FakeEl("body");

  const doc = {
    documentElement,
    body,
    hidden: false,
    handlers: new Map(),
    getElementById: byId,
    createElement: (tag) => new FakeEl(tag),
    createDocumentFragment: () => new FakeEl("#document-fragment"),
    createTextNode: (t) => { const e = new FakeEl("#text"); e.textContent = t; return e; },
    querySelector: (sel) => body.querySelector(sel),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    elementFromPoint: () => null,
    addEventListener(type, fn) {
      if (!this.handlers.has(type)) this.handlers.set(type, []);
      this.handlers.get(type).push(fn);
    },
    removeEventListener() {},
    emit(type, ev = {}) { for (const fn of [...(this.handlers.get(type) || [])]) fn(ev); },
  };

  const store = new Map();
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };

  // Timers are captured, not armed: api.js, can.js and plots.js each start a repeating
  // interval at module scope, which would keep the test process alive forever. Tests invoke
  // the captured callbacks when they want a tick.
  const intervals = [];
  const frames = [];
  const sockets = [];
  const blobs = [];

  globalThis.document = doc;
  globalThis.window = globalThis;
  globalThis.localStorage = localStorage;
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {},
                                   addListener() {}, removeListener() {} });
  globalThis.location = { protocol: "http:", host: "127.0.0.1:8765",
                          href: "http://127.0.0.1:8765/", origin: "http://127.0.0.1:8765" };
  globalThis.getComputedStyle = () => new FakeStyle();
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.prompt = () => null;
  globalThis.alert = () => {};
  globalThis.confirm = () => false;
  globalThis.requestAnimationFrame = (fn) => frames.push(fn);
  globalThis.cancelAnimationFrame = () => {};
  globalThis.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };
  globalThis.clearInterval = () => {};
  globalThis.Blob = class FakeBlob {
    constructor(parts, opt) { this.parts = parts || []; this.type = (opt && opt.type) || ""; }
    text() { return Promise.resolve(this.parts.join("")); }
  };
  URL.createObjectURL = (b) => { blobs.push(b); return "blob:fake/" + blobs.length; };
  URL.revokeObjectURL = () => {};
  globalThis.WebSocket = class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
      sockets.push(this);
    }
    send() {}
    close() { this.readyState = 3; }
  };
  // uPlot is a vendored global in index.html. Charts only build when the canvas has a
  // non-zero width, which a FakeEl never has, so this exists to keep the reference valid.
  globalThis.uPlot = class FakeUplot {
    constructor(opts, data) { this.opts = opts; this.data = data; this.width = 0; this.series = opts.series; }
    setData(d) { this.data = d; }
    setSize() {}
    setCursor() {}
    destroy() {}
    valToPos() { return 0; }
  };
  globalThis.uPlot.paths = { stepped: () => () => null };
  // digital.js joins uPlot's "plots" cursor-sync group; keep the subscriber so a test can
  // publish into it, the way a hovered chart would.
  const syncSubs = [];
  globalThis.uPlot.sync = () => ({ sub: (s) => syncSubs.push(s), subs: syncSubs });

  return { document: doc, body, documentElement, byId, localStorage, store,
           intervals, frames, sockets, blobs, syncSubs };
}

// A terminal pane that routeLiveRow / rebuild / render / flush can operate on, without
// index.html's <template>. The pane's own shape comes from the real constructor; what is
// left here is the fake elements, which are the stub's job and nobody else's.
export function makePane(over = {}) {
  const el = new FakeEl("div");
  const scrollEl = new FakeEl("div");
  const vlist = new FakeEl("div");
  scrollEl.appendChild(vlist);
  const els = {
    el, scrollEl, vlist,
    portSel: new FakeEl("select"),
    matchInput: new FakeEl("input"),
    pill: new FakeEl("span"),
    jumpBtn: new FakeEl("button"),
    shownEl: new FakeEl("span"),
  };
  return { ...newPaneModel({}, els), ...over };
}

// A capture row as the daemon serves it (SPEC 3.4 /lines).
export function makeRow(id, over = {}) {
  return { id, ts: 1000 + id * 0.001, port: "p1", chan: "debug", raw: "line " + id, ...over };
}

// Let queued microtasks and timer callbacks run.
export function tick(ms = 0) {
  return new Promise((r) => setTimeout(r, ms));
}
