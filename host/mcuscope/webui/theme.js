import { $, root } from "./state.js";

// ---- theme -------------------------------------------------------------------------
function updateThemeGlyph() {
  const dark = root.getAttribute("data-theme") === "dark";
  const btn = $("themeBtn");
  btn.textContent = dark ? "☾" : "☀";   // moon in dark mode, sun in light mode
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}

export function initTheme() {
  // Honour an explicit saved choice; otherwise follow the OS preference (dark on a dark-mode
  // OS, light on a light-mode OS). data-theme is always set, so the [data-theme] var blocks
  // drive the palette and the @media (prefers-color-scheme) blocks are unnecessary.
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch { /* private mode */ }
  const sys = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  root.setAttribute("data-theme", saved === "light" || saved === "dark" ? saved : sys);
  updateThemeGlyph();
  $("themeBtn").addEventListener("click", () => {
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch { /* private mode */ }
    updateThemeGlyph();
  });
}
