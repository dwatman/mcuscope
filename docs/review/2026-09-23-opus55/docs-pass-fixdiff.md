# Docs pass, fix-diff leg

Files edited: `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`.
Every SPEC item was checked against the code at its `file:line` before it was applied.
The test suite was not run.

## Missing from the tree when this pass ran

These cross-batch edits had not landed, so the wording that depends on them was left out:

- `/status` has no `ppid` (grep of `server.py`). The CLI reads it (`cli_daemonctl.py:280`), but nothing sends it.
- `GET /ports` rows have no `serial_number` (`serial_link.py` `status()`).
- `mcu daemon start` does not wait for an index build: there is no `building index` in `cli.py`/`cli_daemonctl.py`, although `store.py:651` says the CLI keys on it.
- `_ExportJob.on_open` sets no `set_progress_handler` (store finding 9).

## link

- SPEC 3.4 `reconnect`: 400 when the port was detached, re-attached or disconnected during the reconnect. Code: `serial_link.py:1401-1408`, `server.py:1178-1183`.
- SPEC 3.2 item 7: a Windows console close gets the graceful shutdown. Code: `_stdio.py:81-89`, `daemon.py:401,473`.
- ARCHITECTURE `daemon.py`: "hold the console close (Windows)" added to the startup order, plus "closing the console window on Windows arrives as SIGINT via the ctrl handler".
- CHANGELOG, all three lines merged into existing unreleased entries:
  - the SIGHUP line now also covers the Windows console close;
  - the reconnect-race line;
  - a sub-bullet under the `rx_dropped` entry.

## chrome

- SPEC 9 reload badge: compares against the stamped version, and a page with no stamp compares against the first version it saw. Code: `statusbar.js:99-103`.
- SPEC 3.4 export: the web UI's download sends `wait=1`.
  - Code disagrees with the wording: only the session `.db` navigation adds it (`state.js:407`), not the bundle. The text says "`.db` download".
  - Merged with the server batch's `wait` sentence.
- SPEC 9 divider: also for live rows dropped while the first backfill ran; N counts at most the missing ids. Code: `api.js:233-254`.
- Found without a proposal: SPEC 9 (the user-text line, about line 1749) still listed a fixed set of invisible characters.
  - `state.js:146` escapes every default-ignorable code point and bidi control, and keeps an emoji presentation selector after a non-ASCII emoji. SPEC was corrected to match.
- CHANGELOG:
  - The badge, the `.db` wait and the invisible-character lines amend unreleased entries: the Added badge line, the Changed export-pool line, and the Changed bidi line.
  - The backfill-divider line amends the Fixed shed-divider line.
  - Left out: "the divider no longer counts rows the backfill fetched" and "a shed notice staged then is kept". Both fix unreleased work, and the amended divider line covers the result.
  - Left out: the command bar tooltip line. It is text for unreleased `auto` behaviour, which the existing Upgrade entry already covers.

## panes

- SPEC 9.2:
  - Quarter-scale wording: past a quarter of the double limit. Code: `plots.js:1140-1148`.
  - The axis tick sentence (`plots.js:1034-1041`).
- SPEC 9: paging past a divider (`pane.js:155-170`), and the stacked sidebar below 860 px, always shown (`style.css:439-443`).
- CHANGELOG: amended the two Fixed lines as the batch asked (the shed-divider line and the tick-label line). There are no new lines for P-1, P-2 or P-5.

## server

- SPEC 3.1:
  - The `Sec-Fetch-Site` scope, and that the guard does nothing for a plain-HTTP load of a LAN address.
  - Framing headers apply to route and guard responses, not the plain-text 500.
  - Both are docs-only corrections; there was no code change to check.
- SPEC 3.3.1: the root redirect `/` is exempt from the undeclared-parameter check. Code: `server.py:382`.
- SPEC 3.4 `/assert`:
  - The dropped-unjudged `empty` rule, placed after the `allow_empty` sentence.
  - Its reason text added to the `reason` list. Code: `server.py:2999-3002`.
- SPEC 3.4 `/purge`: the duplicated `before_ts` sentences became one, with the `id_to` delete bound. Code: `server.py:1760-1766`, `store.py:2868-2880`.
- SPEC 3.4 export: the `wait=1` sentence. Code: `server.py:1543,1587,2835-2853`.
  - Kept: a client that leaves while waiting starts no build.
- SPEC 9.1 technology constraints: the stamped `index.html` and its ETag. Code: `server.py:912-930`.
- ARCHITECTURE export pool: "(or a wait, with `wait=1`)".
- CHANGELOG, merged into existing unreleased entries:
  - the assert Upgrade line gains a sub-bullet;
  - the forbid-at-deadline fix joins the live-pool Fixed line;
  - purge `before_ts` joins its Fixed line;
  - the config-loader skips extend the `spy://` Fixed line;
  - the false `export failed` log joins the cancelled-export Fixed line;
  - `wait=1` joins the export-pool Changed line;
  - the version stamp joins the Added badge line.
- CHANGELOG, new line: `pydantic>=2.0.2` (Fixed).

## store

- SPEC 3.2 item 2: the rate is averaged over about half a second. Code: `store.py:258-262,953-955`.
- SPEC 3.2 item 2, added: an older capture builds its missing indexes before the daemon answers, with the two log lines. Code: `store.py:650-663`.
  - Left out: "`mcu daemon start` waits for the build". The CLI edit has not landed, and the batch said to add it only once it does.
- SPEC 3.4 `/purge`: merged with the server wording above.
  - Left out: the store's extra sentence "so `deleted` equals the dry run's count". It is not true against an earlier, separate dry run. The server sentence already states the bound.
- SPEC time bounds rewritten: when the 10 s bound breaks, and the two announcing `sys` rows. Code: `store.py:193,1030-1036`.
- SPEC schema: `idx_lines_port_chan_id` (`store.py:72`).
- ARCHITECTURE:
  - A failed commit leaves its ids spent, and only the fallback resyncs. Code: `store.py:1126`; the old post-failure resync is gone.
  - The summary is also marked dirty by a delete during a rebuild. Code: `store.py:1680-1699`.
  - A new bullet on the late-row `sys` announcements.
- CHANGELOG:
  - The coalescing line and the index-build line amend unreleased Changed entries.
  - The late-row announcement is new in Added.
  - The summary snapshot fix is a new Fixed line: the summary shipped in 0.4.0.
  - Purge is merged into its existing line.
  - Left out: "A session export abandoned before its first statement stops". The `on_open` progress handler has not landed.

## cli

- SPEC 4 `daemon stop`: signals only when the record names the process `/status` reports as its `pid`, so a stale record's pid is not signalled. Code: `cli_daemonctl.py:270-300`.
  - Left out: "or on Windows its `ppid`". `/status` sends no `ppid`.
- Left out: SPEC 4 "`start` succeeds only when `/status` names its child as `pid` (or `ppid`)".
  - The point of the item was `ppid`, which has not landed.
  - Without `ppid` the sentence is also not quite the code: `cli.py:2669-2672` passes when `/status` reports no pid at all.
- SPEC 4 `attach`: the retarget note (`cli.py:396-403`); `AI_GUIDE` already names it.
  - Left out: "a serial binding written `serial <SN>`". The old-target side needs `serial_number` in `/ports`. Until that lands, re-attaching the same serial prints a false note.
- SPEC 4 exit codes: "a WebSocket connection", with `tail -f` and `can dump -f` included (`cli.py:1261-1265`).
  - Also corrected the `can dump -f` give-up sentence: exit 1 when the daemon accepted and stopped answering. Code: `cli.py:2205`.
- SPEC 4 text output: line boundaries are escaped on every sink. Matches `AI_GUIDE` `cli.py:2846-2848` and `render.py:15-18`.
- SPEC 3.4 `/lines/export` `text`: line boundaries inside `raw` are escaped (`server.py:3360-3363` through `fmt_line`).
- Left out: SPEC 3.4 `GET /status` `ppid` and `GET /ports` `serial_number`. Neither is in the code.
- CHANGELOG:
  - `daemon stop` amends the unreleased Fixed line 374.
  - The `tail -f`/`can dump -f` exit code is a sub-bullet on the Changed Upgrade exit-1 line.
  - The `raw`/assert rendering fix is a new Fixed line: the `--decode` crash exists in 0.4.0.
  - The closed-stdout warning amends the unreleased `>&-` line.
  - The Windows `.err` append amends the "CLI appends to `.err`" sub-bullet.
  - Left out:
    - the attach malformed-`/ports` crash: it is in the note code, which is unreleased (0.4.0 has no `was attached to`);
    - the serial-retarget half of that line, and the venv `daemon start` success (both depend on code that has not landed);
    - the `start /b` console-hold line: it keeps released behaviour, and the SIGHUP/console line covers the result;
    - the Docs line (README/guide): no behaviour change.

## firmware

- SPEC not touched, as briefed.
- CHANGELOG:
  - The overflow-notice line is a sub-bullet on the firmware cut Upgrade line.
  - The sim `can filter` fix amends the firmware filter Upgrade line ("the simulator matches").
  - The flash figure changed from 2.3 to 2.2 KB, with "that does not call `monitor_eventf`" added.
  - The CAN-bus-drop notice and the i2c/spi `ERR 8` refusal are new Fixed lines.

## Checks run

- Every scripted replacement asserted that its anchor occurred exactly once before any write (python `assert s.count(a)==1`).
- `git diff -U0` over the three files, grepped with `grep -P '[\x{2013}\x{2014}]'`: no em or en dashes added.
- Not run: the test suite (per brief), and `test_cli_contract.py`. `AI_GUIDE` itself was not changed.

## Top-up (integration batch)

Everything listed above under "Missing from the tree when this pass ran" has now landed, and the items left out because of it are applied below.
Each one was checked against the code first.

### SPEC
- 3.4 `/status`:
  - `ppid` added to the JSON (`server.py:1063`);
  - the `ppid` sentence added after the `pid` sentence.
- 3.4 port rows:
  - `serial_number` added to the JSON (`serial_link.py:1293`);
  - a one-line meaning added before `disconnect_reason`.
- 3.4 `GET /ports`: the `stored` sentence (`server.py:1144`, `store.py:2003`). `""` is excluded because the walk starts at `port > ''`.
- 3.4 `/lines/export` `text`: the `[port]` form and its rule (`server.py:1906,3370-3379`).
- SPEC 3.4 bundle, line 870: no change, because `lines.txt` uses the same rule (`server.py:1631`).
- SPEC 4 exit-code/read paragraph: the stored-ports rule, and that `-p` reads a detached board's history. Matches `AI_GUIDE` PITFALLS (`cli.py:2854-2858`).
- SPEC 4 `daemon` row:
  - the index-build wait and its note text (`cli.py:2665-2680`);
  - the `ppid` clause restored in `stop` (`cli_daemonctl.py:270-282`).
  - The `start` clause is worded from the code, not the proposal: "fails (exit 1, `another daemon is already serving`) when `/status` names neither its child as `pid` nor, on Windows, as `ppid`" (`cli.py:2688-2692`).
  - The proposal's "succeeds only when" was not exact: a body with no pid passes the check.
- SPEC 4 `attach` row: "a serial binding written `serial <SN>`" restored (`cli.py:396-403`, now fed by `serial_number`).
- SPEC 3.2 item 2: "`mcu daemon start` waits for the build rather than timing out."

### ARCHITECTURE
- Under `store.py`: the `stored_ports()` bullet.
- Under the export-pool line: an abandoned build is stopped by the progress handler (`server.py:2793`), and `checkpoint()` stops the later bundle members (`server.py:2795`).
- Under `cli_daemonctl.py`: the readiness wait keys on the two notices, duplicated as literals (`cli_daemonctl.py:104-105`).

### CHANGELOG
- The unreleased Changed `[port]` line now has two sub-bullets:
  - the stored-rows rule and where it applies (the CLI reads, the text export, the web UI's all-ports pane export, which is `format=text` via `terminal.js:605-621`, and a bundle's `lines.txt`);
  - the daemon's own rows do not count.
  - Folded in, not listed as Fixed: the single-board `[port]` fix, because the column is unreleased.
- The unreleased Changed index-build line has a sub-bullet: `daemon start` waits for the build.
- The Added `mcu attach` note line now covers a serial binding, and a same-serial re-attach is quiet.
  - The note is unreleased, so this is an amendment rather than a Fixed line.
- New Added line: `GET /ports` `stored`, `/status` `ppid`, port-row `serial_number`.
- New Fixed line (Windows): `daemon start` from a venv reports success, and `daemon stop` can signal the recorded launcher.
- Left out: "a session export or bundle abandoned before its copy's first statement stops".
  - Cancel-stops-copy is unreleased, and the existing Fixed line "A cancelled session export or bundle stops its copy" now states the whole behaviour.

### Owner question carried from the integration report
- The index-build note ends `Ctrl-C leaves it building (pid N)`, not `mcu daemon stop abandons it`, because `daemon stop` cannot stop a daemon still building.
  - The code has the recommended text, and SPEC 4 quotes it.
  - If the owner picks the other wording, SPEC 4's `daemon` row changes with it.

### Checks run
- Every replacement asserted its anchor exactly once before any write.
  - The first SPEC run failed on a chained anchor, and nothing was written; it was rerun with the checks in sequence.
- `git diff -U0` over the three files, grepped for U+2013/U+2014: 0 added.

## Top-up 2 (fixbatch2-cli, fixbatch2-ui-fw, fixbatch2-daemon)

Each item was checked against the code first; all matched, so every SPEC item is applied.
The tests were not run: the code is settled and a test run was in progress.

### fixbatch2-cli
- SPEC 4 `daemon` row:
  - The `ppid` stop rule: judged on `/status` going quiet, signalled only if `/status` still names it after the grace, and `restart` waits for the launcher. Code: `cli_daemonctl.py:339-377`.
  - The 600 s ceiling with its exit text (`cli_daemonctl.py:114`, `cli.py:2691`).
  - Whole-line notice matching (`cli_daemonctl.py:108-112`).
  - A daemon reporting neither `pid` nor `ppid` is accepted (`cli.py`, `if serving and ...`).
- SPEC 3.2: "for up to 600 s of building (SPEC 4)".
- SPEC 4 read paragraph: the `[port]` rule counts attached and stored ports as one set. Code: `cli.py:890-898`, `server.py:3422`.
- ARCHITECTURE `cli_daemonctl.py`: "matched as whole log lines and capped at 600 s". This was the report's suggestion.
- CHANGELOG:
  - The 600 s ceiling amends the index-build sub-bullet.
  - The union rule amends the `[port]` sub-bullet.
  - The ppid re-check and the restart wait are a sub-bullet on the Windows venv line.
  - Left out:
    - the `WinError 5` stderr-handle fix, because the Windows append-only handle is unreleased;
    - the `db_path` quoting the notice words, because the index-build wait is unreleased.
    - In both cases the amended lines state the final behaviour.

### fixbatch2-daemon
- SPEC 3.1: `pydantic` added to the dependency list, with the `>=2.0.2,<3` sub-bullet (`pyproject.toml:45`).
- SPEC 3.4 export `wait=1`:
  - The cap of 8 waiters and the refusal text (`server.py:2717-2718,2886`).
  - A disconnect while waiting starts no build; a disconnect during the build abandons it. Code: `server.py:2862-2921`.
- SPEC 3.4 `/lines/export` `text`: the union rule.
- SPEC 3.2 item 7: the console close caps the in-flight wait at 3 s. Code: `daemon.py:132,307`.
- CHANGELOG:
  - The disconnect case amends the cancelled-export Fixed line.
  - The cap of 8 amends the export-pool Changed line.
  - The 3 s cap amends the SIGHUP/console line.
  - `<3` amends the pydantic line.
  - The server port-column line is covered by the amended `[port]` sub-bullet.
  - New Fixed line: the config loader strips `device`/`serial_number` (`config.py:466-473`). The unstripped load is in 0.4.0 (`config.py:347` there).
  - Left out: the `_top_ts`-after-purge fix, because the late-row announcement is unreleased.

### fixbatch2-ui-fw
- SPEC 2.3: a `!p` whose first pair does not fit keeps its tick. This matches `INTEGRATION.md:455` and the report's pinned tests.
- SPEC 9: a filled divider goes, and a partly filled one keeps a reduced count. Code: `pane.js:161`, `terminal.js:544-566`.
- CHANGELOG:
  - The divider removal amends the Fixed shed-divider line.
  - New Firmware Fixed line: the `i2c scan` shorted-bus list gains one address. `monitor_cmds.c:266-267`; 0.4.0 clamped at `MON_OK_PAYLOAD_MAX`.
  - Left out: "staged rows keep one shed notice per hole", because the staging is unreleased.

### Owner questions carried from the reports
- The daemon report asks whether the bundle's `wait=1` wait should move ahead of `store._sweep_lock`. Nothing in the docs depends on the answer.
- The daemon report notes that the web UI does not handle the 503 past 8 waiters on its `.db` download. SPEC 3.4 now states the cap; SPEC 9 is unchanged.

### Checks run
- Every replacement asserted its anchor exactly once, in sequence, before the single write per file.
- `git diff -U0` over the three files, grepped for U+2013/U+2014: 0 added.
