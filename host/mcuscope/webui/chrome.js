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
// One store, keyed by channel/lane name and shared by both panels: names are globally
// unique (SPEC 2.5). Effective colour = saved override, else the palette slot for that index.
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
export function colorFor(name, i) { return savedColors[name] || PLOT_COLORS[i % PLOT_COLORS.length]; }

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

// Shared window selector (5s/30s/5m) for both the analog chart heads and the digital head.
// `current` is the selected seconds; `onSelect(secs)` fires on click and the group repaints its
// own "on" state, so the two heads no longer carry duplicate copies of this loop.
export function buildWindowButtons(current, onSelect) {
  const win = document.createElement("div");
  win.className = "plot-win";
  for (const [secs, label] of PLOT_WINDOWS) {
    const b = document.createElement("button");
    b.textContent = label;
    if (secs === current) b.classList.add("on");
    b.addEventListener("click", () => {
      win.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      onSelect(secs);
    });
    win.appendChild(b);
  }
  return win;
}
