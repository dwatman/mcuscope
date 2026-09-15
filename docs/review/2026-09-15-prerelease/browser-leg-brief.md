# Browser leg: common brief

You script checks from `~/git/mcuscope/docs/review/2026-09-15-prerelease/manual-verify.md` in headless Chromium and report pass or fail with evidence.
Report only: do not edit anything under `~/git/mcuscope` except your own report file, and run no git commands.
If a checklist line contradicts `docs/SPEC.md` or the code, report the contradiction instead of bending the check to pass.

## Your space

- Work dir: `~/tt-data/browser-leg/<name>/` (scripts, logs, configs, downloads, fake boards). Nothing goes in `/tmp` or the repo.
- Daemon: `~/git/mcuscope/host/.venv/bin/mcuscoped --config <your toml>`, adding `--sim` for the in-process simulator. Use only the HTTP port and TCP ports assigned to you; never 8558.
- Config TOML: `[server] host = "127.0.0.1"`, your port; `[storage] db_path` inside your work dir; `[update] check = false`. Ports go in `[[ports]]` tables (SPEC 3.3), e.g. `device = "socket://127.0.0.1:<tcp port>"`.
- Environment for every daemon, sim and CLI you start: `MCUSCOPE_DATA_DIR`, `MCUSCOPE_CACHE_DIR`, `MCUSCOPE_CONFIG_DIR` set to subdirs of your work dir.
- Status from the shell: `~/git/mcuscope/host/.venv/bin/mcu --url http://127.0.0.1:<port> status`. `curl` is intercepted in this environment; use `mcu` or Python.
- Record the PID of every process you start. Stall and resume the daemon with `os.kill(pid, signal.SIGSTOP)` / `SIGCONT` from inside the script; stop with `SIGTERM`. Never kill by name.

## Driving the page

- Run scripts with `uv run --no-project --with playwright==1.62.0 python script.py`; `p.chromium.launch(headless=True)`.
- Module state: `page.evaluate("async () => { const m = await import('<same URL the page loaded>'); ... }")` returns the page's own module instance when the URL matches exactly (check `index.html` and the import specifiers).
- `page.route` can delay or hold a single HTTP request (e.g. `/lines`) without stalling the daemon; it does not touch `/ws`.
- Downloads: `page.on("download")` or `page.expect_download`. "Nothing downloads" needs a positive control in the same run: the same listener catching a download that should arrive.
- Collect `console` messages and `pageerror` events for every page and report any error.
- The UI's stall deadline is `STATUS_TIMEOUT_MS = 2000` in `webui/state.js`.

## Boards

- `~/git/mcuscope/host/.venv/bin/mcu-sim --tcp-port N [--plot|--demo|--flood LPS]` is a standalone board; stopping and restarting it on the same port acts as a board reset (the daemon reconnects).
- Where the sim cannot produce the input (a chosen tick value, a redefined `!pd`, two boards interleaved), write a small Python TCP fake board in your work dir. Protocol: SPEC 2 and 7, plot lines and the tick base in SPEC 9.2; `host/mcuscope/sim.py` shows what a board answers (`ping`, `info`).

## Discipline

- Assert on text or state unique to the path under test.
- An item that cannot be asserted mechanically: mark it NOT RUNNABLE with one line on why and what a human would look at. A proxy check (e.g. ARIA attributes for a screen-reader item) is reported as PROXY, not PASS.
- A FAIL is re-run once to rule out timing before it is reported; say if it is intermittent.

## Report

Write `~/git/mcuscope/docs/review/2026-09-15-prerelease/browser-<name>.md`:

- One line per item: `PASS` / `FAIL` / `PROXY` / `NOT RUNNABLE`, the checklist item's label, one-line evidence, script path.
- A "Defects" section: for each FAIL, the repro steps and the observed against expected, in a few lines.
- No em or en dashes. Brief.

Reply with one line of counts only. If writing the report file is refused, reply with the full report instead.

## Clean up before replying

Stop every process you started by PID (daemons, boards, browsers) and confirm none remain by checking those PIDs. Chromium's temp profile goes when `browser.close()` runs; delete any profile dir you created yourself. Leave scripts, logs and downloads in your work dir as evidence. Delete nothing outside your work dir.
