# Batch firmware-packaging

Firmware monitor fixes, CI and packaging, the smoke harness, and `docs/REVIEW.md`.
Owner-pick items use the recommended option in `decisions.md` unless the owner answered otherwise.
The monitor is vendored by charger-test, charger_control and relay_control; the report lists the changed monitor files so the owner can re-copy them.
ARM toolchain: STM32CubeIDE's arm-none-eabi-gcc 13.3 is not on PATH (see the memory note); pass its path to `make arm-check`.

## Files

- Firmware: `firmware/monitor/monitor.c`, `monitor_cmds.c`, `monitor.h`, `INTEGRATION.md`, `port_template/`, `firmware/tests/*` (including `fake_shims.c`, `Makefile`), `host/tests/test_firmware_monitor.py`.
- CI and packaging: `.github/workflows/ci.yml`, `release.yml`, `host/pyproject.toml`, `tools/webui_smoke.py`, new `tools/check_dist.py`.
- Tests you may edit: `test_scaffold.py`, `test_cli_sessions.py`, `test_sim.py`, `test_sim_tcp.py`, `test_serial_link_tx.py`, `test_sim_error_codes.py`, `test_break.py` (imports only), plus new files.
- Docs: SPEC 5 (all of it), `INTEGRATION.md`, `docs/REVIEW.md` (the whole file, including other batches' sweep wording).

## Findings

### R15-1 MEDIUM: the smoke harness runs its checks against whatever answers the port
- Checked: tools/webui_smoke.py:176 `_wait_ready(base)` succeeds on a foreign daemon when its own uvicorn failed to bind; the checks then send commands and attach/detach `board2` there. Default port 8558 is the user's daemon.
- Fix: before any check, require the harness's own server (`server.started` with the thread alive) and `/status` `pid == os.getpid()`; otherwise exit 1 naming the port. Default `--port` to a free ephemeral port.
- Test: a test holding a plain HTTP server on the port runs the harness's readiness function and gets the refusal.

### R15-2 LOW, owner-pick D-2: the sdist's tests cannot be collected
- Checked: five modules `import mcu_sim` (test_sim.py:18, test_sim_tcp.py:20, test_serial_link_tx.py:13, test_sim_error_codes.py:9, test_break.py:80), found only through the repo's `tools/`.
- Fix (D-2 option A): `from mcuscope import sim as mcu_sim` in all five; one test of the `tools/mcu_sim.py` shim itself, skipped when `tools/` is absent.
- Verify: `uv build` of a `git archive HEAD` copy, unpack the sdist, `pytest --collect-only -q` in it exits 0.

### R15-3 LOW: the published artifact gets a 4-name check, the full check runs on a separate build
- Checked: release.yml:111-130 against ci.yml:149-219.
- Fix: move the ci.yml package-data check into `tools/check_dist.py` (wheel and sdist), run by both workflows on the files they build.

### R15-4 LOW: nothing starts `mcuscoped` serving through its console script
- Fix: the Windows wheel-smoke job starts `mcuscoped.exe --sim -c <throwaway toml> --port N`, polls `/status` with `python -c` and urllib, then `mcu.exe --url ... daemon stop` and checks the exit code. Same on the Linux wheel step.
- Verify: the CI run on the pushed branch (Windows leg in `windows.md`).

### R15-5 LOW: firmware shipped forms no job builds
- Fix: a CI job installing `gcc-arm-none-eabi` and running `make -C firmware/tests arm-check`; a Makefile target compiling `port_template/monitor_port_template.c` with its six `#if 0` blocks enabled (a `-D` switch in the template), with host gcc and linked against `monitor.c`/`monitor_cmds.c`, wired into `test_firmware_monitor.py`.
- Test: the new target fails if a shim signature in the template drifts from `monitor.h`.

### R15-6 LOW: `contrib/config.example.toml` is loaded by no test
- Fix: a test loads it with `load_config`, asserting no warnings and every section present.

### R15-7 LOW: the smoke harness leaves its temp dir behind
- Checked: tools/webui_smoke.py:162.
- Fix: remove it in the harness's `finally`.

### R15-8 LOW (latent): the JS-ran guard can pass silently under pipefail
- Checked: ci.yml:103 `grep "SKIPPED" ... | grep -q "test_webui_js"` under `-eo pipefail`.
- Fix: `grep -q 'SKIPPED.*test_webui_js' pytest-report.txt`.

### R15-9 LOW: stale docstring
- Checked: test_scaffold.py:78 says the suite drives `python -m mcuscope.cli`; `test_cli.py:33` uses the console script.
- Fix: correct the sentence.

### R16-4 LOW: `i2c scan` answers OK when the bus cannot be probed
- Checked: monitor_cmds.c:270 treats every non-zero probe as "no device"; `monitor.h:147` says a probe returns 0 or `MONITOR_ERR_NACK`.
- Fix: a probe result other than 0 or NACK ends the scan with that code (NOSUP from the weak default, BUSERR, TIMEOUT, BUSY).
- Tests (firmware/tests): weak-default build `i2c scan` answers `ERR 7 nosup`; a fake answering BUSERR at one address gives `ERR 4`.
- SPEC 5.3: the probe convention and the scan's answer.

### R22-6 LOW, owner-pick D-9 (firmware half)
- Checked: `monitor_mark` trims space and tab (monitor.c:908).
- Fix (D-9 option A): blank means U+0020 only, matching the host half (daemon-process). SPEC 5 text if it states the set.
- Test: `mark \t` sends a marker whose text is the tab.

### R27-1 MEDIUM: the I2C fake never reads the write half
- Checked: fake_shims.c:253 `(void)wr;`; 0x50 answers `A0..` whatever the offset, against its own comment.
- Fix: the fake records the last write (bytes, length) and 0x50 reads from the written offset; tests assert `i2c wr` bytes and the `i2c wrrd` register pointer.
- Revert-verify: monitor_cmds.c:299 passing `wr_len` 0, and :345 passing `NULL, 0`, each fail.

### R27-2 LOW: the CAN filter fake records only the bus
- Checked: fake_shims.c:222.
- Fix: record id, mask and ext; a test asserts `can filter 100 7FF x` programs `(0x100, 0x7FF, true)`.
- Revert-verify: monitor_cmds.c:217 `mon_can_filter(bus, mask, id, !ext)` fails it.

### R27-3 MEDIUM, owner-pick D-13: `can filter all` cannot widen a hardware filter
- Checked: monitor_cmds.c:187-194 change only the software filter; INTEGRATION.md permits a hardware filter in `mon_can_filter`.
- Fix (D-13 option A): `all` also calls `mon_can_filter(bus, 0, 0, false)`, and `monitor.h`, `INTEGRATION.md` and SPEC 5.3 say a zero mask opens the filter to both id kinds; `none` stays software-only.
- Test: the fake records an open call on `can filter all` after a mask filter.

### R43-1 LOW: ruff floor fails lint
- Checked: `ruff>=0.5` in host/pyproject.toml; 0.5.0-0.12.12 report 5 `UP038`.
- Fix: `ruff>=0.13`. Verify with `uvx ruff@0.13.0 check .`.

### R43-2 LOW: a test pins httpx's space encoding
- Checked: test_cli_sessions.py:74 asserts `name=run+1`; httpx 0.27 sends `%20`.
- Fix: compare the decoded query (`urllib.parse.parse_qs`).

### R43-3 LOW: regex floor refuses `\z`
- Checked: `regex>=2024.0`; `\z` is refused before 2026.2.19 and the dialect fixture expects it accepted.
- Fix: `regex>=2026.2.19`. Verify `test_pane_regex_dialect.py` at that version.

### R48-1 LOW: `ping` payload bounded by the local buffer, not the wire
- Checked: `cmd_ping` (monitor_cmds.c:101-111) and `cmd_can_stat` (:223-249) fill `resp_max`; the answer then depends on the seq's digit count.
- Fix: clamp to `MON_OK_PAYLOAD_MAX + 1` as `i2c scan` does (:266).
- Test: a 236-character port name answers the same at seq 1 and seq 65535.

### O-56b LOW: `docs/REVIEW.md`'s "A round is these legs" list sits inside class 43
- Checked: REVIEW.md:561-631.
- Fix: move it out to its own heading.

### R20-5 (docs half)
- The class 20 entry's `/plot/channels?port=` example names a method no handler runs (daemon-api deletes it); point it at the summary path.

### Registry sweep wording (every leg)
- Each leg's "Registry sweep precision" section lists improved sweep commands and one missing shape per class. Fold them into the class entries in `docs/REVIEW.md` (one line each, per the brevity rules), including other batches' classes.

## Owner rulings 2026-09-25 (override the options above)

- D-13 ruled differently: the monitor must never change hardware configuration the firmware relies on. `can filter` is software-only in every form; remove the call into the shim's hardware filter hook (`monitor_cmds.c:217`) and the hook itself from `monitor.h`, the port template and the fake shims; `INTEGRATION.md` and SPEC 5 say shims must not program CAN hardware filters for the monitor. Breaking the vendored shims' extra function is fine (it becomes unused).
- Over-long `!p` at high rate: one overflow notice per episode, with a count when the episode ends, instead of one per cut line.
