# Batch D report: dialogs, Settings, accessibility, theme

HEAD: 9240081ab250c059a2fc8fbfb75192837299b657 (uncommitted working tree on top).

The tree also holds another agent's parallel edits (owner fixes: api.js, state.js, timewindow.js, plots.js, cmdbar port label, regex box CSS); they are not part of this batch.

Files: `webui/index.html`, `style.css`, `chrome.js`, `settings.js`, `statusbar.js`, `exportdlg.js`, `app.js`, `layout.js`, `terminal.js`, `cmdbar.js`, `mcuscope/server.py`.
Docs: `docs/SPEC.md` (3.3.1, 3.4, 9.1), `CHANGELOG.md`, `README.md`, `docs/IMPLEMENTATION_PLAN.md`.

## Per id

- **Item 5, D-F2 contrast**: done, changed for the dark theme.
  - Light: `--text-faint` #646f7b (5.12 panel, 4.72 bg, 4.55 panel-2), `--accent` #0b7a8d (5.02 on white, white on it 5.02).
  - Dark: the recommended #8b96a3 measures 6.10, louder than `--text-dim` #7f8b98 (5.28), so hints would outrank labels.
  - Built: dark faint #7b8692 (4.95 panel, 4.67 panel-2, 5.25 bg) and dark dim lifted to #949fac (6.82 panel, 6.44 panel-2).
  - `--ch-sys` follows faint in both themes, as it matched the old faint before (sys tag and sys text).
  - Found: the dark lit window button and zoom chip were #fff on #46c8d8 (2.00:1); now #04222a (8.30), white kept in light.
  - Checked: disabled controls dim by `opacity: .5`, independent of faint; placeholders stay well below input text; `.chk.off` keeps its opacity.
- **Item 12, D-F9 dirty Settings**: done. A section is dirty while its fields differ from what was last rendered, so editing back clears it.
  - Save buttons are plain until dirty, then primary `Save *`; Escape and the x confirm, naming the sections.
  - PlotJuggler (applies on change) and Sessions (no fields) are not tracked. Removing a port row marks Ports; an untouched `+ port` row does not.
- **D-F4**: done. The token confirmation is `#cfgTokenNote`, `.inline-note` in `--text-dim`.
- **D-F6**: done. Heading reads `Export terminal lines`, `Export plot data` or `Export CAN frames`.
- **D-F7**: done. `reset range`, id `expReset`.
- **D-F8**: done. One hint under each Storage field; the cap's hint is `0 = no cap; now 8.6 MB`, trimmed lines in its title.
  - Section hint shortened to `applies live`, with `applies on restart` under the database path; the duplicate `0 = no cap` placeholder dropped.
- **D-F11**: done, changed for Settings. `autofocus` on `#devSel`, `#cfgHost`, `#sesName`; Export focuses the range radio in force.
  - Enter submits every dialog through `chrome.js enterSubmits`; in Settings it saves the field's own section (the review skipped Settings).
  - Attach, Start and Export ignore a second press while in flight, so a held Enter cannot double-submit.
- **D-F13**: done. `no sessions yet: the session button in the status bar starts one`.
- **D-F14**: done. Hints rewritten short; the bind hint duplicated its label, so the by-id detail moved to the label's title.
- **D-F15**: done. `deriveAlias` mirrors `cli._derive_alias`; the prefill stops once an alias is typed and resumes if it is cleared.
- **D-F17**: done, changed. No `(keep)` option: `GET /config` already returns every port's `eol` (loader-normalised).
  - So the EOL select shows the saved value and the save sends it; PUT /config/ports already accepted it. No daemon change.
- **D-F18**: done. Refusals use the label words (`Bind host is required`, `Keep newest sessions must be 0-1000`, `Baud must be`).
- **D-F19**: done. `not checked yet in this daemon run`; the checkbox hint is one line, the env override in its title.
- **D-F20**: done, plus a latent fix. `refreshConfig` returned the stale config on failure, so an open after the daemon died rendered editable.
  - Offline: meta line says read-only, all daemon Saves and `+ port` disabled, token focused; only token edits count as unsaved.
- **D-F21**: done (`<details>`).
- **D-F22**: done. `#sessionDlg` with Name and Note (textarea), inline errors, default `run-YYYY-MM-DD_HH-MM` in local time.
  - Daemon fix: `POST /sessions` stored a whitespace-only name as an empty name (min_length ran before the strip); now a 422.
- **D-F23**: done. Dialogs `aria-labelledby`, field hints `aria-describedby`, port-row inputs `aria-label`, export option labels `for`.
- **D-F24**: done. Below 860 px, `order` plus a zero-height `::after` break keeps brand and the three action buttons on row 1; brand version hidden.
- **Light `dialog::backdrop`**: done, `rgba(20,30,40,.35)`.
- **Chip hover shadow**: done, `var(--shadow)`.
- **`.reopen` keyboard**: finding does not hold: it is a `<button>` shown while collapsed, so it is in the tab order.
  - Added instead: hide moves focus to the reopen tab and reopen moves it back, since the focused button disappears.
- **`#resizer` keyboard**: done. `role="separator"`, `tabindex="0"`, `aria-valuenow`; Left/Right 20 px, Shift 100, clamped and saved via `layout.sideW`.
- **Per-browser note**: done, one line in the Settings meta row.
- **T-F11**: done. `setRadios` and `rovingRadios` in chrome.js replace the three aria-checked fan-outs and the window-group paint.
  - Window selectors are now radiogroups; the zoom chip is the checked radio while a zoom stands. Arrows wrap and skip disabled or hidden buttons.
- **Beyond the list**: `color-scheme` per theme (native checkboxes and spinners follow it); `.cfg-row .field` basis 150 px so Storage's four fields fit one row.
  - Settings section-level hints had no rule at all (13 px body text); they now use the 11 px faint hint style.
- **SPEC**: 9.1 (contrast rule, divider keys, alias prefill, dialog a11y and Enter, radiogroups, export heading, `reset range`, session dialog, Settings rows, dirty, read-only, per-browser line).
  - 3.3.1 (the dialog sends `eol`), 3.4 (blank session name is a 422). "record button" also fixed in README and IMPLEMENTATION_PLAN.
- **CHANGELOG**: 8 Changed, 3 Added, 3 Fixed under Unreleased.

Totals: 25 done (4 changed from the recommendation: D-F2 dark values, D-F11 Settings Enter, D-F17 no keep option, `.reopen` narrowed), 0 skipped outright.

## Contradictions with the brief

- "a duplicate name the API refuses": `POST /sessions` accepts duplicates (SPEC 3.4: a name resolves to the newest match). No refusal path exists, so none is tested.
- D-F2's dark value contradicts its own claim of staying quieter than `--text-dim` (6.10 against 5.28).

## Tests added

All 32 mutations below were caught (two only after strengthening the test, noted).

- `webui_js/settings_dirty.test.mjs`: clean on open; edit then edit back; a Storage save leaves Server dirty; a failed save stays dirty.
  - Also: blank new port row not dirty, alias typed then cleared, row removal dirty; x declined stays open with exact section names.
  - Also: Escape prevents the native close and asks; reopen re-renders clean; Enter saves only the field's section, not from PlotJuggler or a button; token note.
- `webui_js/settings_offline.test.mjs`: read-only copy, disabled controls, token focus; daemon edits not unsaved, token edit asks; daemon back is editable.
  - Also: daemon down after a good load stays read-only and an edit there does not ask (strengthened after the readOnly-guard mutant survived).
- `webui_js/settings_ports_eol.test.mjs`: EOL seeds from saved values; every row sends its eol, a new row lf; cap hint and trimmed title.
  - Also: update status copy, sessions empty copy, and each refusal's exact label wording with nothing saved.
- `webui_js/statusbar_session_dialog.test.mjs`: no prompt, local default name; empty and whitespace refused unsent; Enter in note or on a button sends nothing.
  - Also: Enter in name posts trimmed name and note, closes, clears the strip; 422 and unreachable stay inline with typing kept; held Enter posts once.
  - Also: Escape and Cancel start nothing; a running session stops with no dialog; `deriveAlias` against 13 values printed by `cli._derive_alias`.
  - Also: alias follows the device until typed, resumes when cleared, and a new open forgets a typed alias (strengthened after that mutant survived).
- `webui_js/chrome_radios.test.mjs`: one tab stop; programmatic selection moves it; wrap both ends; click before focus; disabled and hidden skipped.
  - Also: non-arrow keys untouched; window selector roles and the zoom chip as the stop; Enter ignored from textarea, button, link, composition, disabled or no primary.
- `webui_js/app_resizer_keys.test.mjs`: keys from the expanded width, Shift step, aria-valuenow; stops at 1274 and 260 px; other keys do nothing; focus handoff; three groups wired.
- `webui_js/theme_a11y_static.test.mjs`: computed contrast of faint on bg, panel and panel-2 and faint below dim in both themes; light accent; lit window button colours.
  - Also: light scrim and chip shadow rules; every dialog's labelledby heading and every describedby id exist; autofocus; no `window.prompt`; plain Saves, note, focusable divider.
- `webui_js/exportdlg.test.mjs` (appended): heading per panel; option label `for`; focus on the remembered range; Enter exports once with a second press ignored.
- `tests/test_sessions.py::test_a_blank_session_name_is_refused_not_stored_empty` (`"   "`, `"\t\n"`, `"\x1f"`): 422 naming `name`, nothing stored, padded name still trimmed.
- Updated for intended changes: `settings_ports_baud` (label wording), `exportdlg.test` (`expReset`), `smoke.test` (new exports), `dom_stub` (`select()`), `test_webui.py` (new ids).

## Gates

- `uv run python -m ruff check .`: all checks passed.
- `uv run python -m pytest tests/test_webui_js.py tests/test_webui.py tests/test_cli_contract.py tests/test_sessions.py tests/test_config_api.py -q`: 105 passed.
- `node --test` over `host/tests/webui_js`: 576 pass, 0 fail.
  - An earlier run had 1 failure in `api_db_reset_misfire` (the parallel agent's in-progress tick estimate); it passed on the next run and touches none of this batch.
- `tests/test_cli.py tests/test_assert.py tests/test_session_bundle.py tests/test_review_r2_server.py -k session`: 33 passed. Full suite not run.

## Renders

Headless Firefox (`ff-shot-light` profile; `ff-shot` was in use by the other agent) on static copies of index.html; the copies are deleted.

- `batch-d-settings-dark.png`, `batch-d-settings-light.png`: Server dirty (`Save *`), Storage hints one per field in one row.
- `batch-d-settings-dark-bottom.png`: PlotJuggler disclosure, token section, ports table with the EOL column.
- `batch-d-header-700.png`: brand and actions on row 1, daemon chip, port chips and restart badge below.
- `batch-d-session-light.png`: session dialog over the light scrim.

## Owner decisions needed

- [ ] Dark `--text-dim` is brighter (#949fac); it is on every label, iconbtn and chip meta in the dark theme.
- [ ] Save buttons are plain until a section has edits, including PlotJuggler's `Save as default`, which is never lit.
- [ ] Enter in a Settings field saves that section.
- [ ] The default session name is now local time, `run-2026-09-14_10-32` (was UTC, `run-2026-09-14T10:32`).
- [ ] Duplicate session names are accepted by the API; should the dialog warn when a name exists?
- [ ] The simulator entry in Attach prefills `board` (the CLI rule for a URL), not `sim`.
- [ ] Below 860 px the brand version is hidden; `color-scheme` now follows the theme.
- [ ] The ports table has 8 columns in 720 px, so the device select is narrow (about 90 px).

## Manual checks owed

- [ ] Contrast: hints, empty states, CAN headers and Settings headings read as quieter than labels but legible, dark and light.
- [ ] Dark theme: labels and chip metadata with the brighter `--text-dim` still sit below body text.
- [ ] Settings: edit Bind host, see `Save *`; type it back, it clears; Escape asks with the section name; Cancel keeps the edit.
- [ ] Settings: remove a port row, see Ports marked; change a port's EOL, save, reopen, value kept.
- [ ] Settings with the daemon stopped: read-only line, Saves disabled, token field focused and saveable.
- [ ] Settings: Enter in Retention saves Storage only; the PlotJuggler disclosure opens with keyboard and mouse.
- [ ] Attach: device select focused on open; alias follows the device; Enter attaches; a held Enter attaches once.
- [ ] Session: button opens the dialog with the name selected; Enter starts; Enter in Note adds a newline; the note shows as the session row's hover.
- [ ] Export: heading per panel; focus on the range radio; `reset range`; Enter exports.
- [ ] Tab through the page: one stop per segmented control and window selector; arrows move and select; the zoom chip while zoomed.
- [ ] cmd/raw with arrows: focus stays on the group, not the command input.
- [ ] Divider: Tab to it, Left/Right resize with the accent highlight, reload keeps the width.
- [ ] Hide the sidebar with Enter: focus lands on the reopen tab; Enter reopens and focus returns.
- [ ] Window at 700 px with two ports and badges: header rows as in `batch-d-header-700.png`.
- [ ] Light theme: dialog scrim, port hover card shadow, light accent on Attach and the brand.
- [ ] Screen reader spot check: dialogs announce their titles; attach fields read their hints.
