# Fix batch firmware-packaging (registry leg, 2026-09-25)

Base HEAD `3f10153`. Other batches edited the tree at the same time; my hunks are only in the files below.

Files changed:
- Simulator: `host/mcuscope/sim.py` (the `!p` overflow episode, handed over after daemon-process finished).
- Firmware: `firmware/monitor/{monitor.c,monitor_cmds.c,monitor.h,INTEGRATION.md}`, `firmware/monitor/port_template/monitor_port_template.c`, `firmware/tests/{fake_shims.c,test_monitor.c,Makefile,.gitignore}`, new `firmware/tests/test_port_template.c`.
- CI and packaging: `.github/workflows/{ci,release}.yml`, `host/pyproject.toml`, `host/contrib/config.example.toml` (R15-6, assigned here by `index.md`), `tools/webui_smoke.py`, new `tools/check_dist.py`.
- Tests: `host/tests/{test_firmware_monitor,test_scaffold,test_cli_sessions,test_sim,test_sim_tcp,test_serial_link_tx,test_sim_error_codes,test_break}.py`.
  - New: `test_webui_smoke.py`, `test_sim_shim.py`, `test_check_dist.py`, `test_config_example.py`, `test_sim_overflow_episode.py`.
- Docs: `docs/SPEC.md` (2.3 and 2.5 overflow notices, 2.4 `can filter`, 5.2 `monitor_mark`/`monitor_eventf`, 5.3), `docs/REVIEW.md`.

Monitor files the vendoring projects (charger-test, charger_control, relay_control) should re-copy: `monitor.c`, `monitor_cmds.c`, `monitor.h`, `INTEGRATION.md`, `port_template/monitor_port_template.c`.
- charger-test's `monitor_port.c:83` defines `mon_can_filter` (a no-op); it is now unused and should be deleted.
- None of the three defines `mon_i2c_xfer`, so their `i2c scan` changes from `OK` with an empty list to `ERR 7 nosup` (R16-4, intended).

## Findings

Revert-verify, all in private copies under `~/tt-data/mcuscope-2026-09-25/fix-firmware-packaging/`:
- `fw_mutate.py` (firmware, `make run` / `make port-template`): 22 of 23 caught. A `run` mutation counts only when the build succeeded and a check failed.
  - The miss ("a template block left disabled") is caught by the new pytest check below (`py_mutate.py`).
- `py_mutate.py` (Python, against a private copy of `host/mcuscope` on `PYTHONPATH`): 31 of 31 caught, plus the positive control for R43-2.

Per finding:

- R15-1 fixed. `_wait_ready(base, thread)` refuses when the harness's server thread died, and when `/status` answers with a pid other than `os.getpid()`. The harness binds its own socket first (`socket.create_server`), so a taken `--port` is refused before anything starts, and the default port is a free one.
  - Fails with the fix reverted: `test_webui_smoke.py::test_readiness_refuses_a_server_of_another_process` (pid check), `::test_readiness_refuses_when_its_own_server_thread_died`, `::test_a_taken_port_is_refused_before_any_check` (the foreign server receives no request), `::test_the_default_port_is_a_free_one_and_the_temp_dir_is_removed` (default back to 8558).
  - Positive control: `::test_readiness_accepts_its_own_process`.
  - Deleted the brief's `server.started` gate: with the harness holding the bound socket it is redundant, and no mutation of it could be caught.
- R15-2 fixed (D-2 A). The five modules import `from mcuscope import sim as mcu_sim`; `test_sim_shim.py` tests the shim (identity of re-exports, `--help` as a script), skipped without `tools/`.
  - Verified: `uv build --sdist` of a copy of the working tree (tracked plus untracked files), unpacked, `pytest --collect-only -q`: exit 0, 2874 tests. Positive control: the old `import mcu_sim` in one module fails collection there with ImportError.
  - Fails: `test_sim_shim.py::test_the_shim_re_exports_the_package_simulator` (a re-export dropped from the shim).
- R15-3 fixed. `tools/check_dist.py artifacts <dist> --source <host>` holds the ci.yml check (now also refusing an empty file in the sdist); ci.yml's build job and release.yml's build job both run it on what they built. release.yml's 4-name check is gone.
  - Fails: `test_check_dist.py`, one test per branch (missing file in wheel, in sdist; empty file in wheel, in sdist; the sentinels; the wheel count).
- R15-4 fixed. `tools/check_dist.py serve <scripts dir>` starts `mcuscoped --sim -c <throwaway toml> --port <free>` with `MCUSCOPE_*_DIR` in a temp dir and the update check off, polls `/status` with urllib, checks `db_path` is the one it configured (so a foreign daemon on the port cannot pass), runs `mcu --url ... daemon stop`, requires exit 0, then waits for the daemon to exit.
  - Wired into ci.yml's Linux wheel step, ci.yml's Windows wheel-smoke, and release.yml's Windows wheel-smoke (each gained a checkout step for the script).
  - The daemon's own exit status is printed, not judged: a graceful stop replays SIGTERM (`daemon._release_pid_on_terminating_signal`), so POSIX reports -15. My first version required 0 and failed on exactly that.
  - Verified locally: built the wheel, installed it into a scratch venv, `check_dist.py serve` passed. The CI run on the pushed branch is not done (no push).
- R15-5 fixed. `make port-template` builds the template with `-DMON_TEMPLATE_ALL` (the six `#if 0` blocks are now `#ifdef MON_TEMPLATE_ALL`) plus `-Wmissing-prototypes`, links it against the monitor and runs `test_port_template.c`, then runs the same checks over the weak defaults alone. `make arm-check` also compiles the template; `ARM_CC` is now overridable. New CI job `firmware-arm` installs `gcc-arm-none-eabi` and runs `make -C firmware/tests arm-check`.
  - Fails (`fw_mutate.py`): a template signature drifting from `monitor.h` (compile error), a template still defining a shim the header dropped (`-Wmissing-prototypes`).
  - Fails (`py_mutate.py`): `test_firmware_monitor.py::test_port_template_enables_every_shim_the_header_declares` for a block guarded by `#if 0` and for a shim missing from the template.
  - Pytest wiring: `test_firmware_monitor.py::test_port_template_matches_the_shim_contract` (2 summaries).
- R15-6 fixed. `test_config_example.py` loads the example with `load_config` (no warnings, the port read), and requires its sections to equal `Config`'s fields. The example lacked `[update]` and `[plotjuggler]`; both added from SPEC 3.3.
  - Fails: `::test_the_example_config_shows_every_section` (a section removed), `::test_the_example_config_loads_without_warnings` (a misspelt key). Positive control: `::test_a_misspelt_key_would_be_warned_about`.
- R15-7 fixed: the temp dir is removed in `main()`'s `finally`, after joining the server thread. Fails: `test_webui_smoke.py::test_the_default_port_is_a_free_one_and_the_temp_dir_is_removed`.
- R15-8 fixed: `grep -q 'SKIPPED.*test_webui_js' pytest-report.txt`. The firmware guard above it (ci.yml:95) had the same pipefail shape (`grep | grep | grep -viq`) and is now one `awk`.
  - Driven under `bash -eo pipefail` on a synthetic 6000-line report: the old JS guard missed the skip, both new guards flag it, and neither flags the expected MinGW ASan skip.
- R15-9 fixed: the docstring now says the suite starts the daemon in process and `_mcu_command` falls back to `python -m` without a wrapper. Docstring only, nothing to revert.
- R16-4 fixed. A probe result other than 0 or NACK ends `i2c scan` with that code. `monitor.h`, INTEGRATION.md and SPEC 5.3 state the convention.
  - Fails: `test_monitor.c` "i2c scan probe buserr" (`ERR 4`) and "... timeout" (`ERR 3`), and `test_port_template.c` `i2c scan` answering 7 over the weak default and over the template stub. Positive control: a fault at 0x78, outside the sweep, leaves `OK 48 50`.
- R22-6 fixed (D-9 A, firmware half): `monitor_mark` treats only U+0020 as blank. `monitor.h`, INTEGRATION.md and SPEC 5.2 say so; SPEC 2.5 already did (daemon-process).
  - Contradiction with the batch file's test text ("a marker whose text is the tab"): `write_line` keeps the wire 7-bit printable, so the tab goes out as `.`. The test asserts `!m @7 .` (and `!m @7   .  ` for `"  \t  "`), which keeps SPEC 2.2 true.
  - Fails: `test_monitor.c` "mark tab is text" with the tab restored to the blank set.
- R27-1 fixed. The fake records the last non-probe write, and 0x50 reads `A0+offset+i` from the offset the write half set.
  - Fails: "i2c wr bytes" with `cmd_i2c_wr` passing `wr_len` 0; "i2c wrrd register pointer" (`OK B0B1`) with `cmd_i2c_wrrd` passing `NULL, 0`.
- R27-2 superseded by the D-13 ruling: the hook it asked to observe is deleted. The software filter's id, mask and ext are pinned by existing tests; swapping id and mask, and inverting ext, in `cmd_can_filter` each fail "can filter x extended only" (`fw_mutate.py`).
- R27-3 fixed per the owner's D-13 ruling (not option A): the `mon_can_filter` call, its weak default, its declaration in `monitor.h`, the template stub and the fake are removed. INTEGRATION.md and SPEC 5.3 say a shim must not program a CAN hardware filter for the monitor.
  - SPEC 2.4 said "hardware filter usage is up to the port layer" and "`x` is also passed to the port layer for its hardware filter", contradicting the ruling. Rewritten, although 2.4 is outside SPEC 5.
  - Nothing to revert: a reinstated call fails to compile (no declaration, `-Werror`).
- R43-1 fixed: `ruff>=0.13`. `uvx ruff@0.13.0 check .` in `host/`: clean; `uvx ruff@0.12.12`: 7 UP038.
- R43-2 fixed: the lookup's query is compared through `parse_qs`, the paths decoded; the export path, which is the CLI's own quoting, stays raw.
  - Verified in a scratch venv with httpx 0.27.0: the new test passes, the old assertion fails (`py_mutate.py`).
- R43-3 fixed: `regex>=2026.2.19`. `test_pane_regex_dialect.py` in a scratch venv: 90 passed at 2026.2.19; 1 failed at 2026.1.15.
- R48-1 fixed: `wire_max()` bounds `ping` and `can stat` at `MON_OK_PAYLOAD_MAX + 1`, and replaces the two inline copies in `read_into_resp` and `i2c scan`.
  - Fails: "ping long name at seq 65535" (236-character name, same answer at seq 1 and 65535), "can stat long state at seq 65535", and, with `wire_max` returning `resp_max`, the existing scan and hex-clamp tests.
- Owner ruling on over-long `!p` at high rate, fixed as the owner's option A. Cut events of one type in a row are one episode.
  - The first cut is announced at once (`!e event <type> overflow`, unchanged). Later cuts of that type send only what they keep, and are counted; a cut that keeps nothing sends nothing.
  - The episode ends at the next event of its type sent whole, at a cut of another type, or after 1 s with no cut (2000 `monitor_poll` calls on a clockless port). It then sends `!e event <type> overflow cut=<n>`, before the whole event that ended it.
  - `monitor_init` drops an open episode.
  - Firmware: `g_ovf_*` state in `monitor.c`, `event_type()` shared by both paths and sanitizing as `write_line` will, and the notice built in its own 56-byte stack buffer because `g_out` may still hold the triggering line. The expiry check is in `monitor_poll`.
  - Sim: `OverflowEpisode` on each `Simulator` (`sim.overflow`), passed to `encode_lines` by every transport (TCP, in-process `SimSource.feed`/`poll`, pty). `poll_events` ends a quiet episode.
    - `encode_lines(lines)` with no episode keeps the old single-pass behaviour.
    - Found by the new tests: a pass whose only line was a silently counted cut encoded as a blank line. `encode_lines` now returns `b""` for an empty result.
  - Fails with the fix reverted, firmware (`test_monitor.c`, `test_overflow_episode`, 11 mutations): whole event of the type ends it; same type counted, not re-announced; a cut of another type ends it; the quiet expiry; `>=` at exactly 1000 ms; the clockless poll count; the poll count reset on each cut (a second clockless episode); the quiet timer taken from the last cut; `monitor_init` dropping the episode; type sanitization (`a\x01` and `a\x02` are one episode); the `cut=` suffix.
  - Fails, sim (`test_sim_overflow_episode.py`, 14 mutations): the same branches, the empty-pass guard, the kept-nothing cut, and the episode wiring of each transport and each send helper (in-process poll and feed, TCP, pty).
  - C against sim parity, driven in scratch (`parity/`): 200 seeded random sequences of events through `monitor_eventf` and through `encode_lines` with one episode gave 0 mismatches, covering 1086 start notices and 955 count notices. A mutant C build (the whole-event close inverted) gave 182 mismatches. Not a committed test; the quiet expiry is outside it (no polls).
  - Not revert-verifiable: `g_ovf_count`'s saturation at `UINT32_MAX`. Without it, 2^32 cuts in one episode would wrap the count to 0, which reads as "no episode", and the count would be lost silently. Reasoned only.
  - Known difference, not fixed: the sim treats every whole `!` line (`!can`, `!ps`) as an event that can end an episode of its type. In the firmware only `monitor_eventf` and `monitor_mark` lines reach `event_end`. They differ only if an application cuts its own over-long `!can` or `!ps` through `monitor_eventf`.
- O-56b fixed: the legs list is its own `## Legs of a round`, ahead of `## Sweep discipline` ("the sweep discipline above" became "below").
- R20-5 (docs half) fixed: class 20 now points at the summary rebuild (`_scan_plot_rows`, pinned by `test_store_plot_summary.py`), not `Store.query_plot_channels`.
- Registry sweep wording fixed. Every leg's "Sweep imprecision" note, in all six leg reports, is now one line (a lead plus sub-bullets where it passed 200 characters) at the end of its class entry: 49 classes. Class 43's own command moved off `/tmp`.
  - Classes whose note said "none found" got nothing.
  - Class 53's "the gating floor is 0.1.0" was left out: D-16 raised `DAEMON_MIN_VERSION` to the current version.
  - `c58b.py`, `quantclock.py` and `c78py.py` were copied into `~/tt-data/mcuscope-tools/sweeps/class-<n>/`, and the sweep discipline names that directory once.

Footprint (`arm-none-eabi-gcc` 13.3, `-Os -mthumb -ffunction-sections -fdata-sections`, text / bss, whole object):

| Object | M0+ before | M0+ after | M4 before | M4 after |
|---|---|---|---|---|
| monitor.o | 4461 / 990 | 4823 / 1019 | 4503 / 990 | 4833 / 1019 |
| monitor_cmds.o | 2236 / 80 | 2246 / 80 | 2264 / 80 | 2266 / 80 |

The episode costs +366 B text and +29 B bss on M0+ (`event_type` 84, `overflow_notice` 104, `overflow_end` 32, `monitor_poll` +72, `event_end` +52); the other batch items net -4 B in `monitor.o` and +10 B in `monitor_cmds.o`.
`make arm-check ARM_CC=<13.3 path>` (Cortex-M4F, `-Werror`, now including the template) passes.

Single files green after the change: `test_sim_overflow_episode.py`, `test_sim_pty.py`, `test_sim_flood_stall.py`, `test_protocol_hex_grammar.py`, `test_firmware_monitor.py`, `test_webui_smoke.py`, `test_check_dist.py`, `test_config_example.py`, `test_sim_shim.py`, `test_cli_sessions.py`, `test_scaffold.py`, `test_sim.py`, `test_sim_tcp.py`, `test_serial_link_tx.py`, `test_sim_error_codes.py`, `test_break.py`; `make run`, `asan`, `families`, `families-asan`, `port-template`.
Ruff: clean on every file I touched, and `ruff check .` in `host/` reports only two F811 in `test_server_export_admission.py`, which is another batch's in-flight file.

## CHANGELOG

- **Upgrade (firmware, vendored ports):** `mon_can_filter` is removed from `monitor.h`; `can filter` is the monitor's software filter in every form and never programs hardware. Delete `mon_can_filter` from your `monitor_port.c`, which nothing calls now.
- Firmware `i2c scan` answers the probe's error (`ERR 4 buserr`, `ERR 3 timeout`, and `ERR 7 nosup` on a port with no I2C shim) instead of `OK` with an empty or partial list.
- Firmware `monitor_mark` refuses only space-only text; a tab is text (sent as `.`).
- Firmware and `mcu-sim`: an over-long event is announced once per run of cuts of one type, `!e event <type> overflow` at the first cut and `!e event <type> overflow cut=<n>` when the run ends, instead of a notice after every cut line (SPEC 2.3). This replaces the unreleased entry at CHANGELOG.md:32, which says each cut line is followed by the notice.
- Firmware `ping` and `can stat` are bounded by the line limit, so a long port name answers the same at every seq instead of `ERR 8 overflow` at a long one.
- `tools/webui_smoke.py` serves on a free port by default, refuses a taken `--port`, never runs its checks against another daemon, and removes its temp dir.
- Declared floors: `ruff>=0.13`, `regex>=2026.2.19`.
- `contrib/config.example.toml` shows the `[update]` and `[plotjuggler]` sections.

## Needs another batch

- cli (`AI_GUIDE` in `cli.py`, currently line 3054): replace the four lines that start `"!e event p overflow": a !p line over 255 bytes was cut at` with:

  ```
                                  "!e event p overflow": !p lines over 255 bytes are being
                                  cut at a space (trailing pairs lost). Later cut lines
                                  carry no notice; "!e event p overflow cut=<n>" ends the
                                  run with its count, at a !p that fits, a cut of another
                                  type, or 1 s with no cut. A cut keeping nothing past the
                                  type or a marker's @<tick> is not sent at all; a !p whose
                                  first pair does not fit arrives as "!p <tick>".
  ```

  The current text also says `"!p @<tick>"`, but the monitor keeps `!p <tick>` (SPEC 2.3), so that part was already wrong.
- Orchestrator: the three vendoring projects re-copy the monitor files listed at the top.

## Needs Windows

- ci.yml and release.yml `wheel-smoke`: `python tools\check_dist.py serve $scripts`, including `shutil.which` finding `mcuscoped.exe` through PATHEXT and the daemon exiting after `mcu.exe daemon stop`. Not run.
- `make port-template` under MinGW-w64 (`./test_port_template` via MSYS make), reached through `test_firmware_monitor.py`.
- `test_webui_smoke.py`: `socket.create_server` refusing a port another socket holds (no SO_REUSEADDR on Windows), and uvicorn serving a pre-bound socket.

## Needs a browser

Nothing.

## Cleanup owed

These directories are still under `~/tt-data/mcuscope-2026-09-25/fix-firmware-packaging/` and need a recursive delete, which needs the owner's confirmation:
- `base/` (4.2 MB): a copy of `firmware/` at HEAD, used for the "before" footprint;
- `fp/` (32 kB): footprint object files;
- `rv/` (62 MB): a venv with regex 2026.2.19 and httpx 0.27.0, which `py_mutate.py` uses for R43-2;
- `sdist/` (40 MB): the working-tree copy, the built sdist and wheel, the unpacked sdist and the wheel venv;
- `parity/` (48 kB): the C against sim fuzz (`drv.c`, `fuzz.py`), rerunnable.

`fw_mutate.py` and `py_mutate.py` beside them can be rerun.

The mutation scripts themselves `rmtree` their per-run copy (`fwcopy/`, `pycopy/`), each one they had just created, without a manifest.
I ran `tools/webui_smoke.py --no-wait` once with no `MCUSCOPE_*` isolation, before the tests existed. Its capture was in its own temp dir (removed); the daemon's release check may have written the real user cache once.

## The two questions

1. **Least confident, rechecked.**
   - The CI YAML is unrun. Rechecked locally: both workflows parse (`yaml.safe_load`), `check_dist.py artifacts` passed on a real `uv build` output, and `check_dist.py serve` passed against an installed wheel.
     - Not rechecked: pwsh quoting, and Ubuntu's apt `gcc-arm-none-eabi` (not 13.3) under `-Werror` in `firmware-arm`.
   - R22-6's wire form: rechecked that `write_line` rewrites every byte below 0x20, so no test can see a raw tab on the wire.
   - The episode: the claim "the sim matches `event_end`" was driven by the parity fuzz, not reasoned. The quiet-second test first passed with `last` never updated, and now uses timestamps taken around each cut.
   - The REVIEW.md insertion script: checked that the 86 class headings are in order, that every added line sits in its own class, and that no `\'` escapes remain (seven had to be removed).
2. **What we had not thought about.**
   - The firmware skip guard in ci.yml had R15-8's pipefail shape too (fixed and driven).
   - A graceful `mcu daemon stop` leaves `mcuscoped` with status -15 on POSIX by design. Any CI step or tool that judges the daemon's exit status will fail on it.
   - SPEC 2.4 contradicted the D-13 ruling. None of the vendoring projects implements `mon_i2c_xfer`, so R16-4 changes their `i2c scan` answer.
   - The empty-pass blank line in the sim was reachable only once a cut could send nothing; the new test found it.
   - `docs/REVIEW.md` is 122 kB, four times the 30 kB split threshold in the owner's rules. I did not split it (see below).

## Open for the owner

Split `docs/REVIEW.md` by topic (runbook, legs, registry in class ranges)? Recommended yes, as its own change, since CLAUDE.md and the memory notes point at the single file.
