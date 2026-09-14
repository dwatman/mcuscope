# Web UI UX review: status bar, terminal panes, shared toolbar, command bar

Read-only review at HEAD 2149806, scoped to `index.html`, `style.css`, `app.js`, `chrome.js`, `pane.js`, `terminal.js`, `cmdbar.js`, `statusbar.js`, `state.js`, `theme.js`.
Lens: an embedded engineer watching a board, and an AI agent driving the UI alongside `mcu`.
Items already closed by the 2026-09-07 and 2026-09-12 rounds, and items on the P2 backlog (global keyboard shortcuts, marker list with click-to-jump, command autocomplete), are not re-raised.

17 findings, ranked by value.

## Findings

### F1. A pane showing nothing looks identical to a broken page (S)

**Where.** `terminal.js:156` `render` (nothing is drawn for `rows.length === 0`), `terminal.js:133` `updateShown`, `index.html:344` the `.scrollback` in `paneTpl`.

**Today.** Untick `evt` on a sim pane, or type a regex that matches nothing, and the scrollback becomes a plain empty rectangle.
The only feedback is the footer, which reads `0 lines` when no regex is set and `0 / 2966 lines` when one is.
A pane added before any traffic arrives looks exactly the same as a pane whose filter is wrong, which looks exactly the same as a dead WebSocket.

**Why it hurts.** Every other panel on the page has an empty state (`index.html:81` CAN, `index.html:90` plots) and the terminal, which is the largest surface, has none.
The first thing a new user does is tick channel buttons, and the first way they lose the view is by ticking them all off; nothing on screen names the cause.
An agent screenshotting or scraping the pane cannot distinguish "no data" from "filtered out".

**Change.** In `render`, when `pane.rows.length === 0`, put one `.empty-state` div in the vlist whose copy names the likely cause, chosen from state already in hand: no rows in the shared buffer at all ("waiting for the first line"), rows in scope but killed by the pattern ("N lines in scope, none match the regex"), nothing in scope ("no lines from this port on these channels"), or cleared ("cleared; scroll up to pull older lines from the capture").
`updateShown` already computes the in-scope total for the regex case.

**Effort.** S.

### F2. The new per-port rate reintroduces the chip jitter SPEC 9.1 exists to prevent (S)

**Where.** `statusbar.js:314` builds `<span class="meta rate">`, `statusbar.js:216` `renderPortRates` writes it on every poll; `style.css` has no `.chip .meta.rate` rule (grep for `meta.rate` returns nothing).

**Today.** The cell is empty on the first poll, then fills with `78/s`, then with `1234/s`, then empties again after a daemon restart.
Each transition changes the chip's width, which moves every chip to its right, and the buttons the user is reaching for move with them.

**Why it hurts.** SPEC 9.1 spends a whole bullet on this exact failure: "Status-bar readouts that come and go must not move the things beside them... The lines/s figure sits in a reserved, fixed-width box (tabular figures)... Both used to shove the chips sideways every time the traffic changed."
`#lineRate` got that treatment (`style.css:88`); the per-port cell landed one level down without it, so the defect is back inside the chips instead of beside them.
The detach `x` and the reconnect `↻` are 14 px targets at the right end of a chip that moves every 5 s.

**Change.** Add `.chip .meta.rate { display: inline-block; min-width: 6ch; text-align: right; font-variant-numeric: tabular-nums; }` and keep it displayed when empty, mirroring `#lineRate`.

**Effort.** S.

### F3. The command bar never says which port "auto" resolved to (S)

**Where.** `cmdbar.js:62` `targetAlias()` computes it exactly as the daemon does, and uses it only to seed eol and mode; `index.html:115` the select shows the literal string `auto`.

**Today.** With two ports attached and one connected, the select reads `auto`, the eol select reads `port default (crlf)` and the mode toggle sits on `cmd`, all three derived from a port the user is never shown.
The command lands on whichever port the daemon resolves.

**Why it hurts.** This is a control that writes bytes to hardware.
The bench memory records the ST-LINK being moved between two boards; "which board am I about to talk to" is the one question the bar must answer before Enter, and it is the one thing it does not print.
The resolution is already computed, correct, and thrown away.

**Change.** In `populateCmdPort`, label the auto option with what it resolved to: `auto (sim)` when `targetAlias()` is a real alias, plain `auto` when it is not, refreshed from `syncCmdMode`/`syncCmdEol` which already run on every status poll.
Only the option text changes; the value stays `auto` so `cmdPortValue()` is untouched.

**Effort.** S.

### F4. Clicking a CAN id destroys a hand-built pane filter with no way back (S)

**Where.** `can.js:333-345` `setPaneFilter` / `clearPaneFilter`, `terminal.js:763` `filterPaneTo`.

**Today.** The click overwrites `panes[panes.length - 1]`'s regex.
That is the rightmost pane, which in a three-pane layout is as likely to be a carefully built `board-a` view as a scratch pane.
The panel's `unfilter` button calls `paneFilter("")`, which clears the box rather than putting back what was there.

**Why it hurts.** The feature is new and its cost is invisible until it has already eaten a pattern the user spent a minute on, and `unfilter` reads like an undo while being a second destructive action.
Nothing in the id cell's affordance warns that a click is going to rewrite another panel.

**Change.** Two lines in `terminal.js`: `filterPaneTo` stashes the pane's current `regexSrc` on the pane before overwriting it, and an empty pattern restores the stash instead of clearing.
Change the `canFilterClear` tooltip from "Clear the terminal filter an id click applied" to "Restore the pane's previous filter".

**Effort.** S.

### F5. The command result strip resizes the panes without telling the virtualizer (S)

**Where.** `cmdbar.js:115` `showResult` / `cmdbar.js:108` `hideResult` toggle `#cmdResult.hidden`; `terminal.js:161` caches `pane.viewH`; `terminal.js:226` invalidates it only from `onResizeRedraw`.

**Today.** The strip is inside `<footer class="cmdbar">` and the app is `grid-template-rows: auto 1fr auto` (`style.css:40`), so showing it takes about 24 px off the workspace row and hiding it (5 s later, or 9 s after an error) gives it back.
Neither transition calls `scheduleResizeRedraw`, so every pane keeps rendering a screenful sized for the other height.

**Why it hurts.** After the strip auto-dismisses, the panes are taller than the cached `viewH` and the virtualizer renders too few rows, leaving a blank band at the bottom of every scrollback until something else triggers a resize.
It appears a few seconds after every command, which is precisely when the user is watching the pane for the response.

**Change.** Import `scheduleResizeRedraw` from `plots.js` (as `terminal.js:7` already does) and call it at the end of `showResult` and `hideResult`, but only when `hidden` actually changed.

**Effort.** S.

### F6. Two different lines-per-second figures in the status bar, neither labelled (S)

**Where.** `index.html:22` `#lineRate` (global, from the WebSocket, `api.js:59`), `statusbar.js:314` the per-port cell (from `lines_rx` deltas between status polls).

**Today.** The daemon chip reads `mcuscoped 0.4.0  127.0.0.1:8558  up 5m33s  db 8.6 MB  78/s` and a port chip beside it reads `sim  /dev/ttyACM0  charger-test  78/s`.
Two figures in the same typeface and colour, measured over different windows, from different sources, distinguishable only by which pill they sit in.

**Why it hurts.** With one port they agree and nobody notices; with two ports they disagree and the reader has no way to know which is the total.
The bare `78/s` inside the daemon chip also reads as a property of `db 8.6 MB`, the token immediately to its left.

**Change.** Prefix the global one `rx 78/s` (it already has 7ch reserved, so widen to 9ch) and leave the per-port one bare inside the chip, where the alias supplies the scope.
Update the `#lineRate` title to say "total across all ports".

**Effort.** S.

### F7. Scroll-to-top history paging is completely undiscoverable (S)

**Where.** `terminal.js:629`, `pane.js:55-96`.

**Today.** A paused pane scrolled to its top silently fetches an older page from the capture and prepends it.
Nothing in the toolbar, the footer or the scrollback says the feature exists.
A user who wants older lines reaches for Settings, for `mcu lines`, or concludes the buffer is all there is.

**Why it hurts.** This is one of the more valuable things the UI does over a plain terminal, it took real work (the `HISTORY_HOPS` walk, the clear floor, the gap divider), and it is reachable only by accident.
The one piece of copy that mentions the capture at all is the gap divider's "gap: N lines not loaded", which shows up after the walk has already run.

**Change.** When a paused pane is at its top and `historyIdTo(pane)` is non-null, render a first row reading "scroll up for older lines from the capture", and swap it for "no older lines for this filter" once `historyDone` is set.
This shares the mechanism F1 needs, so land the two together.

**Effort.** S.

### F8. Double-click-to-copy is invisible, and the space that would advertise it says "5000 cap" (S)

**Where.** `terminal.js:634` the dblclick handler, `index.html:350` `<span class="dim">5000 cap</span>`, `style.css:177` `.ln.copied`.

**Today.** Copying a clipped line is a double-click, documented in a code comment and nowhere on screen.
The footer's right-hand slot instead carries the fixed string `5000 cap` in every pane, which never changes, is never actionable, and is meaningless without the sentence in SPEC 9.1 that explains it.

**Why it hurts.** `buildLine` puts the full text in a `title` precisely because rows clip at 18 px, and the tooltip cannot be selected, so double-click is the only escape from a truncated line - and the user has no reason to try it.
Meanwhile a per-pane constant occupies the one bit of persistent footer real estate.

**Change.** Replace the per-pane `5000 cap` text with `dbl-click a line to copy` (and move the cap into the `.shown` readout's `title`, where a reader who wants it will look).

**Effort.** S.

### F9. The regex box is 90 px wide for patterns that run to 30 characters (S)

**Where.** `style.css:152` `.match-group .match { width: 90px; }`, `index.html:336`.

**Today.** About 11 monospace characters are visible.
The pattern `can.js` itself generates for an id click is `!can[0-9]? +[0-9]+ +\S+ +321 `, 28 characters, so the box shows a fifth of it, and hand-editing one is scrolling a one-line text field blind.

**Why it hurts.** The regex filter is the pane's most powerful control and the least usable one.
A user cannot see whether the red `invalid` border is a typo three characters off-screen or a genuinely bad pattern.

**Change.** `.match-group .match:focus { width: 260px; }` with a transition, so the toolbar keeps its compact resting layout and the box grows to a workable size while it is being edited.
Also put two worked examples in the box's resting `title` (currently the bare "Client-side regex filter" set in `applyRegex`), since regex dialect and case sensitivity are otherwise guesses.

**Effort.** S.

### F10. The port chip is four dim tokens with no visual grammar (S)

**Where.** `statusbar.js:291-318` (alias, resolved device, target, rate), `style.css:78` `.chip .meta { color: var(--text-dim); }`.

**Today.** `sim  /dev/ttyACM0  charger-test  78/s`.
Only the alias is distinguished (600 weight); the other three are the same colour, size and weight, separated by an 8 px gap.
`charger-test` and `/dev/ttyACM0` read as two names for the same thing.

**Why it hurts.** The target field was added specifically to answer "which board is behind this port", and it cannot be told apart from the port path beside it.
The alias-versus-target distinction is subtle to begin with (the alias follows the cable, the target follows the board) and the rendering flattens it away.

**Change.** Give `.chip .meta.target` its own treatment: `font-style: italic` plus a `::before { content: "→ " }`, or the accent colour at reduced opacity.
One rule, no markup change.

**Effort.** S.

### F11. The segmented controls claim to be radiogroups but do not behave like them (M)

**Where.** `index.html:50` `#timeSeg`, `index.html:68` `#sideSeg`, `index.html:111` `#modeToggle`; the `aria-checked` fan-out in `terminal.js:691`, `app.js:38`, `cmdbar.js:94`.

**Today.** Each group is `role="radiogroup"` containing `role="radio"` buttons.
Every button is its own tab stop, and arrow keys do nothing.
A screen-reader user tabbing the page hears "radio, host, 1 of 4" and then finds that the arrow keys a radiogroup implies are inert.

**Why it hurts.** The ARIA roles are a promise about interaction, and the promise is not kept: keyboard users get four tab stops where the pattern specifies one, and the declared role actively misleads them about how to move within it.
Tab-through cost is real too: reaching the terminal from the status bar crosses eleven segmented buttons.

**Change.** One shared helper (`chrome.js` is the right home, beside `buildWindowButtons`): roving `tabindex` so only the checked button is tabbable, and a `keydown` handler mapping Left/Up and Right/Down to the neighbour, activating on move.
Wire the three existing groups to it.
`host/tests/webui_js/` can drive the keydown handler through the DOM stub.

**Effort.** M.

### F12. The shipped screenshot is three releases out of date (S, already owed)

**Where.** `docs/img/webui.png`, last written 2026-07-28 at 0.1.0; shown at `README.md:11` and `host/README.md:27`, so it is the PyPI landing image.

**Today.** It shows `mcuscoped 0.1.0`, a three-button time base with no `delta`, port chips with no target and no rate, a CAN head with no pause and no unfilter, and no byte-diff highlighting.
It is the first thing a prospective user sees and it advertises none of the last three releases' work.

**Change.** Recapture per `docs/SCREENSHOTS.md` before the next release.
Already tracked; listed here because it is the single largest first-impression gap in the reviewed surface.

**Effort.** S once captured (the capture itself has documented traps).

### F13. Sending a marker gives no acknowledgement (S)

**Where.** `cmdbar.js:209` `submitMarker`.

**Today.** Success clears the input and nothing else.
The marker arrives as a divider line over `/ws`, so it is visible only in a pane that has `mrk` ticked and whose regex does not exclude it, and only if that pane is live and scrolled to the bottom.
Failure does show in the result strip.

**Why it hurts.** Silence-on-success beside a visible-on-failure path trains the user to check the panes, and the panes may legitimately not be showing it.
The result strip is already built and already used by this function's error path.

**Change.** On success, `showResult("ok", "marker", text, null)`.
Two lines, reusing the strip's existing auto-dismiss.

**Effort.** S.

### F14. `2 / 5` and `5000 cap` are unlabelled numbers (S)

**Where.** `index.html:57` `#paneCount` (written at `terminal.js:556`), `index.html:350`.

**Today.** `2 / 5` sits beside `+ pane` with no title and no unit.

**Change.** Add `title="panes open / maximum"` to `#paneCount`; F8 handles the footer string.

**Effort.** S.

### F15. Line-ending "none" cannot actually be used from a text input (S, copy only)

**Where.** `index.html:118`, whose title reads "none sends a bare control character (Ctrl-C)".

**Today.** The tooltip names a use case the control cannot serve: a browser text input cannot capture Ctrl-C (the browser takes it as copy), so there is no way to type the control character the option exists to send.

**Why it hurts.** A user reading the tooltip tries it, gets nothing, and cannot tell whether the port, the daemon or the option is broken.

**Change.** Either correct the copy to describe what the option really does (append nothing, for a target that frames its own lines), or make the intent reachable by having the command input translate a typed `^C` / `\x03` escape when eol is `none`.
The copy fix is the lazy correct one; take the escape only if the owner wants the break-signal workflow.

**Effort.** S for the copy, M for the escape.

### F16. The command port select offers "auto" with zero ports attached (S)

**Where.** `cmdbar.js:39` `populateCmdPort`, `cmdbar.js:34` `cmdPortValue`.

**Today.** With nothing attached the bar looks fully armed: prompt, input, mode toggle, Enter.
The send fails with whatever the daemon returns, rendered in the strip as `error <http text>`.

**Change.** When `state.knownAliases` is empty, disable `#cmdInput` and `#markerBtn` and set the input placeholder to "attach a port to send commands".
The Attach button is already the primary action in the status bar, so the user is pointed at it.

**Effort.** S.

### F17. The brand's `web ui` tag spends the page's most prominent slot on nothing (nit)

**Where.** `index.html:15`, `style.css:48`.

**Today.** `MCUscope  web ui`, in the top-left corner of a page that is unambiguously the web UI.

**Change.** Drop it, or make it carry the daemon version so the top-left reads `MCUscope 0.4.0` and the daemon chip keeps the address and health.

**Effort.** S.

## SPEC contradictions

SPEC 9.1 contradicts itself about the port chip, and the code follows the later bullet.

- Line 21: "one chip per port showing alias, device, baud, connected state."
- Line 28: "...the short port name it landed on (`resolved_device`)... description, the requested device string when it differs, baud, `last_write_error`... are its hover."

`statusbar.js:296` renders `resolved_device` and puts baud and the requested device in `chip.dataset.tip`, which matches line 28 and not line 21.
Line 21 also predates target, the per-port rate, `rx_dropped` and `write_failures`, all of which line 28 lists.
Line 21 should be reduced to a pointer ("one chip per port; see below for what it carries"), leaving line 28 as the single description.
Flagged rather than worked around, per CLAUDE.md.

No other contradiction found in the reviewed surface: the four time bases, the pause-all label rule, the freeze watermark on pane export, the reserved lines/s box, the per-alias send-mode memory and the `OK monitor` default all match SPEC 9.1 as written.

## Already good, keep

- **The pause vocabulary is coherent.** The pane pill, the jump button's `↓ N new`, the shared `pause all` label that follows the surfaces, and clear-all leaving surfaces frozen are one consistent model, and the code says why at each point.
- **Failure states are named in plain English, not tokens.** `DISCONNECT_WHY` (`statusbar.js:234`), the `SLOW_MSG` regex-budget explanation that insists the view below is unfiltered, and the high-rate title that says CAN and plots are still live are all written for a person mid-incident.
- **Readouts that would move their neighbours are pinned.** `#lineRate`'s reserved tabular box and the high-rate notice's separate badge downstream of the chips (F2 is the one place this discipline slipped).
- **Colour carries meaning consistently.** One hue per channel, reused by the `.chk` toggles, the row tag and the row text, with the on/off states of a toggle rendered as tint-versus-grey rather than as two arbitrary colours.
- **The channel toggles are real buttons** with `aria-pressed`, so they are keyboard-operable for free, and the scrollback is `role="log"`.
- **The chip hover card is a CSS tooltip, not a native title,** specifically so a by-id path is not wrapped at the browser's chosen width; the reasoning is recorded at `statusbar.js:258`.
- **The `port default (crlf)` option text.** A default that prints the value it will produce, and can be picked again to escape an override, is the right shape for every such control on the page.
- **Empty states exist and are written, not templated,** on the CAN and plot panels ("No CAN frames seen yet. !can events populate this live."); F1 asks for the terminal to get the same treatment.
- **The dark palette is deliberate**, not a Bootstrap inversion: separate `--panel` / `--panel-2` / `--bg` levels, per-channel hues tuned per theme, and a mono/UI type split used consistently (mono for anything a machine produced, UI sans for chrome).
