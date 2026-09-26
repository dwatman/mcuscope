# Fix-diff review 2: the race and break fix batches (uncommitted tree on 89a6af3)

HEAD 89a6af3ec4b19ca798ab384a5f2005936d88ddf2. Target: `git diff` plus `host/tests/test_support.py`.
No HIGH or MEDIUM findings. The loss decision, `_stop_child`, the Win32 break and the short-write check hold up.

## Findings

### F1 LOW: the 3 s refusal settle is shorter than a slow Windows loser needs, and a 401 is usually decisive anyway
- `cli.py:2766-2770`, `cli_daemonctl.py:262-265`.
- The loser's child exits at spawn + interpreter start + imports + `CaptureLock.acquire`'s 2 s.
  - Measured here: `import mcuscope.daemon` takes 0.43 s warm on Linux, so the budget is 2.5 of 3 s.
  - `registry-triage/windows.md:85` records that Store Python starts slowly. Its import alone can pass 1 s.
- Scenario: two starts with different tokens on a LAN URL, on a Store Python desktop.
  - The loser's child is still alive at 3 s, so the loser prints `started mcuscoped (pid <loser>)` and exits 0.
  - Its child then fails on the lock, or takes the port if the winner stops within that window.
- The guard only refuses a start's own daemon in a narrow case (`server.py` `_TokenGuard` and the Host guard).
  - A token is runtime-only, so the child's token is `s.token`, which the CLI also sends.
  - Loopback clients are exempt.
  - So a 401 comes from the start's own daemon only when the CLI has no token and the child inherited `MCUSCOPED_TOKEN` from the environment.
- Fix: when `s.token` is set, or `MCUSCOPED_TOKEN` is unset in the CLI's environment, treat a 401 as a loss and call `_reap_losing_start` at once.
  - Keep the settle only for the ambiguous case. A longer settle there (the old 10 s) costs time only on that rare path.
- Live check: the 401 path cannot be reached on loopback. Six loopback races with different tokens (`smoke_refusal.py race 6`, port 18597) all went down the body path, because loopback skips the token.
  - Each gave exactly one `started` and one `...; stopped this start's own process (pid N)`, and left one daemon.
- Fixed (superseded by the owner's start-id ruling): the daemon echoes the start id on refusals too, so a refusal is ours or lost for certain and there is no settle at all. The 10 s settle ruling has no path left to apply to: an answer can lack the id only if it is not this start's daemon.

### F2 LOW: the Windows launcher test fails, rather than skips, where there is no launcher, and nothing flags it if it skips on CI
- `test_cli_daemon_stop_scope.py:580` asserts `pid != proc.pid`.
  - Suppose the venv's `python.exe` is a copied interpreter, not a launcher. As far as I know, uv falls back to that for a base Python that ships no `Lib/venv/scripts/nt` launcher, such as some python-build-standalone builds.
  - Then the child is the interpreter and the loss decision is exact, but the test fails.
- CI's setup-python CPython ships the launcher, so the test should run on `windows-latest`: it is in a venv, so the `sys.prefix` skip does not fire.
  - But nothing guards against it skipping. The workflow has skip guards only for the firmware and JS suites (`ci.yml` verify steps).
- Would it catch a surviving `python.exe`? Yes, if it runs. `pid_running` uses a zero-timeout wait on a process handle (`pidfile.py:80-97`), which is correct for a terminated interpreter, and the test has a positive control before the stop.
- Fix:
  - `if pid == proc.pid: pytest.skip("no launcher: the spawned process is the interpreter")`.
  - Add a Windows verify step that fails on `SKIPPED.*venv_launcher` in `pytest-report.txt`.
- Fixed: `pytest.skip` when the spawned process is the interpreter itself. A new Windows-only `ci.yml` step fails the job on any `SKIPPED` line naming the launcher, so a CI skip is loud.

### F3 LOW (test gap): nothing pins that a start answered by its own child skips the settle wait
- Mutant M2 (`if refusal is not None:` changed to `if True:` before the settle) survives `test_cli_daemon_stop_scope.py`.
- On a real `Popen` that mutant adds 3 s to every successful start.
  - The live `smoke_refusal.py single 2` start took 0.75 s, so today's code does not pay it.
- Fix: a `_start_with` success case whose body names `pid: 999997`, asserting `child.calls == []` and exit 0.
- Fixed: the settle is gone. Three tests assert `child.calls == []` on a start answered by its own daemon (body, refusal, and the two-deep launcher case). Mutant R19 (a 10 s wait on every answer, the settle's successor) fails all three.

### F4 NIT (test gap): only a zero count is tested as a short write
- Mutant M13 (`n == 0 and data` in place of `n != len(data)`) survives `test_link.py`.
  - An aborted Win32 write returns the bytes that did go out (`serialwin32.py:322-323`), so a partial count is the Windows case.
- Fix: a stub `ser` whose `write` returns `len(data) - 1`, through `SerialLink(stub, "COM9").write`.
- Fixed: `test_link.py::test_a_partial_write_raises` (9 of 10); M13 now fails it. See `windows-fixbatch-break.md` Round 2.

### F5 NIT: Ctrl-C after the child exited leaves the record naming a dead pid
- `cli.py:2699-2703`: the handler names a live child, but keeps the record when `proc.poll()` is not None.
- The record is stale, not dangerous: claim and stop both treat it as stale. `test_ctrl_c_after_the_child_exited_does_not_call_it_running` does not assert on it.
- Fix: `else: _remove_pid_record(pid_path, proc.pid)` in the handler, and assert that in the test.
- Same area: a Ctrl-C inside `_write_pid_record` (between `Popen` and the `try`) prints no note. The window is tiny, and widening the `try` to start right after the spawn closes it.
- Fixed: the handler removes the record naming an exited child; the test asserts it, with a positive control. The pid record write moved inside `_await_spawned`, so a Ctrl-C there names the child too (new test).

### F6 NIT: the unstoppable reap branch drops the stderr tail
- `cli_daemonctl.py:297-300` returns before `_stderr_tail`, so the one failure most worth diagnosing shows none of the child's lines.
- Fix: append `_stderr_tail(err_path, start=err_start)` to that message too.
- Fixed: the unstoppable message ends with the stderr tail; the test asserts the child's line is shown and the winner's is not.

### F7 NIT: SPEC 3's `write_failures` gloss does not name the new cause
- `SPEC.md:695` says "(timeout, closed handle)". A cut-short write now fails as `port <alias> write failed: write cut short (n of N bytes reported written)` (HTTP 400 from `/send` and `/cmd`) and counts in the streak.
- Fix: add "cut short".
- Fixed: `SPEC.md:695` reads "(timeout, closed handle, cut short)".

## Checked and sound
- Loss decision (area 1):
  - `_stop_child` only ever acts on this start's own `Popen`.
  - On POSIX `proc.pid` is the daemon's pid (exec, same pid). On Windows the one-launcher premise holds for the CPython venv redirector and venvlauncher, which put the interpreter in a kill-on-close job. So a winner can be stopped only if a launcher chain deeper than one ever appears, as the diff documents.
  - After `_stop_child` returns True on Windows, the job has already been closed in the launcher's rundown, so the child cannot claim the port afterwards.
- Exits (area 1): every post-spawn exit matches SPEC 4 and class 89, and I traced each record decision.
  - Index-build ceiling: left running, named.
  - Abandon, and a reap that stopped the child: record removed.
  - Unstoppable child: record kept, named.
  - Refusal where the child exited: record removed, tail shown.
  - Ctrl-C: named if the child is alive (see F5).
- Win32 break (area 2):
  - The private `WinDLL(use_last_error=True)` and `argtypes=[HANDLE]` are right, and the error is read before the `finally` clear.
  - The clear runs on every path.
  - `str(WinError(code))` carries the code.
- Short write (area 3): no normal write can fail falsely. pyserial 3.5 is both the floor in `pyproject.toml` and the installed version.
  - `serialposix` returns `length - len(d)` (the full length on a whole write).
  - `serialwin32` with `write_timeout=2` returns `n.value`, or raises on a short count unless the write was aborted. A stale last error of 995 cannot misfire, because a whole write has `n == len`.
  - `protocol_socket` returns the full length.
  - `rfc2217` returns `len(data)`.
  - An empty write returns 0 on every backend.
  - `loop://` and other handlers are refused by `ALLOWED_URL_SCHEMES`. `sim://` is `SourceLink`, whose `write` is its own.
  - The only false failure is the known one: POSIX reports 0 when `cancel_write` lands after `os.write`, which happens only on a port the reader is already closing.
  - How it is reported: `_write_bytes` gives HTTP 400 `port X write failed: write cut short (...)` and a `write_failures` increment.
- `symlink_or_skip` (area 4):
  - It skips only on 1314 or `NotImplementedError`.
  - Every caller links to a file, so `target_is_directory` is not needed.
  - The class 88 sweep finds 4 sites, as documented.
- Docs (area 5):
  - SPEC 4, the AI guide, ARCHITECTURE, classes 7/88/89/90 and `windows.md` match the code.
  - There are no U+2013 or U+2014 characters in any changed or new file.
  - `ruff check .` is clean and `test_cli_contract.py` passes (59).

## Revert table
Scratch copy `~/tt-data/mcuscope-2026-09-26/fixdiff2/host`, runner `mutate.py`.
- The runner checks every anchor before writing, then restores and asserts the restore.
- Runs use `PYTHONDONTWRITEBYTECODE=1`, an empty `PYTHONPYCACHEPREFIX`, `-p no:cacheprovider -p no:randomly`, and a fresh tar with no `__pycache__`.
- Baseline on the scratch copy: stop_scope 34 passed and 1 skipped; link 6; break_errors 8 and 1 skipped; serial_link_tx 17; port_health 27.

| Mutant | Result | Caught by |
|---|---|---|
| M2 settle wait on every answer | SURVIVED | none (F3) |
| M13 only a zero count is short | SURVIVED | none (F4) |
| M34 poll check before the settle wait | caught | `test_a_refused_probe_is_another_daemons_when_the_child_fails_on_the_lock` |
| M37 short write raises `ValueError` (escapes `_write_bytes`, a 500) | caught | `test_link.py::test_a_write_cut_short_by_cancel_write_raises` only; tx and port_health files pass |
| M38 Ctrl-C during the reap turned into a silent exit | caught | `test_ctrl_c_while_stopping_a_losing_child_names_it` |
| M39 unstoppable message drops the pid | caught | `test_a_lost_race_child_that_cannot_be_stopped_keeps_its_record[deaf0-refuse0]` |
| M40 clear skipped for a None handle | survived, equivalent | a None handle cannot be holding a break |

The fix batches' own N1 to N19 and W1 to W11 / X1 / Y1 to Y3 tables were not re-run.

## Also run
- On the repo tree: stop_scope, break_errors, link and support together gave 53 passed and 2 skipped (the two Windows-only tests).
- Live, port 18597, throwaway TOML with its own `db_path`, data and config dirs:
  - two single starts at 0.75 s each;
  - 6 of 6 concurrent races gave one start and one loser stopped at once.
  - Leftover daemons were killed by PID afterwards; `ps` shows no `mcuscope.daemon`.

## Not covered
- Anything on Windows: the launcher test, the real kernel32 test, a live break, and whether uv's CI venv really gets a launcher (F2 rests on my reading of uv, not a run).
- A live 401 race: it needs a non-loopback URL.
- The whole Python suite and the JS suite (per the brief).

## Scratch (not deleted)
- `~/tt-data/mcuscope-2026-09-26/fixdiff2/`:
  - `host/`, `tools/`: tree copy;
  - `mutate.py`;
  - `smoke_refusal.py`;
  - `smoke/`: TOML, db, data and config dirs.
