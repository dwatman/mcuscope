# Fix report: webui batch

Tests: `node --test` on `app_sidebar_resize.test.mjs`, `plot_grammar.test.mjs`, `app_layout`, `app_resizer_keys`, `app_layout_corrupt`: all pass. `ruff check .` clean.

## Per finding

- MODULES-3: `app.js` (after the first `applySideWidth()`): `window.addEventListener("resize", applySideWidth)`.
  - Pinned by `app_sidebar_resize.test.mjs` ("a window resize re-clamps...", plus a `sideWidthFor` test).
  - Revert (listener removed): 4 fail.
  - DOM half: headless Chromium (Playwright 1.62.0) against the static webui served on 18881 (the `--sim` daemon would not start, see Doubts): stored sideW 1200, 1600 px viewport gives `--side-w` 1200px; narrowed to 800 px gives 474px and `scrollWidth` == `innerWidth` (no sideways overflow). Not run against the pre-fix code.
- MODULES-4: `app.js` `endResizerDrag` / `endDividerDrag` registered on `pointerup`, `pointercancel`, `lostpointercapture` for both dividers; `style.css` `touch-action: none` on `.resizer` and `.hdivider`.
  - Pinned by 6 tests in `app_sidebar_resize.test.mjs` (each ending, each divider, plus a move after the end must not drag).
  - Revert (list cut to `pointerup`): 2 fail per divider. `touch-action` is CSS, not pinned by a test.
- OP-11.2 JS half: `plots.js` `decodePlotSample` divides by k = 1/scale when k is an integer with 1 <= |k| < 2^53, else multiplies.
  - Pinned by the fixture's `values` cases, now asserted in `plot_grammar.test.mjs` (plus a guard that the fixture has `values`).
  - Revert (always multiply): 1 fail; bound removed: 1 fail. Zero-scale guard was redundant in JS (1/0 is Infinity) and is not there. The `|k| >= 1` check is mirror-only: no case distinguishes it (scale Infinity cannot parse).

## Existing tests edited

`plot_grammar.test.mjs`: added the `values` assertion (shared test, new assertion only).

## SPEC edits

None.

## Changelog

- The sidebar re-clamps when the window is narrowed.
- A touch-cancelled or capture-lost drag of a sidebar divider now ends instead of sticking.
- Plot scales like `*0.1` show exact decimals in the browser too.

## Not done

None.

## Doubts

- `mcuscoped --sim` failed to start during the run (`'Store' object has no attribute 'add_tick_check'`, another batch mid-edit), so the browser check used a static server, not the daemon; the UI's resize path does not depend on the API.
- `touch-action: none` untested on a real touch device.
