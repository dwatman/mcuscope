# Web UI UX review: dialogs, onboarding, copy, accessibility

Read-only review at HEAD 2149806, against `host/mcuscope/webui/`, `docs/SPEC.md` sections 9.1 and 3.3.1, and `docs/img/webui.png`.
Lens: usability, intuitiveness, understandability, usefulness.
Nothing here re-raises an item already closed by the 2026-09-07 or 2026-09-12 rounds, or already queued on `docs/review/2026-09-12-adversarial/manual-verify.md`.

24 findings, ranked by value to the user.
Effort tags: S = under an hour, M = half a day, L = more.

## Contradictions between SPEC, code, and shipped copy

Flagged rather than worked around, per CLAUDE.md.

- **F1 below is a copy/daemon contradiction**: the status bar tells the user to set `server.token` in `config.toml`, and `config.py:341` explicitly ignores that key and warns about it.
- **SPEC 9.1, port chip**: the "Status / setup bar" bullet says a chip shows "alias, device, baud, connected state".
  - The detailed chip paragraph three bullets later puts baud in the hover only, which is what the code does.
  - The first sentence is stale; delete "baud" from it.
- **SPEC 9.1, Settings ports rows**: the list "alias, device, serial number, baud and auto-attach" omits `identify`.
  - The code renders it as a seventh column (`index.html:281`) and sends it on every save, and SPEC 3.3.1 lists it in the PUT body.
  - Add it to the 9.1 sentence.
- **SPEC 9.1, "Errors from the API shown inline"**: detach, disconnect, reconnect and session errors are not inline.
  - They are a 2.5 s red flash on the daemon chip with the reason hidden in a `title` (`statusbar.js:45`). See F5.

## Findings

### F1. The status bar tells the user to configure the token in a place the daemon ignores (S)

`index.html:16`, tooltip on the daemon chip: "bind 0.0.0.0 to reach it across the LAN; set server.token in config.toml for that, and this page will ask for it".

**Today.** A user who follows this edits `config.toml`, restarts, and gets `config: server.token in the config file is ignored` in the daemon log (`config.py:341`), an unauthenticated daemon on 0.0.0.0, and a page that never asks for a token.

**Why it hurts.** It is the only in-product instruction for the one security-relevant setup step, and it is wrong. It also disagrees with the Settings copy 250 lines later (`index.html:268`), which names `--token / MCUSCOPED_TOKEN` correctly.

**Change.** Replace the tooltip text with: `Daemon address. To reach this page across the LAN, start mcuscoped with --host 0.0.0.0 and set MCUSCOPED_TOKEN; this page will then ask for the token.`
Same wording in `statusbar.js:43` (`DAEMON_TITLE`), which must stay byte-identical or the flash restores the wrong string.

### F2. Hint text and empty states fail contrast in both themes (S)

`style.css:5` and `:28` (`--text-faint`), used by `.hint` at 10.5 px (`style.css:334`), `.empty-state` (`:42`), and every Settings section heading (`.cfg-sec h4`, `:346`).

**Today.** Measured contrast of `--text-faint` against its own background: 2.98:1 light, 2.82:1 dark. WCAG AA wants 4.5:1 for text this size.

**Why it hurts.** It is exactly the copy a newcomer needs: every explanatory hint, every empty state, and the labels that name the Settings sections. On a bench under room light they read as decoration, not instruction. The instrument palette is otherwise strong (body text is 12.5:1 dark), so this is one variable away from compliant.

**Change.** `--text-faint: #646f7b` in the light block and `#8b96a3` in the dark block. Measured 5.12:1 and 6.10:1 against `--panel`, still visibly quieter than `--text-dim`. Also `--accent: #0b7a8d` in the light block only: the current `#0e8ba0` is 4.02:1 on white and carries the update-badge link text and `.iconbtn.on`.

### F3. A fresh install shows a blank terminal with nothing to do next (M)

`index.html:59` (`#terminalArea`), `index.html:24` (`#ports`).

**Today.** First run against a fresh daemon with no ports: the terminal column is empty black, the ports area is empty, the CAN and Plots panels each carry a helpful empty state, the terminal (the largest thing on the page) carries none. The only cue is a `+ Attach` button in the top right.

**Why it hurts.** Two of four panels teach and two do not, and the two that teach are the ones a newcomer has least use for. The user cannot tell whether the page is broken, still loading, or waiting for them.

**Change.** Two `.empty-state` blocks, matching the CAN and Plots idiom.

- In a pane with no rows: `No lines yet. Attach a serial port with + Attach above, or restart the daemon as "mcuscoped --sim" for the zero-hardware demo.`
- In `#ports` when the port list is empty: `no ports attached`.

The pane one belongs in `terminal.js`'s render, guarded like `plots.js:1096` so it is added once and removed on the first row.

### F4. Successful saves are printed in the error colour (S)

`settings.js:68` writes "saved; reconnecting stream" into `#cfgTokenErr`, and `style.css:336` sets `.inline-err { color: var(--crit) }`.

**Today.** Save a token and the confirmation appears in red, in the slot every refusal in that dialog uses.

**Why it hurts.** The user cannot tell a success from a failure by looking, in the one section where a failure is likely (a wrong token).

**Change.** Add `.inline-note { color: var(--text-dim); font-size: 11.5px; min-height: 1em; }` and toggle the class in `applyToken`: `err.className = value ? "inline-note" : "inline-note"` on success, back to `inline-err` on the error paths. Or simplest: `err.classList.toggle("ok", true)` plus `.inline-err.ok { color: var(--good) }`.

### F5. A failed detach, disconnect, reconnect or session action leaves no readable message (M)

`statusbar.js:45` `flashDaemonError`, called from `detachPort`, `holdPort`, `reconnectPort`, `toggleSession`, and via `hooks.reportError` from the session export and bundle buttons.

**Today.** The daemon chip border turns red for 2500 ms and the reason is written into the chip's `title`. Reading it means noticing the flash and getting the pointer onto the chip inside 2.5 s. On a touch screen or by keyboard it is unreadable. After 2.5 s there is no trace the action failed at all.

**Why it hurts.** These are the destructive and state-changing actions. A detach that silently did nothing reads as a UI that ignored the click. SPEC 9.1 says "Errors from the API shown inline".

**Change.** Keep the flash as the attention cue, and add a persistent one-line message strip under the status bar carrying the last failure with a dismiss `x`, cleared on the next successful poll. Reuse `.inline-err` styling and `role="status"`. Cheaper interim (S): raise the timeout to 8 s and put the message as visible text inside the chip rather than in `title`.

### F6. The Export dialog never says what it is exporting (S)

`index.html:294`, heading is the literal word "Export"; `exportdlg.js:154` already receives `ctx.kind`.

**Today.** The same dialog opens from a terminal pane, a chart, the digital panel, the CAN table and a session row. Its title, range controls and buttons are identical in all five cases; only the small options block below differs.

**Why it hurts.** With three panes open and a chart, a user who clicks the wrong `export` gets no chance to notice before the download lands. The dialog also decides the filename from `ctx.kind`, so the information is already in hand.

**Change.** In `openExportDialog`, set the heading to `Export ${ctx.kind}` (giving "Export terminal pane", "Export CAN frames", "Export plot"). One line, and it makes the shared dialog feel deliberate rather than generic.

### F7. "whole session" is a button that does not export a whole session (S)

`index.html:311`, title "Forget the remembered range and export the whole session"; `exportdlg.js:210` calls `reset()`.

**Today.** The control sits in the footer of the Range block, styled like the panel `export` buttons, and reads as "export the whole session now". It actually resets the remembered range to the default, which sends no session bound, which the daemon reads as the currently open session, not the whole capture.

**Why it hurts.** The label promises an action it does not perform, and describes a scope (whole session) that is a third thing again.

**Change.** Rename the button `reset range`, title `Forget the remembered range and go back to the open session`. S, and it removes the only misleading label in the dialog set.

### F8. The Storage section is four stacked paragraphs of prose detached from their fields (M)

`index.html:220-223`, four `.hint` spans after the checkbox, describing the session floor, the auto-session interaction, and the size cap, in 90 to 130 characters each.

**Today.** The fields are in a `.cfg-row` at the top; the explanations for three of them are four lines of grey prose below the checkbox, in an order that does not match the field order. Two of the four explain each other ("the newest N sessions never expire", then "without automatic sessions the floor above only protects runs you named by hand, which is nothing at all in normal use").

**Why it hurts.** It is the densest text in the product, at the smallest size and lowest contrast, and it reads as a design rationale rather than as instructions. A user scanning for "what does Size cap do" has to parse all four.

**Change.** One hint per field, under that field, one line each, and drop the meta-commentary.

- Under Size cap: `0 = no cap. Above it, the oldest lines are trimmed.`
- Under Keep newest sessions: `These never expire by age. 0 = age only.`
- Move the live `#cfgDbNow` readout beside the Size cap field, so the number and the cap sit together.
  - That is what SPEC 9.1 asks for: "shows the current capture size next to the cap".
- Fold the auto-session caveat into the checkbox label's own hint: `Off, only runs you name by hand are protected by the floor above.`

### F9. Settings loses typing with no warning, and each section saves separately (M)

`settings.js:529` `closeSettings`, and `initSettings`'s five independent Save handlers.

**Today.** Five sections, five Save buttons, no visual difference between a section with unsaved edits and one without. Escape and the `x` close the dialog and discard everything unsaved, silently. Removing a row from the Ports table (`settings.js:370`) deletes the row immediately but changes nothing until Save, which nothing says.

**Why it hurts.** The multi-Save model is defensible (each section maps to one endpoint), but with no dirty state it is a trap: edit Bind host, then edit Retention, press the Storage Save, close, and the host edit is gone with no sign it ever existed.

**Change.** Mark a dirty section: on any `input`/`change` inside a `.cfg-sec`, add a class that makes that section's Save primary-coloured with the label `Save *`, cleared on a successful save and on re-render. Then in `closeSettings`, if any section is dirty, `window.confirm("Close Settings? Unsaved changes in <n> section(s) will be lost.")`. Both are small and the first alone removes most of the risk.

### F10. The port chip dot is an 8 px click target that toggles the connection (S)

`style.css:71` (`.dot { width: 8px; height: 8px }`), `statusbar.js:280` makes it a `<button>`.

**Today.** The primary connect/disconnect switch for a port is an 8 by 8 pixel circle, sitting 8 px from the alias text. Its only affordance is a hover `scale(1.4)`.

**Why it hurts.** Well under the 24 px minimum target size, and the consequence of a misclick is dropping a live capture link. On a laptop trackpad it is a deliberate aim.

**Change.** Keep the visual size, grow the hit area: `.chip button.dot { padding: 8px; margin: -8px; background-clip: content-box; }` or a `::after { position:absolute; inset:-8px }` overlay, the trick `.resizer::after` (`style.css:127`) already uses in this file.

### F11. No dialog takes focus on open and none submits on Enter (S)

`statusbar.js:485`, `settings.js:517`, `exportdlg.js:166`; no `autofocus` attribute and no keydown handler anywhere in `index.html` or the dialog modules.

**Today.** `showModal()` focuses the first focusable element, which in all three dialogs is the close `x` in the header. Typing an alias means tabbing past it. Pressing Enter in the Alias field does nothing; the user must reach for the mouse to press Attach.

**Why it hurts.** The attach dialog is a four-field form that a bench user opens repeatedly. Enter-to-submit is the single most expected behaviour of a form and it is absent. Keyboard-first users land on Close, which reads as "the dialog wants to be dismissed".

**Change.** Add `autofocus` to `#devSel`, `#cfgHost` and `#expSession`. Add one keydown listener per dialog: Enter outside a `<textarea>` triggers that dialog's primary button (`#dlgAttach`, `#expGo`). Skip Settings, which has no single primary action.

### F12. The sidebar width and collapsed state are lost on every reload (S)

`app.js:58`, `:78`, `:85`, `:99`: `--side-w` and `--can-h` are written as inline styles, and `ws.classList.add("collapsed")` is not persisted.

**Today.** Everything else in the UI remembers itself: theme, plot colours, pane layout, command history, export range, dismissed update version, CAN collapsed groups, per-port send mode. The sidebar geometry does not, so a user who works with a wide chart panel re-drags it after every reload.

**Why it hurts.** It is the most physical adjustment in the page and the only one that does not stick, which reads as an inconsistency rather than a decision.

**Change.** Three `localStorage` keys, validated on read like `chrome.js loadColors` does, applied in `app.js` before the first `resizePlots`. Clamp the width with the existing `clampW`.

### F13. The Sessions empty row names a control that does not exist (S)

`settings.js:281`: "no sessions recorded yet (use the record button in the status bar)".

**Today.** There is no control labelled "record". The status bar button reads `● session` (`index.html:35`).

**Why it hurts.** The one place the product explains how to start a session points at a name the user cannot find. SPEC 9.1 also calls it "a record button", so the vocabulary drift starts there.

**Change.** `no sessions recorded yet - press the "session" button in the status bar to name one`. Optionally settle the vocabulary in SPEC 9.1 too: the button, the CLI (`mcu session start`) and the dialog should all say "session".

### F14. Attach dialog hints explain the API, not the user's goal (S)

`index.html:141`, `:145`, `:174`, `:178`.

**Today.**

- Device: `populated from GET /devices`
- Bind: `attaches by the stable /dev/serial/by-id path instead of the port name, so another device on this port is not picked up`
- Alias: `name clients use: mcu -p <alias> ...`
- Save to config: `also add or update this port in the saved config file (Settings > Ports)`

**Why it hurts.** The first tells the user an endpoint name and nothing actionable. The second and third are correct but lead with the mechanism. Only the fourth reads as help.

**Change.**

- Device: `Devices the daemon can see right now. Not listed? Choose "custom..." and type the path.`
- Bind: `Follow this board if it enumerates on a different port after a replug.` (keep the by-id detail in a `title`, not in the visible hint)
- Alias: `How you will refer to this port here and in the CLI (mcu -p <alias>).`
- Save to config: keep as is.

### F15. The Attach dialog does not prefill the alias, though the CLI does (S)

`statusbar.js:478` clears `#aliasInput`; `cli.py:344` defaults `--alias` to "the device's basename, or 'board' for a URL".

**Today.** The dialog's Alias field is empty with placeholder "board", is required, and refuses with "alias is required" only after Attach is pressed. The CLI attaching the same device needs no alias at all.

**Why it hurts.** The same concept has a sensible default in one client and a mandatory blank field in the other, and the placeholder "board" reads as a default that is not applied.

**Change.** In `syncDevCustom`, prefill `#aliasInput` from the selected device's basename (or `board` for a URL) whenever the field is untouched, matching the CLI's rule. Mark the label `Alias` as required if it stays mandatory.

### F16. The "restart daemon to apply" badge gives no way to do it (S)

`index.html:34`, title "A saved config change needs a daemon restart to take effect".

**Today.** A persistent warning badge, correct and unactionable. The daemon deliberately does not restart itself (SPEC 3.3.1), and nothing on the page says how the user should.

**Why it hurts.** A standing warning with no next step trains the user to ignore it, which is the opposite of what a restart-required badge is for.

**Change.** Extend the title: `A saved setting differs from the running daemon. Stop mcuscoped (Ctrl-C in its terminal) and start it again to apply.` Naming the exact affected keys would be better but needs a daemon change; the tooltip is the cheap win.

### F17. Settings offers no way to change a saved port's line ending (S/M)

`index.html:281` ports table columns; `settings.js:401` `collectPorts` deliberately omits `eol`.

**Today.** The Attach dialog has a Line ending select; the Settings ports table does not. Omitting `eol` from the PUT is correct per SPEC 3.3.1 (it preserves a hand-written value), but the consequence is that a saved port attached with the wrong `eol`, or created by hand, can only be corrected by editing `config.toml`.

**Why it hurts.** The two dialogs disagree about whether line ending is a property of a port. SPEC 9.1 promises "a fresh install is fully configurable from the browser".

**Change.** Add an `Eol` column with options `(keep)`, `none`, `lf`, `crlf`, defaulting to `(keep)`, and send `eol` only when the user picks a concrete value. That preserves the omit-to-keep contract exactly and closes the gap. Alternatively decide the gap is intended and say so in SPEC 9.1.

### F18. Two error messages use a different word from the field they refuse (S)

`settings.js:461`: `sessions to keep must be 0-1000`, for the field labelled **Keep newest sessions**.
`settings.js:456`: `size cap must be 0-<n> MB`, for the field labelled **Size cap (MB)** (this one is fine).

**Today.** A user scanning the section for "sessions to keep" finds "Keep newest sessions".

**Change.** `"Keep newest sessions" must be 0-1000`. Same treatment for `retention must be 1-3650 days` -> `Retention must be 1-3650 days`, and quote the label in the ports refusal too.

### F19. The update-check status line is a dead end that names an environment variable (S)

`settings.js:187`: `no result yet in this daemon run (or MCUSCOPE_UPDATE_CHECK=0)`.

**Today.** With the checkbox on and no result, the user is told a check may or may not have run, and given an env-var name they probably did not set.

**Why it hurts.** It reports the daemon's internal ambiguity to the user instead of resolving it for them, and offers no next step.

**Change.** `not checked yet in this daemon run; the first check runs shortly after startup` and move the env-var caveat into the section hint, where it already lives (`index.html:234`). Also shorten that hint, which is currently one 190-character sentence: split it into `One request a day at most, cached across restarts; only the version number is fetched.` and `MCUSCOPE_UPDATE_CHECK=0 or =1 in the daemon's environment overrides this.`

### F20. Settings opens against an unreachable daemon with live Save buttons (S)

`settings.js:519-524`.

**Today.** With the daemon down, the dialog opens showing `could not load config (daemon unreachable)`, every field blank or stale, and all five Save buttons enabled. Pressing one writes an inline error into that section.

**Why it hurts.** A blank Bind host field beside an enabled Save invites the user to save an empty config over their real one. (`saveServer` refuses an empty host, so no damage lands, but the affordance is wrong.)

**Change.** In that branch, disable the five Save buttons and set the meta line to `Could not reach the daemon. Settings are read-only until it answers; the Access token field below still works.` The token exception is already the right call in the code (`settings.js:522`), it just is not said.

### F21. The PlotJuggler hint is a manual for another application (S)

`index.html:249`, 200 characters describing how to configure PlotJuggler's UDP Server source.

**Today.** The longest single hint in the dialog, at 10.5 px and 2.98:1 contrast, explaining a third-party tool's dialog.

**Why it hurts.** It is genuinely useful once, and noise on every subsequent visit. It also makes the section look four times as complicated as its two controls.

**Change.** Keep the content, hide it behind a `<details><summary>How to receive this in PlotJuggler</summary>` inside the section. Zero JS, native disclosure, and the section collapses to its two controls.

### F22. The session name prompt breaks out of the product's own dialog system (M)

`statusbar.js:167`: `window.prompt("Session name", "run-" + iso)`.

**Today.** Every other interaction in this UI happens in a styled `<dialog>`; naming a session raises a browser-chrome prompt, unthemed, unlabelled beyond four words, with no note field. The CLI's `mcu session start` takes `--note` (`cli.py:1260`); the UI always posts `note: ""`.

**Why it hurts.** It is the moment the dialog system stops feeling like one product. It is also the only place a user can annotate a run, and the annotation is unavailable.

**Change.** A fourth `<dialog>` with Name and Note fields, matching the attach dialog's markup exactly (which is how `settings.js` was already built, per its own header comment). M because it is new markup; the handler is a rename of `toggleSession`. Lower priority than F1 to F13 but it is the largest consistency gap in the set.

Note: the delete-session confirmation (`settings.js:255`) is also a `window.confirm`, but its copy is exemplary (names the run and the line count, states irreversibility). If only one gets a real dialog, make it this one, and keep the copy verbatim.

### F23. Dialogs are not named to assistive technology, and hints are not attached to their fields (M)

`index.html:134`, `:189`, `:293`: no `aria-labelledby` on any `<dialog>`; `.hint` spans are siblings, never referenced by `aria-describedby`.

**Today.** A screen reader announces "dialog" with no name, then reads labels without their explanatory hints, then reads the hints as loose text after the form.

**Change.** Give each `<h3>` an id and point the dialog at it with `aria-labelledby`. Give each `.hint` an id and add `aria-describedby` on the input it describes. Mechanical, about 20 attributes.

The related export-dialog issue (dynamic `expOpt_*` inputs have no `<label for>`) is already item 9 on the 2026-09-12 manual-verify list; not re-raised here, but the same `aria-describedby` pass should fix it.

### F24. The status bar becomes most of the viewport below 860 px (M)

`style.css:45` (`.statusbar { flex-wrap: wrap }`), `:365` (the 860 px block, which touches the workspace and command row but not the header).

**Today.** With two ports attached, the update badge showing and the restart badge showing, the header at 700 px wraps to four or five rows: brand, daemon chip, port chips, badges, then the action buttons. The `+ Attach`, settings and theme buttons land at the bottom, furthest from where the eye starts.

**Why it hurts.** The 860 px block already accepts that this is a supported width; the header is the one region it does not adapt. On a tablet at the bench the terminal gets what is left.

**Change.** Inside the existing `@media (max-width: 860px)` block: put the brand, spacer and the three action buttons on a fixed first row (`flex-basis: 100%` on a wrapper, or `order:` values), and let the daemon chip, port chips and badges wrap below them. Hide `.brand .ver` and the `#daemonUptime` readout at that width. No new breakpoint.

## Persistence: what survives a reload, and whether the user can tell

Compiled from the code so the gap is visible in one place; the finding is F12 plus the absence of any in-product statement.

Survives (all `localStorage`, all silent): theme (`theme.js:294`), plot and lane colours (`chrome.js:326`), export range (`exportrange.js:224`), dismissed update version (`statusbar.js:86`), access token (`state.js`), pane layout, command history, per-port send mode and eol, CAN collapsed groups.

Does not survive: sidebar width, the CAN/Plots divider position, sidebar collapsed state, the expand toggle (all F12); pause state and chart zoom (deliberate, and reasoned in `improve-webui.md` (c)); per-pane regex and channel filters if they are not in the pane layout record.

The user is told about exactly one of these: the token ("stored only in this browser", `index.html:268`). Nothing says the theme or the colours are per browser, which matters the first time someone opens the UI from a second machine and finds their palette gone. A single line under the Settings meta row would cover it: `Theme, colours, pane layout and the export range are stored in this browser, not in the daemon.` S, and it turns an invisible model into a stated one.

## Light theme versus dark

The dark theme is the designed one and it shows: the screenshot reads as a coherent instrument. The light theme is a variable swap and mostly holds up, with three specific weaknesses.

- Contrast: F2 covers `--text-faint` and the light `--accent`.
- `dialog::backdrop` is `rgba(4,8,12,.55)` with a blur (`style.css:327`), tuned for dark.
  - Over a `#f3f6f8` page it is a near-black scrim, so the light theme goes dark the moment any dialog opens.
  - Suggest `rgba(20,30,40,.35)` under `:root[data-theme="light"]`. S.
- `.chip[data-tip]::after` hover cards carry `box-shadow: 0 4px 14px rgba(0,0,0,.35)` unconditionally (`style.css:94`).
  - It is the one shadow in the file that does not go through `--shadow`. Route it through the variable. S.

`button.btn.primary` and `.seg button.on` both already carry a light-theme text-colour override (`style.css:106`, `:121`), which is the right instinct and shows the theme was checked; these three are what it missed.

## Reduced motion and keyboard basics

`@media (prefers-reduced-motion: reduce)` kills all transitions (`style.css:374`), and there are no CSS animations, so that base is covered.
`:focus-visible` outlines are defined for buttons, inputs, selects, `[role="button"]` and the scrollback (`style.css:110`, `:171`), which is more complete than most projects this size.
Two gaps beyond F10, F11 and F23: the `.reopen` tab and the `#resizer` are not keyboard-reachable, so a collapsed sidebar cannot be reopened without a mouse, and the drag-to-resize has no keyboard equivalent. Adding `tabindex="0"` plus an Enter handler to `.reopen` is S and fixes the reachability half; the resizer needs arrow-key handling and is M.

## Already good, keep it

- **The freeze-and-export model.** A paused surface's watermark rides along as `id_to` in every range mode.
  - Users never notice this kind of correctness and would be furious if it were wrong; the comment at `exportrange.js:262` earns its space.
- **The delete-session confirmation** (`settings.js:255`): names the run and the line count, and states that it cannot be undone.
  - This is the template every other destructive confirm should copy.
- **Section-level "applies live" / "applies on daemon restart" hints.** Four words that answer the question every settings dialog leaves open.
  - Extend the pattern rather than replacing it: the Storage one just needs to cover all five of its fields.
- **The remembered-session warning** (`exportdlg.js:143`): "session N is no longer in the list; the range moved to the newest run".
  - Said before falling back, instead of silently exporting a different run. Exactly right.
- **The disconnect-reason gloss** (`statusbar.js:234`): wire tokens become "device not present (power, cable, or still enumerating)".
  - The raw token is the fallback, so a future value stays visible instead of blanking the line.
- **Loss indicators that only appear when non-zero** (`style.css:79-82`): dropped lines and write failures read as warnings, not as more dim metadata.
- **The one-button session control**, and its refusal to treat the daemon's automatic session as "running".
  - The reasoning in `statusbar.js:132-141` is the right call, and the tooltip explains it to the user in one sentence.
- **The reserved-width lines/s box** (`style.css:88`): tabular figures in a fixed box so a changing readout does not shove the port chips. Small, invisible, and the reason the header holds still.
- **The empty states that do exist** (CAN, Plots): they name the wire events that will fill the panel, which is genuinely what the user needs to know. F3 asks for the same treatment in the terminal.

## Suggested order

Copy and contrast first, since they are hours not days: F1, F2, F4, F7, F13, F18, F19, F6.
Then the onboarding and safety work: F3, F5, F9, F20, F14, F16.
Then the input and accessibility pass: F11, F10, F12, F23, F15.
Then the larger consistency items: F8, F17, F21, F22, F24.
