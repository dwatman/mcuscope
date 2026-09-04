# Adversarial review: CLI and protocol

HEAD `7a1120f`. Date 2026-09-04.

Scope: `host/mcuscope/cli.py`, `cli_output.py`, `cli_daemonctl.py`, `protocol.py`, `config.py`, `_stdio.py`, `pidfile.py`, `update_check.py`.
Contract: `docs/SPEC.md` section 4 (CLI table, exit codes) and section 2 (wire protocol).

Method:

1. `docs/REVIEW.md` read in full; the sweeps for classes 5, 7, 9, 10, 11, 13, 17, 19, 22, 30, 33, 35, 44 run mechanically over the scope. Site counts and per-site verdicts are below.
2. `mcu ai-guide` diffed against the click command tree by introspection (`typer.main.get_command`, walking every subcommand's params).
3. `protocol.py` fuzzed: 12,515 random/truncated/NUL-injected lines through 17 decode entry points, plus 30,000 format/parse round trips (CAN, marker, command, response).
4. The stack driven live: throwaway daemon (`mcuscoped --config ~/.cache/mcurev/cfg.toml --port 8579 --sim`, own `db_path`), installed console script `host/.venv/bin/mcu`, never port 8558. Exit codes asserted as numbers.
   Ctrl-C driven with `SIGINT` restored to `SIG_DFL` in the child: a background job of a non-interactive shell inherits `SIG_IGN`, and `trap - INT` restores that, not the default. Both make Ctrl-C look ignored.

---

## Findings

### C1 (HIGH) `--json` output that cannot be written escapes as exit 120 with a traceback

`host/mcuscope/cli_output.py:157-160` (`out_json`), `host/mcuscope/cli_output.py:170-176` (`emit_stream`).

Both guard the write with `except BrokenPipeError` only, so any other `OSError` from the flushed write escapes the command, is re-raised by the dispatcher's broken-pipe-only arm (`cli.py:2296`), reaches `_stdio.console_entry`, prints a rich traceback and a crash log, and then the interpreter's shutdown flush fails over the top of it and exits **120**.

Observed:

```
mcu --json status      > /dev/full   -> 120 + traceback + ~/.local/share/mcuscope/mcu-crash.log
mcu --json tail  -n 3  > /dev/full   -> 120
mcu --json can dump -n 3 > /dev/full -> 120
mcu --json tail -f     > /dev/full   -> 120, and stderr first says
                                        "daemon unreachable at http://127.0.0.1:8579: [Errno 28] No space left on device"
mcu status             > /dev/full   -> 1   (unflushed print; main()'s flush arm maps it)
```

Scenario: an agent runs `mcu --json lines --last-ms 60000 > /mnt/ci/out.json` on a volume that hits its quota. SPEC 4 promises 0/1/2/3; the caller gets 120, no JSON, and a crash file. The non-`--json` form of the same command exits 1, so the machine-readable mode is the one that breaks the contract.

Registry: class 9 (a traceback reaching the user is a defect; every path maps to 0/1/2/3) and class 35's second face (buffered bytes making the shutdown flush raise over the mapped code) - the recorded instance was `err()`/stderr, this is the stdout siblings that were not swept.

Fix (minimal): in `cli_output.py`, widen both guards and remember the failure, without letting the write's exception become the exit code.

```python
_OUT_FAILED = False

def reset_output_state() -> None:
    global _OUT_FAILED
    _OUT_FAILED = False

def output_failed() -> bool:
    return _OUT_FAILED

def out_json(obj: Any) -> None:
    global _OUT_FAILED
    try:
        print(json.dumps(obj), flush=True)
    except BrokenPipeError:
        _silence_stdout()
    except OSError as exc:
        # A stdout that cannot be written is not a broken pipe and not the daemon's
        # fault; suppress it here (class 35: the write must not own the exit code),
        # neutralise the buffer, and let main() map it to 1.
        _OUT_FAILED = True
        _silence_stdout()
        err(f"cannot write output: {exc}")
```

Same two arms in `emit_stream` (keeping its `raise typer.Exit(0)` for the broken-pipe case only; a non-EPIPE `OSError` sets the flag and raises `typer.Exit(1)`).
In `cli.py`, call `cli_output.reset_output_state()` beside `set_json_mode(False)` in `_dispatch` (`cli.py:2263`), and in `main()` after `code = _dispatch(argv)`:

```python
    if code == 0 and cli_output.output_failed():
        code = 1
```

Test that fails without it: `test_json_output_to_a_full_stdout_exits_1` - run `cli.main(["--json", "status"])` with `sys.stdout` replaced by a stream whose `write` raises `OSError(errno.ENOSPC, "No space left on device")`, assert the return value is exactly `1` and that no exception escaped. Add the `emit_stream` twin driving `mcu --json tail -n 1`.

### C2 (MED) Ctrl-C during any non-follow command exits 130, not 1

`host/mcuscope/cli.py:2276-2277`.

SPEC 4: "Interrupting a `-f` follow with Ctrl-C is exit `0` ... Ctrl-C anywhere else is `1`."
typer converts a `KeyboardInterrupt` raised inside a command into `Exit(130)` (`typer/core.py:204`), and the dispatcher's `except EXIT_EXCEPTIONS` arm returns that code verbatim. The dispatcher's own `except KeyboardInterrupt` arm (which maps to 1 and emits the `--json` object) is only reachable for an interrupt raised outside the `app()` call, which is exactly what `tests/test_review_r2_cli.py:175` drives - so the real path has never been asserted.

Observed (SIGINT delivered with the default disposition restored):

```
mcu wait --match ZZZ --timeout 20000   -> rc=130, stderr empty
mcu log export                          -> rc=130, stderr empty
mcu tail -f                             -> rc=0    (handles its own, correct)
mcu can dump -f                         -> rc=0    (correct)
```

Scenario: a CI wrapper or an agent runs `mcu wait ... --timeout 300000`, the operator interrupts it, and the wrapper sees 130 - not a documented code, and silently distinct from the 1 the SPEC promises. `--json` consumers get no object at all.

Registry: class 9 (every path out of `mcu` maps to 0/1/2/3).

Fix: in `_dispatch`,

```python
    except EXIT_EXCEPTIONS as exc:
        code = int(getattr(exc, "exit_code", 0) or 0)
        # typer turns a Ctrl-C raised inside a command into Exit(130); SPEC 4 maps an
        # interrupt to 1. A -f follow catches its own and has already exited 0.
        if code == 130:
            return _dispatch_error("interrupted", 1)
        return code
```

Test: `test_ctrl_c_inside_a_command_exits_1` - monkeypatch `cli.Client.get` to raise `KeyboardInterrupt`, call `cli.main(["--json", "status"])`, assert `rc == 1` and `json.loads(out) == {"error": "interrupted", "exit_code": 1}`. It fails today with 130 and empty stdout.

### C3 (MED) `mcu log export -o FILE` leaves a truncated file when the export dies mid-stream

`host/mcuscope/cli.py:1368-1386`.

The rows are a generator that issues one `GET /lines` per page inside the `with open(out_file, ...)` block. A daemon that goes away mid-walk makes `Client.get` call `die(..., 3)`, which raises `typer.Exit` - not an `OSError`, so the `except OSError` arm does not see it - and the partly written file is left on disk. `plot export` (`cli.py:1788-1796`) and `Client.download` (`cli_client.py:186`) both remove theirs, citing exactly this ("indistinguishable from a whole one").

Observed, killing the daemon 0.35 s into an export of an 80k-row capture:

```
mcu log export -o exp.txt  -> rc=3, exp.txt left behind: 132696 bytes, 3000 lines
mcu plot export -o exp.csv -> rc=3, exp.csv removed
```

Scenario: `mcu log export --session boot-test -o run.txt` during a daemon restart archives 3 of 80 pages; the file exists, the shell script that made it may or may not check `$?`, and the archived run is silently short.

Registry: class 19's "one of two siblings" shape (the guard exists in the sibling exporter and the download helper, not here). New sub-clause candidate: *a writer that streams a remote resource to a file removes the file on any non-completion, not only on a write error.*

Fix: wrap the write loop the way `plot_export` does.

```python
    if out_file:
        ok = False
        try:
            with open(out_file, "w", encoding="utf-8", newline="\n") as fh:
                for row in rows:
                    ...
            ok = True
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)
        finally:
            if not ok:
                # A stream that died mid-walk leaves a short file that reads as a whole
                # one; same guard as plot export and Client.download.
                with contextlib.suppress(OSError):
                    os.remove(out_file)
```

Test: `test_log_export_removes_a_partial_file_when_the_daemon_dies` - a stub client that yields one full page then raises the unreachable `die(..., 3)`; assert `rc == 3` and `not os.path.exists(out_file)`.

### C4 (MED) A negative count option is answered "nothing found" with exit 0

`host/mcuscope/cli.py:677` (`lines --limit`), `:704` (`tail -n`), `:1475` (`can dump -n`), `:1176` (`session list --limit`).

None of these carries a `min=`. The daemon clamps a negative limit (`store.py:1044,1413,1632`: `max(0, min(int(limit), 1000))`), so the query returns zero rows and the CLI reports an empty capture:

```
mcu lines --limit -1        -> [0]  (no rows)  stderr: note: results truncated at 0 rows...
mcu tail -n -1             -> [0]  (no rows)
mcu can dump -n -1         -> [0]  (nothing at all)
mcu session list --limit -1 -> [0]  "no sessions recorded"
mcu log export --limit -1  -> [1]  usage error   <- the sibling that has min=0
```

Scenario: a script computes `--limit $((budget - used))`, the arithmetic goes negative once, and `mcu lines` answers "nothing matched" with exit 0. That is the dangerous direction: the tool is used to decide whether an error happened.

Registry: class 22 (a bound the wire has and the CLI does not) plus the one-of-siblings shape - `log export --limit` and `plot channels --active` are both bounded, these four are not.

Fix: `min=0` on all four options, matching `log export --limit`:

```python
    limit: int = typer.Option(100, "--limit", min=0),
    n: int = typer.Option(20, "-n", min=0, help="Number of recent lines to show first."),
```

(`-n 0` stays legal: SPEC 4 documents `mcu can dump -n 0 -f` as "no backfill, follow only", and `--limit 0` is the documented no-backfill probe.)

Test: `test_negative_limit_is_bad_usage` - parametrised over the four commands, assert exit 1 and that stderr names the option, not exit 0 with an empty result.

### C5 (MED) `--timeout 0` clears the client bound and is refused by the daemon

`host/mcuscope/cli.py:359-368` (`timeout_ms_option`), used at `:382-384` (`cmd`), `:1007-1009` (`wait`), `:1068-1071` (`assert`).

The callback accepts `0 <= value <= MAX_TIMEOUT_MS`. The server declares `timeout_ms` as `gt=0` for `/cmd` (`server.py:205`) and `/wait` (`server.py:231`), and `ge=0` only for `/assert` (`server.py:248`), where 0 means "retrospective". So the shared lower bound is wrong for two of the three callers:

```
mcu cmd "adc read vbat" --timeout 0 -> [1] error: timeout_ms: Input should be greater than 0 (got 0)
mcu wait --match x --timeout 0      -> [1] error: timeout_ms: Input should be greater than 0 (got 0)
mcu assert --expect x --timeout 0   -> [0] PASS (correct)
```

The exit code is right, but the callback exists precisely so this is bad usage naming the option rather than a 422 from a request that should never have been sent (its own docstring, and the same argument `BREAK_MS_OPTION` and `--eol` make).

Registry: class 19 (one rule, two implementations, the client's looser).

Fix: parameterise the lower bound.

```python
def timeout_ms_option(value: int | None, lo: int = 1) -> int | None:
    if value is not None and not lo <= value <= MAX_TIMEOUT_MS:
        raise typer.BadParameter(f"expected {lo} to {MAX_TIMEOUT_MS} ms, got {value}")
    return value


def _retrospective_timeout(value: int | None) -> int | None:   # `assert`: 0 is legal
    return timeout_ms_option(value, lo=0)
```

`cmd` and `wait` keep `callback=timeout_ms_option`; `assert` uses `callback=_retrospective_timeout`.
While there: `assert --min-window` (`cli.py:1072-1075`) has no bound at all while the server caps it at `MAX_TIMEOUT_MS` and refuses `min_window_ms > timeout_ms` - see C11.

Test: `test_cmd_timeout_zero_is_bad_usage` - assert exit 1 and that stderr contains `expected 1 to 300000 ms`, and that `mcu assert --timeout 0` still succeeds. Asserting on the message matters: the exit code alone is satisfied by the 422 the bug produces.

### C6 (MED) `--from`/`--to` accept non-ASCII decimal digits

`host/mcuscope/cli_output.py:198-201` (`_CLOCK_RE`).

The grammar is spelled with `\d`, and Python's `re` matches every Unicode decimal digit with it; `int()` then converts them.

```
mcu lines --from '١٢:٣٠' --limit 1        -> [0], window opened at 12:30 local
mcu lines --from 12:30 --to '١٣:٣٠'       -> [0]
```

The module's own docstring says "an explicit grammar rather than `fromisoformat`", and every sibling grammar in `protocol.py` uses `[0-9]` for exactly this reason (`_ENUM_VAL_RE`, `_MARKER_TICK_RE`, `update_check._VERSION_RE`, all carrying the comment). This one was written with `\d`.

Registry: class 22 (explicit character set, never `\d`/`isdecimal()`), same face as the `!m @٥٥` instance already closed in `protocol.py:989-991`.

Fix: `[0-9]` throughout `_CLOCK_RE` (or compile it with `re.ASCII`; the explicit set matches how the rest of the tree spells it):

```python
_CLOCK_RE = re.compile(
    r"^(?:(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})[T ])?"
    r"(?P<h>[0-9]{2}):(?P<mi>[0-9]{2})(?::(?P<s>[0-9]{2})(?:\.(?P<f>[0-9]{1,6}))?)?$"
)
```

Test: extend the `parse_clock` refusal test with `"١٢:٣٠"` (U+0661...) and assert `typer.BadParameter`. Use the Arabic-Indic digits, not `²`: the superscript is already refused by `\d`.

### C7 (MED) `autoconnect = "false"` still autoconnects the port

`host/mcuscope/config.py:380-381`.

`_as_bool(..., strict=False)` logs a warning and returns the *default*, which for `autoconnect` and `identify` is `True`. So the hand-edited string the helper was written for produces the behaviour the code comment says was the bug:

```
config: [ports.a] autoconnect must be true or false, not 'false'; using True
-> ports=[('a', 115200, autoconnect=True, 'lf', '/dev/x')]
```

The comment at `cli`-adjacent `config.py:370-375` reads "bare `bool()` read it as True: the port then opened itself on every start, which is the setting's exact opposite" - true of the current code too. The warning goes to the daemon log, which the person hand-editing `config.toml` is not reading.

Registry: class 22's "decide per value whether a bad one fails the load or falls back", with the `update.check` precedent stated one section above it: for a switch whose whole point is *not* doing something, resolving a typo towards doing it is the wrong way to be wrong. Opening a serial port is that kind of switch (it drives DTR/RTS on a bench).

Fix: for the two per-port booleans whose default is `True`, skip the entry rather than default it - the same call `baud` out of range already makes at `config.py:354-364`:

```python
        raw_auto = entry.get("autoconnect", PortConfig.autoconnect)
        if not isinstance(raw_auto, bool):
            log.warning("config: port %r autoconnect must be true or false, not %r; "
                        "skipping it", alias, raw_auto)
            continue
```

(Or keep `_as_bool(strict=False)` and pass `default=False` for `autoconnect`; skipping is the more honest answer, since the entry as written cannot be honoured.)

Test: `test_a_non_bool_autoconnect_does_not_open_the_port` - load a config with `autoconnect = "false"` and assert the port list is empty (or `autoconnect is False`), never `True`.

### C8 (LOW) `mcu ai-guide` omits six flags SPEC 4 documents

`host/mcuscope/cli.py:1998-2167`.

Mechanical diff of the click tree against `AI_GUIDE` (every option string of every non-hidden subcommand):

| command | flag missing from the guide | why it matters |
|---|---|---|
| `wait`, `assert` | `--raw` | the only way to send a line verbatim instead of as a monitor command; `--repeat-ms`'s guide text says "implies --raw" without ever defining it |
| `attach` | `--baud` | a 9600 target cannot be attached from the guide alone |
| `plot export` | `--wide` | the one-sample-per-row CSV shape |
| `plotjuggler` | `--save` | persisting the setting |
| `session start` | `--note` | |
| `daemon start` | `--config`, `--sim` | `--sim` is the zero-hardware demo |

(`--follow`/`--out` are long spellings of `-f`/`-o`, which the guide gives: exempt. `mcu daemon stop|status` appear as `mcu daemon start | stop | status`: exempt. `mcu ai-guide` does not name itself: exempt.)

CLAUDE.md requires the guide and the SPEC 4 table to change with every CLI change; the guide is the agent's only view of the CLI.

Fix: add to the CORE LOOP block

```
  --raw                           with wait/assert --send: write the line verbatim instead
                                  of as a monitor command (no seq, no response matching)
```

and `[--baud N]` to the `mcu attach` line, `[--wide]` to the `mcu plot export` line, `[--save]` to the `mcu plotjuggler` line, `[--note "..."]` to `mcu session start`, and `--sim` to the DAEMON CONTROL block.

Test: `test_ai_guide_names_every_flag` - walk `typer.main.get_command(cli.app)` recursively and assert every non-hidden option string appears in `AI_GUIDE`, with an explicit exemption set for the long aliases. This is the mechanical check that keeps the guide honest; it fails today on six flags.

### C9 (LOW) Two wire vocabularies are hand-mirrored where the source is already imported

`host/mcuscope/cli.py:291` (`EOL_CHOICES = ("none", "lf", "crlf")`, comment: "mirroring protocol.EOL_BYTES") and `host/mcuscope/cli.py:1420` (`BUS_OPTION ... min=1, max=9`, mirroring `protocol.CAN_BUS_MIN/CAN_BUS_MAX`).

`cli.py` already does `from . import protocol as p`, and `config.py:382` passes `p.EOL_BYTES` to `_as_choice` for the same domain. A fourth EOL spelling added to `protocol.EOL_BYTES` and to `server.Eol` would be refused by the CLI as bad usage, with a message listing the old three.

Registry: class 19 ("name the single implementation both use"; a mirror is a copy that stops being one silently). Both mirrors agree today - this is the sweep ruling them, not a live defect.

Fix: `EOL_CHOICES = tuple(p.EOL_BYTES)` and `BUS_OPTION = typer.Option(1, "--bus", min=p.CAN_BUS_MIN, max=p.CAN_BUS_MAX, ...)`.

Test: `test_cli_eol_choices_match_the_protocol` - `assert set(cli.EOL_CHOICES) == set(p.EOL_BYTES)`; today it passes by coincidence, afterwards by construction. (`MAX_TIMEOUT_MS`, `FOLLOW_MATCH_TIMEOUT_S`, `LINES_PAGE` and `DEF_LOOKBACK` are deliberate duplications with a stated reason - keeping the daemon's stack out of the CLI import - and all four still match their originals: `server.py:82`, `store.py:320`, `store.py:1044`, `serial_link.py:73`.)

### C10 (LOW) A local stdout failure inside `tail -f` is reported as "daemon unreachable"

`host/mcuscope/cli.py:968-969`.

`except OSError as exc: die(f"daemon unreachable at {s.url}: {exc}", 3)` wraps the whole follow, including `emit_stream`'s writes to our own stdout (`cli.py:935`). Any non-EPIPE `OSError` from printing a row is therefore attributed to the daemon and mapped to exit 3:

```
mcu --json tail -f > /dev/full
stderr: daemon unreachable at http://127.0.0.1:8579: [Errno 28] No space left on device
```

(The process then dies 120 through C1; with C1 fixed this arm still mislabels and still returns 3.)

Registry: class 17's shape for diagnostics - the message names a cause that was not measured. Related sub-clause: *a broad `except` around a loop that also writes output attributes the writer's failures to the reader.*

Fix: narrow the arm to the connection, i.e. move the emit out of its reach - simplest is to classify at the raise site by having `emit_stream` raise `typer.Exit` (C1's fix does this), which the `except OSError` arm no longer sees.
If C1 is fixed as proposed, this closes with it; assert it separately so the two cannot regress independently.

Test: `test_follow_output_error_is_not_reported_as_unreachable` - a follow whose `emit_stream` write raises `OSError(ENOSPC)`; assert the exit code is 1 and that stderr does not contain "unreachable".

### C11 (LOW) `assert --min-window` has no client-side bound

`host/mcuscope/cli.py:1072-1075`.

The server bounds it (`min_window_ms: le=MAX_TIMEOUT_MS`, plus "cannot exceed timeout_ms"), the client sends anything:

```
mcu assert --expect x --timeout 1000 --min-window 999999999 -> [1] min_window_ms: Input should be <= 300000
mcu assert --expect x --timeout 1000 --min-window 5000      -> [1] min_window_ms cannot exceed timeout_ms
```

Exit code correct, message a 422 rather than usage. The neighbouring `--timeout` on the same command is bounded locally for exactly this reason. Fold into C5's fix: `min=0, max=MAX_TIMEOUT_MS` on the option, and a local check `min_window > timeout` -> `die(..., 1)` in the daemon's words.

---

## Sweep verdicts

### Class 5 - argv hoisting in `cli.main()`

3 sites in scope (`_value_taking_opts`, `_split_global_opts`, `_hoist_global_opts`, all thin wrappers over `cli_argv`), driven across {global option before / after the subcommand} x {`break`, `sysrq`, `wait --repeat-ms`, `cmd`, `send`, `lines`} x {`--json`, `-p/--port`, `--url`} = 14 invocations, including the three subcommands added since 412c014.
All comply: `mcu ... break --json --ms 5`, `mcu ... sysrq --ms 5 b`, `mcu ... sysrq b -p sim` and `mcu ... lines --limit 1 -p sim --json` all resolve correctly and no value is stolen.

### Class 7 - pid record lifecycle

8 sites: `pidfile.read_pid_record`, `claim`, `release`, `pid_running`, `cli_daemonctl._write_pid_record`, `_remove_pid_record`, `_abandon_daemon`/`_stop_running_daemon`, `cli.daemon_stop`'s no-record branch.
Matrix driven: {no record, empty, garbled, live foreign pid, our own pid, stale pid} x {claim, release, stop}. All comply.
`claim` over a live foreign record returns None (unrecorded, as designed); over a stale record it re-reads before removing; `release` leaves a record another process rewrote; `daemon stop` with a dead daemon removes the stale record and exits 1, and a second `daemon stop` exits 1 with "no pid file".
The residual Windows compare-and-delete window is stated in `pidfile.claim` and remains open by design; not re-filed.

### Class 9 - CLI exit-code contract

34 `die()` sites, 11 `typer.Exit` sites, 35 `except` arms and 18 `raise` sites in `cli.py`; every literal code in the source is 0, 1, 2 or 3.
Violations: **C1** (120 from a full stdout under `--json`), **C2** (130 from Ctrl-C inside a command).
Complies: unreachable daemon 3 (REST, WS and the `can dump -f` give-up path), `/cmd` err 1 / timeout 2, `assert` 0/1 and never 2 (`timeout_code=1`), `daemon status` 3, usage errors 1, `Abort` 1, unknown command 1, bad global option 1, `KeyError`/`IndexError` from a skewed response 1, `--help | head` 0, EPIPE on every emitter 0, stderr closed on an error path still 3/1 (class 35's recorded bit holds).

### Class 10 - `--json` stdout purity

21 commands driven with `--json`; every one emits exactly one JSON document on stdout, or documented JSONL (`tail`, `log export`, `can dump` - the three SPEC 4 names).
Error paths too: unknown command, missing global-option value, prompt refusal, `/cmd` ERR, `wait` timeout, `purge` cancel. All prompts, notes, drop warnings and `note_truncated` go to stderr.
No violation. (C1 is a write *failure*, not a purity break.)

### Class 11 - codec symmetry in `protocol.py`

17 decode entry points fuzzed with a 12,515-line corpus (4,000 random lines over an alphabet including U+0663, U+0000, U+FFFF and astral characters; every truncation and every NUL-injection of 12 structured seeds; 8,000 targeted `!ps` lines against two real definitions).
**No exception escaped any decoder**: `parse_can_event`, `parse_plot_adhoc`, `parse_plot_def`, `parse_marker`, `parse_plot_value`, `decode_plot_sample`, `PlotDecoder.feed/points`, `classify`, `normalize_line`, `parse_can_family`, `is_decimal_token` all return `None`/a value as documented; `parse_command`, `parse_response`, `parse_hex_int`, `hex_to_bytes`, `parse_seq_token`, `parse_can_flags`, `parse_can_tx_args` raise `ProtocolError` and nothing else.
Symmetry: 20,000 `format_can_event` -> `parse_can_event` round trips over the full id/dlc/bus/tick/ext/rtr domain, 5,000 marker round trips, 5,000 command/response round trips - **0 failures, 0 asymmetric refusals**.
Clean.

### Class 13 - Windows file-sharing and encoding semantics

`os.replace`/`os.rename`: 1 site (`config.replace_atomic`), every writer routed through it (`config._write_doc`, `cli_daemonctl._write_pid_record`, `update_check._save_cache`) - complies.
`os.remove`/`unlink` on a path this process may hold open: 6 sites in scope (`cli_daemonctl:115,144`, `pidfile:212,233,246`, `cli:1796`, `cli:1974`). `pidfile.claim`'s close precedes its unlink (the recorded Windows bit); `plot_export`'s `os.remove` follows a `contextlib.suppress`ed `fh.close()` - complies.
`encoding=` at every read of user-editable text: `config.load_config`/`_read_doc` use `utf-8-sig` (BOM), `pidfile.read_pid_record` and `update_check._load_cache` use `utf-8` - complies.
Explicit `newline=`: `log export` `\n`, `plot export` `""`, `_write_doc` `""`, `_write_report` `""`, `_write_pid_record` `""` - complies.
`EINVAL`/`except BrokenPipeError`: 10 handler sites, all relying on `_stdio.translate_closed_pipe_errors`, which stays tty-gated; no handler classifies an errno - complies. The stdout guards being *too narrow* is C1, which is the class-9/35 finding, not an errno classification.

### Class 17 - reported value is the request, not the result

14 reported fields in scope. All comply: `note_truncated` reports rows returned (and only offers "raise --limit" when the user's limit was binding), `purge` prints `res["deleted"]`, `session delete` prints `res["lines_deleted"]`, `log export` prints its own count, `plot export` prints newlines received and `os.path.getsize`, `plotjuggler` prints the response's `enabled`/`dest`, `wait --repeat-ms` prints `res["sends"]`, `daemon start --json` prints the spawned pid after checking it against `_serving_pid`.
Exempt-because: `mcu break` and `mcu sysrq` echo the requested `ms` - `POST /break` answers `{"ok": true}` and reports no duration, so there is no result to read back; `mcu attach` echoes the requested device because the open has not happened when `POST /ports` returns (the resolved name appears in `mcu status`).
C10 is this class's shape one level over, in a diagnostic message rather than a health field.

### Class 19 - two engines validating one thing

7 duplicated validations in scope:

- `--eol` domain: **violates** (C9) - `EOL_CHOICES` hand-mirrors `protocol.EOL_BYTES` while `config.py` uses the dict itself.
- CAN bus range: **violates** (C9) - `min=1, max=9` literal against `protocol.CAN_BUS_MIN/MAX`.
- `--timeout` lower bound vs `server.CmdBody/WaitBody`: **violates** (C5).
- `--min-window` bound vs `server.AssertBody`: **violates** (C11).
- `repeat_ms`: complies - one implementation (`protocol.repeat_refusal`) called by both the daemon's 400 and the CLI's local refusal; all six refusal cases produce identical wording.
- `--match` engine: complies - `regex` plus `timeout=` on both sides (`_follow_match` mirrors `store._make_regexp`'s ceiling, both 0.25 s).
- `--rtr` DLC 0..8: complies - checked in the CLI and again in `parse_can_tx_args`.

Deliberate duplications with a stated reason, all still in sync: `MAX_TIMEOUT_MS` (300000 = `server.py:82`), `FOLLOW_MATCH_TIMEOUT_S` (0.25 = `store.MATCH_TIMEOUT_S`), `LINES_PAGE` (1000 = the store's clamp), `DEF_LOOKBACK` (20000 = `serial_link.PLOT_DEF_LOOKBACK`), `MIN_DB_CAP_BYTES`/`MAX_BAUD` (imported, not copied).

### Class 22 - a stdlib predicate standing in for a wire grammar

Coercion sites in scope reached by external input: 31.

- `protocol.py` (13 sites: `parse_seq_token`, `parse_response`'s ERR code, `parse_can_event` tick and RTR dlc, `parse_can_tx_args` dlc, `parse_plot_adhoc` tick, `parse_plot_def` sid, `_parse_enum_labels`, `decode_plot_sample` tick, `parse_marker` tick, `parse_hex_int`, `parse_plot_value`, `_decode_field`): **all comply** - `is_decimal_token`, explicit `"0123456789"` membership, `[0-9]` regexes, 16-hex-digit cap, `math.isfinite` after every float and after scaling.
- `pidfile.read_pid_record` (1): complies - `is_decimal_token` plus the 1..0x7FFFFFFF range; verified against `""`, `"+1234"`, `"1_234"`, `"٣٤"`, `"²"`, `"-1"`, `"0"`, `"2**32+1234"`, `"0x10"`, `"12.0"`, 5000 digits, `"12\x0034"` - every one `None`, and `pid_running` never raises on any of them.
- `config.py` (12 `_as_*` sites): comply as type/range gates; the *fallback direction* for `autoconnect` is C7.
- `update_check.parse_version` and `_load_cache` (2): comply - `[0-9]` regex, 64-character cap, `math.isfinite` on the cached timestamp.
- `cli_output.parse_clock` (6 `int()` calls behind one regex): **violates** (C6).
- Exempt-because: click's `INT`/`FLOAT` converters accept non-ASCII decimal digits from argv (`mcu lines --limit ٣` reads as 3). The token spells the number it converts to, nothing downstream re-parses it, and both float options are guarded by `finite_option`/`positive_option`, which reject `nan`/`inf`.
- Exempt-because: `bool(body.get("truncated"))` and the three `bool(match or chan)` calls are internal predicates, not wire values.

### Class 30 - a wrapper trusting an external runner's exit code

0 sites in scope: no test of these modules shells out to another test runner (the two that do, `test_webui_js.py` and `test_firmware_monitor.py`, are outside it). Exempt.

### Class 33 - a test that runs the real entry point inherits the user's environment

4 `platformdirs.*` call sites in the package, using 3 functions: `user_data_dir` (`pidfile.py:61`, `config.py:121`, `_stdio.py:293`), `user_config_dir` (`config.py:113`), `user_cache_dir` (`update_check.py:90`).
`tests/conftest.py:32-41`'s autouse `_isolated_user_dirs` patches exactly those three. Complies.
Note for the record: this review's own live runs used `--config` with a `db_path` under `~/.cache/mcurev` on port 8579, never a bare `mcuscoped`; the one thing that did reach a real user path is `~/.local/share/mcuscope/mcu-crash.log`, written by C1's crash backstop.

### Class 35 - an error-reporting write failing hijacks the exit code

Error-path writes in scope: 6 (`err`, `err_write`, `die`, `out_json` from `die`, `_dispatch_error`, `confirm_or_exit`'s prompt).
stderr side complies, verified differentially: with stderr closed, `mcu --url http://127.0.0.1:1 status` still exits 3, `mcu bogus` still exits 1, `mcu --json bogus` with both streams closed still exits 1, and `mcu ... 2>&1 | head -1` exits 3.
stdout side **violates**: C1 - `out_json`/`emit_stream` guard only `BrokenPipeError`, so a full or otherwise unwritable stdout escapes and the shutdown flush turns the mapped code into 120.

### Class 44 - a relative bound re-evaluated on every page

6 paged or repeated-request walks in scope: `_fetch_lines`, `_iter_pages_asc`, `_decode_pages`, `_make_decoder`, `_clock_bounds`, `_dump_follow`.
All comply. `lines` and `log export` convert `--last-ms` to one absolute `since_ts` before the first request (`_absolute_window`, `cli.py:532-542`) and never pass `last_ms` on; `_fetch_lines` walks `id_to` downwards and `_iter_pages_asc` walks `since_id` upwards, both carrying every filter unchanged; `_clock_bounds` resolves `--to` once; `_make_decoder`/`_decode_pages` bound their priming by explicit ids; `can dump -f` builds its poll params without `last_ms` and re-primes only on a capture-token change.
Grep for `last_ms` in a loop body across `cli.py` and `webui/api.js`: no hit.

---

## The two questions

**1. Least confident claim, and how I rechecked it.**
C2's mechanism - that exit 130 comes from typer rather than from an unhandled `KeyboardInterrupt`. My first three attempts measured nothing at all: a background job of a non-interactive shell inherits `SIGINT` set to `SIG_IGN`, CPython preserves an inherited `SIG_IGN`, and `trap - INT` restores the shell's *entry* disposition, which is the same `SIG_IGN` - so `mcu wait` read as "ignores Ctrl-C for 20 s", which is a different (and wrong) finding. I re-ran with `SIGINT` explicitly reset to `SIG_DFL` in a `preexec_fn`, then confirmed the code path two ways: an in-process probe showed `console_entry()` *returning* 130 (so nothing was propagating to the interpreter), and `grep` found `raise _click.exceptions.Exit(130)` at `typer/core.py:204`, which the dispatcher's `EXIT_EXCEPTIONS` arm returns verbatim. `tests/test_review_r2_cli.py:180` documents the same conversion, which is the corroboration I trust most.

Second on that list: mid-review, an external cleaner emptied `/tmp/mcurev` under the running daemon, after which every query answered "no such table: lines". I re-checked before filing anything: `capture.db` was 0 bytes with the config and scripts gone from the same directory, and `/status` still reported `db_content_bytes: 30732288` from the deleted inode. Environment, not a purge defect - so nothing was filed, and the affected runs were repeated against a daemon in `~/.cache/mcurev`.

**2. What should have been checked that nobody asked for.**
The crash backstop itself. C1's failure mode ends in `_stdio.console_entry` writing `~/.local/share/mcuscope/mcu-crash.log`, and that path is **not keyed** for `mcu` (`_report_key` is only set by the daemon, `_stdio.py:300-303`), so every concurrent `mcu` process in a multi-agent session overwrites the same file - the exact defect `set_report_key` was added to fix for the daemon, left open for the client. It is also the one artifact of this whole review that landed in a real user directory. Worth a round of its own: which of the two arguments in that docstring ("foreground programs whose failures are visible on the console anyway") still holds when the caller is an agent with a closed stdout.
Second: nothing in the suite drives the CLI with a *slow* daemon. Every timeout path here was exercised against a daemon that answers in milliseconds, so the client-side HTTP timeouts (`timeout/1000 + 5` on `/cmd` and `/wait`, `+30` on `/assert`, the 10 s connect on `can dump -f`) were never observed firing on their own; a `--timeout 300000` `wait` against a wedged daemon is a 305 s untested path that decides between exit 2 and exit 3.
