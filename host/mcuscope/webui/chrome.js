// ---- chart chrome: the colour store and the shared window selector -------------------
//
// The analog charts and the digital lanes share a colour palette, a native colour picker
// with a documented Firefox workaround, and a 5s/30s/5m window selector. Here rather than
// in state.js, which every module reads, so colour persistence is not filed beside the
// auth token's retry budget.

const PLOT_WINDOWS = [[5, "5s"], [30, "30s"], [300, "5m"]];
// What a chart, the digital panel and the history seed all come up showing. Shared so
// the seed pulls the span the UI is about to draw, rather than its own guess at it.
export const PLOT_WINDOW_DEFAULT = 30;
const PLOT_COLORS = ["#46c8d8", "#e0a458", "#b48ce8", "#5bd18b",
                     "#ef7a5e", "#6fb2ff", "#d888c0", "#c7d05b"];
// One store, keyed by channel/lane name and shared by both panels, so a name keeps its colour
// across ports and reloads. Effective colour = saved override, else the name's palette slot.
// Null-prototyped, like PLOT_TYPES in plots.js: the keys are device-supplied channel names,
// and SPEC 2.5's name grammar admits `toString` and `constructor`, which on a plain object
// would answer colorFor with an inherited function (a stroke value canvas silently ignores,
// so the lane draws in whatever colour the previous lane left) and made
// saveColor("__proto__", ...) a silent no-op. Values are type-checked on load because
// localStorage is hand-editable.
const COLOR_KEY = "mcuscope.colors";
function loadColors() {
  const store = Object.create(null);
  let parsed;
  try { parsed = JSON.parse(localStorage.getItem(COLOR_KEY) || "{}"); } catch { return store; }
  if (typeof parsed !== "object" || parsed === null) return store;
  for (const k of Object.keys(parsed)) {
    if (typeof parsed[k] === "string") store[k] = parsed[k];
  }
  return store;
}
const savedColors = loadColors();
export function saveColor(name, color) {
  savedColors[name] = color;
  try { localStorage.setItem(COLOR_KEY, JSON.stringify(savedColors)); } catch { /* private mode */ }
}
// Palette slots are handed out here, per name on first sight, rather than by each caller's own
// index: that index restarted in every chart and in the lanes, so a chart's first channel and
// the first digital lane were the same teal. Bounded against a device rotating names.
const paletteSlots = new Map();
const PALETTE_NAMES_MAX = 512;
let nextSlot = 0;
export function colorFor(name) {
  if (savedColors[name]) return savedColors[name];
  let slot = paletteSlots.get(name);
  if (slot === undefined) {
    if (paletteSlots.size >= PALETTE_NAMES_MAX) paletteSlots.clear();
    slot = nextSlot++ % PLOT_COLORS.length;
    paletteSlots.set(name, slot);
  }
  return PLOT_COLORS[slot];
}

// Normalise a colour string to a 6-digit hex for the <input type=color> picker (which
// rejects anything else); shared by the analog swatches and the digital lane swatches.
export function rgbToHex(c) { return c && c[0] === "#" ? c.slice(0, 7) : "#46c8d8"; }

// Open a native colour picker. The input must be IN the document: Chromium happily opens
// the dialog for a detached <input type=color>, but Firefox drives it from the element's
// layout frame, which a detached element does not have - so clicking a swatch there did
// nothing at all, with no picker and no error. Hidden rather than visible, and removed
// once the picker commits or the element loses focus.
export function openColorPicker(value, onInput, onChange) {
  const inp = document.createElement("input");
  inp.type = "color";
  inp.value = value;
  inp.style.cssText = "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
  // An opacity:0 input is still focusable, so without this a leaked one lands at the top
  // of the tab order.
  inp.tabIndex = -1;
  document.body.appendChild(inp);
  const drop = () => {
    window.removeEventListener("focus", drop);
    if (inp.parentNode) inp.remove();
  };
  inp.oninput = () => onInput(inp.value);
  inp.onchange = () => { onChange(inp.value); drop(); };
  // Firefox fires neither on cancel; blur is the reliable "picker went away" signal.
  inp.onblur = drop;
  // ...except that neither showPicker() nor .click() moves focus to the input, so a
  // cancelled picker (Esc) fired no change and no blur and left the element in the
  // document forever, one per cancel. Focus returning to the window says the dialog closed.
  window.addEventListener("focus", drop);
  if (inp.showPicker) { try { inp.showPicker(); return; } catch { /* fall through */ } }
  inp.click();
}

// Every selector built so far -> {onSelect, secs, chip}. A shift-click drives them all,
// which is what makes "set the window everywhere" one click instead of one per chart plus
// one for the lanes; it is kept here so neither panel has to know the other has a selector.
const windowGroups = new Map();

// The shared drag zoom's chip text, or null while there is none. Every selector shows it in
// place of a lit span, because while the zoom stands no span button is what the panel draws.
let zoomText = null;
// plots.js owns the zoom (onZoomControls): `leave` drops it and keeps the freeze, `exit` also
// resumes. Registered rather than imported, since digital.js reaches these through here.
let zoomLeave = () => {};
let zoomExit = () => {};
export function onZoomControls({ leave, exit }) { zoomLeave = leave; zoomExit = exit; }
export function leaveZoom() { zoomLeave(); }
export function exitZoom() { zoomExit(); }

// Shared window selector (5s/30s/5m) for both the analog chart heads and the digital head.
// `current` is the selected seconds; `onSelect(secs, event)` fires on click. Picking a span
// also leaves a drag zoom, since the span is then what the panel should draw; the freeze
// stays, which is the pause button's to lift.
export function buildWindowButtons(current, onSelect) {
  const win = document.createElement("div");
  win.className = "plot-win";
  win.setAttribute("role", "radiogroup");
  win.setAttribute("aria-label", "Time window");
  const group = { onSelect, secs: current, chip: null };
  for (const [secs, label] of PLOT_WINDOWS) {
    const b = document.createElement("button");
    b.setAttribute("role", "radio");
    b.textContent = label;
    b.dataset.secs = String(secs);
    b.title = `Show the last ${label}; shift-click to set every chart and the digital lanes`;
    b.addEventListener("click", (e) => {
      const hit = e && e.shiftKey ? [...windowGroups.values()] : [group];
      for (const g of hit) { g.secs = secs; g.onSelect(secs, e); }
      zoomLeave();
      paintWindowGroups();
    });
    win.appendChild(b);
  }
  const chip = document.createElement("button");
  chip.className = "zoom";
  chip.setAttribute("role", "radio");
  chip.hidden = true;
  chip.title = "Zoomed to the dragged range, with every chart and the lanes paused on it. "
    + "Click to return to the window and resume (so does a double-click on a chart)";
  chip.addEventListener("click", () => zoomExit());
  win.appendChild(chip);
  group.chip = chip;
  rovingRadios(win);
  windowGroups.set(win, group);
  paintWindowGroup(win, group);
  return win;
}

function paintWindowGroup(win, g) {
  g.chip.hidden = zoomText === null;
  if (zoomText !== null) g.chip.textContent = zoomText + " ×";
  // While a zoom stands the chip is the checked item and no span is.
  setRadios(win, (b) => (b === g.chip ? zoomText !== null
    : zoomText === null && Number(b.dataset.secs) === g.secs));
}

function paintWindowGroups() {
  for (const [win, g] of windowGroups) paintWindowGroup(win, g);
}

// Show the zoom chip on every selector (text), or take it away and relight each span (null).
export function showZoom(text) {
  zoomText = text;
  paintWindowGroups();
}

// Repaint every selector's "on" state. After a shift-click the groups that did NOT receive
// the click are showing the wrong span as selected, and a head lying about its own window is
// exactly the half-done state the shift-click exists to prevent.
export function syncWindowButtons(secs) {
  if (!PLOT_WINDOWS.some(([s]) => s === secs)) return;
  for (const g of windowGroups.values()) g.secs = secs;
  paintWindowGroups();
}

// Clear-all destroys a chart's DOM; without this its onSelect would keep taking shift-clicks
// and writing the window onto a chart object that is no longer drawn.
export function dropWindowButtons(win) { windowGroups.delete(win); }

// ---- segmented controls and dialogs: keyboard behaviour shared by every module ----------

// A role="radiogroup" of buttons: `on` class, aria-checked and a roving tabindex, so the group
// is one tab stop landing on the checked button (else the first usable one). Every place that
// changes a group's selection paints it through here, or the tab stop goes stale.
export function setRadios(group, isOn) {
  const all = [...group.querySelectorAll("button")];
  let stop = null;
  for (const b of all) {
    const on = !!isOn(b);
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = -1;
    if (on && stop === null && !b.disabled && !b.hidden) stop = b;
  }
  stop = stop || all.find((b) => !b.disabled && !b.hidden);
  if (stop) stop.tabIndex = 0;
}

// Arrow keys move to the neighbouring usable button and select it, wrapping at either end, as
// native radios do. Click first, then focus: a click handler may move focus (cmd/raw focuses
// the command input), and the keyboard user is still in the group.
const ARROW_STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
export function rovingRadios(group) {
  group.addEventListener("keydown", (e) => {
    const step = ARROW_STEP[e.key];
    if (!step) return;
    const items = [...group.querySelectorAll("button")].filter((b) => !b.disabled && !b.hidden);
    if (!items.length) return;
    e.preventDefault();
    const at = items.indexOf(e.target);
    const next = items[at < 0 ? 0 : (at + step + items.length) % items.length];
    next.click();
    next.focus();
  });
}

// Enter in a dialog presses its primary button: `pick(target)` names the button, or null for
// none. Never from a textarea (Enter is a newline there) or a button or link (Enter already
// activates that one, and Cancel must not submit).
export function enterSubmits(dlg, pick) {
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.defaultPrevented) return;
    const t = e.target;
    if (!t || t.tagName === "TEXTAREA" || t.tagName === "BUTTON" || t.tagName === "A") return;
    const btn = pick(t);
    if (!btn || btn.disabled) return;
    e.preventDefault();
    btn.click();
  });
}

// Alt-click (or Shift+Enter) on a channel/lane name: show only that one, or show them all
// again when it is already the only one shown. A 12-channel stream otherwise needs 11 clicks
// to look at one and 11 more to get back. Returns the new show map; nothing is mutated here,
// so the two callers (analog legend, lane gutter) apply it their own way.
export function soloShow(names, showMap, name) {
  const shown = names.filter((n) => showMap.get(n));
  const sole = shown.length === 1 && shown[0] === name;
  return new Map(names.map((n) => [n, sole || n === name]));
}
