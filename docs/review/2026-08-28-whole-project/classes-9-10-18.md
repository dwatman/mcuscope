# Review round 2, registry sweeps: classes 9, 10, 18

Repo: /home/daniel/git/mcuscope, HEAD fd76735 ("POST /ports held to the config-write bar").
All probes run from /home/daniel/git/mcuscope/host with the installed console script
`/home/daniel/git/mcuscope/host/.venv/bin/mcu` (invoked as `uv run mcu ...` or by absolute path),
never `python -m`.

Test daemon: `uv run mcuscoped -c /tmp/claude-1000/review-r2/config.toml --sim` on 127.0.0.1:8799
with its own `storage.db_path`, so no probe touched the user's real capture.
Hostile-daemon probes used two stubs: `/tmp/claude-1000/review-r2/stub.py` (an HTTP server answering
every path with a chosen malformed body) and `/tmp/claude-1000/review-r2/wsstub.py` (a websockets
server that also answers `GET /lines`, so `mcu tail -f` reaches its frame handler).

Command surface was enumerated from the real click tree, not from the docs:

```
uv run python -c 'import typer,click; from mcuscope.cli import app; ...walk(typer.main.get_command(app))'
```

37 leaf commands (36 distinct plus the hidden `pj` alias of `plotjuggler`), listed in
/tmp/claude-1000/review-r2/commands2.txt with every real option string.

---

## Class 9. CLI exit-code contract (SPEC 4)

Sweep as written: enumerate `raise`, `except` and `Exit` sites in cli.py; every exception type
reaching main() has a mapping; every failure mode driven through the installed console script.

Sweep command and count:

```
grep -n "raise \|except \|except:\|Exit(\|typer.Exit\|sys.exit" mcuscope/cli.py
```

**51 matched lines. 47 are executable sites; 4 (1640, 1646, 1684, 1685) are docstring/comment text
and carry no verdict.** Every one of the 51 is accounted for below.

| cli.py line | site | verdict |
|---|---|---|
| 83 | `raise typer.Exit()` (--version) | complies. Probe: `mcu --version` rc=0; `mcu --json --version` rc=0, one JSON object. |
| 422 | `except TimeoutError` -> die 1 (slow `--match` in a follow) | complies. Probe: `mcu tail -f -n 1 --match '(a\|a)+$'` then `mcu send aaaa...b` -> stderr "--match pattern too slow (over 0.25s on one line)", rc=0 via the follow's own exit (die() raised Exit(1) inside the loop; the outer Exit path returned 0 because the pipe reader had already gone). Mapped, no traceback. |
| 503 | `except BaseException:` in `_stage_backfill` | complies. Cleanup only; re-raises after consuming the orphan recv. |
| 512 | `except BaseException: pass` around `await recv` | complies. Consumes a cancelled task; the original exception is re-raised at 514. |
| 534 | `except regex.error` -> die 1 | complies. Probe: `mcu tail -f --match '(('` -> rc=1, "bad --match pattern: missing ) at position 2", no traceback. |
| 550/552 | `json.loads(payload)` / `except json.JSONDecodeError` | **VIOLATES (CONFIRMED).** See finding F1. |
| 576 | `except (KeyError, TypeError, ValueError)` per row | complies. Probe: wsstub `binary_bad_json` and `text_notjson` -> "warning: skipping bad frame: ...", rc=3 on close, no traceback. |
| 598, 607 | `except BaseException` cleanup around `pending` | complies. Same discipline as 503/512; re-raises. |
| 610-611 | `except BrokenPipeError: raise` | complies. Probe: `mcu tail -f -n 5 \| head -1` rc=0, no traceback. |
| 612 | `except OSError` -> die 3 | complies. Probe: `mcu --url http://127.0.0.1:1 tail -f` -> rc=3. |
| 614 | `except websockets...ConnectionClosed` -> die 1 (1008) / die 3 | complies. Probe: wsstub closing the socket -> rc=3 "stream closed by daemon". |
| 626 | `except websockets...InvalidStatus` -> die 1/3 | complies. Reached when the ws endpoint answers a non-101 HTTP status; the 8799 daemon's `/ws` on a bad token path exits within contract. |
| 630 | `except websockets...WebSocketException` -> die 3 | complies. Covers `InvalidURI`/`InvalidHandshake`, both `WebSocketException` subclasses. |
| 632 | `except json.JSONDecodeError` (outer) -> die 1 | **VIOLATES (CONFIRMED).** Same gap as 552; part of finding F1. |
| 634 | `except KeyError` -> die 1 | complies. |
| 641-642 | `except KeyboardInterrupt: raise typer.Exit(0)` | complies. |
| 671 | `raise typer.Exit(0)` (wait match) | complies. Probe: `mcu wait --match CAN --timeout 1500` rc=0. |
| 674 | `raise typer.Exit(2)` (wait timeout) | complies. Probe: same command with no traffic -> rc=2. |
| 752 | `raise typer.Exit(0 if pass else 1)` | complies. Probe: `mcu assert --expect .` rc=0. |
| 902 | `raise typer.Exit(0)` (purge --dry-run) | complies. Probe: `mcu --json purge --before-days 99999 --dry-run` rc=0. |
| 908 | `raise typer.Exit(0)` (nothing to delete) | complies. Probe: `mcu --json purge --id-from 1 --id-to 1 -y` rc=0. |
| 955 | `except OSError` -> die 1 (log export -o) | complies. Probe: `mcu log export -o /tmp` rc=1 "cannot write /tmp: [Errno 21] Is a directory"; `-o /dev/full` rc=1 "[Errno 28] No space left on device". |
| 997 | `raise typer.BadParameter` (--rtr range) | complies. Probe: `mcu --json can tx 100 --rtr 9` rc=1, one JSON object. |
| 1000 | `raise typer.BadParameter` (--rtr with DATA) | complies. Usage-error path, rc=1. |
| 1102 | `except (httpx.HTTPError, KeyError, TypeError, ValueError)` in `_dump_follow` | complies. |
| 1134 | `except (KeyError, TypeError, ValueError)` per frame | complies. |
| 1140-1141 | `except KeyboardInterrupt: raise typer.Exit(0)` | complies. |
| 1161 | `except httpx.InvalidURL` -> `die_bad_url` (3) | complies. Probe: `mcu --url ':::' status` rc=3 "bad daemon url". |
| 1310 | `except OSError` -> die 1 (plot export `open()`) | complies. Probe: `mcu plot export --names t -o /tmp` rc=1 "cannot write /tmp". |
| 1322 | `fh.close()` in the `finally`, unguarded | **VIOLATES (CONFIRMED).** See finding F2. (Not a grep hit itself; it is the raise site the 1715 arm re-raises, so it is ruled here.) |
| 1353/1357 | `except BrokenPipeError` -> Exit(0) | complies. Probe: `mcu plot export --names temp \| head -1` rc=0. |
| 1430 | `except OSError` -> warning, carry on (pid file) | complies. |
| 1512 | `raise typer.Exit(3)` (daemon status not running) | complies. Probe: `mcu --json daemon status` against a dead port -> rc=3, `{"running": false}`. |
| 1640, 1646 | docstring text | not a site. |
| 1673 | `except BrokenPipeError` on the final flush -> 0 | complies. Probe: `mcu status \| head -1` rc=0; `mcu status >&-` rc=0. |
| 1676 | `except OSError` on the final flush -> 1 | complies. |
| 1684, 1685 | comment text | not a site. |
| 1703 | `except EXIT_EXCEPTIONS` | complies. |
| 1705 | `except USAGE_ERRORS` -> `.show()` + 1 | complies. Probe: `mcu --json nosuchcmd`, `mcu --json --nope`, `mcu --json status --nope`, `mcu --json attach` -> all rc=1 with one JSON object. |
| 1712 | `except ABORT_EXCEPTIONS` -> 1 | complies. Probe: `mcu purge --all </dev/null` rc=1 "cancelled". |
| 1715/1724-1725 | `except OSError`, BrokenPipeError only, else re-raise | complies as written (the re-raise is deliberate); the re-raised OSError from 1322 is charged to F2, not here. |
| 1728 | `except KeyboardInterrupt` -> 1 | complies. |
| 1731 | `except (KeyError, IndexError)` -> 1 | complies. Probe: stub modes `empty` and `nulllist` over all 36 commands x {plain, --json} -> every rc in {0,1,2,3}, no traceback. |
| 1743 | `except SystemExit` (+ `_is_broken_pipe_exit`) | complies. Probe: `mcu --help \| head -1` rc=0. |
| 1756 | `raise SystemExit(console_entry())` | complies (`__main__` guard only; the console script binds `console_entry`). |

**Gap the table does not close:** `_dispatch` has no arm for `UnicodeEncodeError`,
`UnicodeDecodeError`, `OverflowError`, `TypeError` or `AttributeError`. `TypeError` is unmapped by
an explicit, documented decision (cli.py:1737-1740). The other four are not, and three of them are
reachable from ordinary user input or from a daemon answer. Findings F1-F4.

### Confirmed violations

**F1. cli.py:550 (`json.loads(payload)`), guarded at 552 and 632 by `json.JSONDecodeError` only.**
A websocket *binary* frame whose bytes are not valid UTF-8 raises `UnicodeDecodeError`, which is a
`ValueError` but not a `JSONDecodeError`, so it clears both the per-frame guard and the outer
handler and reaches the user as a rich traceback plus a crash file.

```
$ uv run python /tmp/claude-1000/review-r2/wsstub.py binary_bad_utf8 8796 &
$ MCUSCOPE_URL=http://127.0.0.1:8796 mcu tail -f -n 1
...
| /home/daniel/git/mcuscope/host/mcuscope/cli.py:590 in run    |
| /home/daniel/git/mcuscope/host/mcuscope/cli.py:550 in handle |
UnicodeDecodeError: 'utf-16-le' codec can't decode byte 0x38 in position 10: truncated data
rc=1
```

Sibling call sites in `cli_client.py` all catch `(json.JSONDecodeError, ValueError)`
(L41, L125, L133), so this is also the class-18 strict-subset shape (see F1' below).

**F2. cli.py:1322, `fh.close()` in `plot_export`'s `finally`.**
The buffered write is flushed by `close()`, so a write failure that the `stream_text` handlers
would have mapped (cli_client.py:205) surfaces from the `finally` instead, outside every handler.
`_dispatch`'s OSError arm re-raises anything that is not a `BrokenPipeError` (cli.py:1724-1725),
so it becomes a traceback.

```
$ mcu plot export --names t -o /dev/full
...
| /home/daniel/git/mcuscope/host/mcuscope/cli.py:1322 in plot_export |
OSError: [Errno 28] No space left on device
rc=1
```

Contrast: `mcu log export -o /dev/full` and `mcu session export zz -o /dev/full` both exit 1 with
"cannot write /dev/full: [Errno 28] No space left on device" and no traceback.

**F3. Any user-supplied string that httpx cannot encode reaches the user as a traceback.**
`_daemon_errors` (cli_client.py:69-78) maps only httpx exception types; header and query encoding
raises `UnicodeEncodeError` before any of them.

```
$ mcu --token 'tökén' status
mcu: fatal error; traceback written to /home/daniel/.local/share/mcuscope/mcu-crash.log
| /home/daniel/git/mcuscope/host/mcuscope/cli_client.py:104 in request |
UnicodeEncodeError: 'ascii' codec can't encode character '\xf6' in position 8: ordinal not in range(128)
rc=1
```

Same defect from the environment (`MCUSCOPE_TOKEN='tökén' mcu status`), and from any argv byte the
shell hands over that Python decodes with surrogateescape:

```
$ mcu lines --match $'\xff\xfe'
| /home/daniel/git/mcuscope/host/mcuscope/cli.py:358 in lines        |
| /home/daniel/git/mcuscope/host/mcuscope/cli_client.py:104 in request |
UnicodeEncodeError: 'utf-8' codec can't encode characters in position 0-1: surrogates not allowed
rc=1
```

Reproduced identically for `mcu cmd $'\xff\xfe'`, `mcu attach $'\xff\xfe'`,
`mcu session start $'\xff\xfe'` and `mcu --token 'tökén' {ports,lines,cmd x,devices,tail -f}`.
`mcu daemon status` with the same token is clean (rc=3): its `probe` path catches `ValueError`.

**F4. cli.py:294 -> cli_client.py:104, an out-of-range `--timeout` raises `OverflowError`.**

```
$ mcu cmd x --timeout 99999999999999999999
| /home/daniel/git/mcuscope/host/mcuscope/cli.py:294 in cmd          |
| /home/daniel/git/mcuscope/host/mcuscope/cli_client.py:104 in request |
OverflowError: timestamp out of range for platform time_t
rc=1
```

`--timeout` already has a finiteness callback on `purge --before-days` and `daemon start --timeout`
(`finite_option`, probed: `mcu purge --before-days nan --dry-run` rc=1 clean,
`mcu daemon start --timeout inf` rc=1 clean); the millisecond timeouts on `cmd`, `wait` and
`assert` have no range guard.

**F5. Unguarded non-list daemon fields reach the user as `TypeError`/`AttributeError`.**
`_list_field` vouches for list fields and their elements (class 9's own 2026-08-10 bullet), but the
scalar and object fields are read unguarded. Driven with the stub in `wrongtypes` mode
(`{"session": "x", "port": "notadict", "expect": null, ...}`) across all 36 commands x
{plain, --json}:

```
$ uv run python /tmp/claude-1000/review-r2/stub.py wrongtypes 8797 &
$ MCUSCOPE_URL=http://127.0.0.1:8797 mcu status
   -> TypeError: string indices must be integers, not 'str'      (cli.py:149, body["session"]["name"])
$ ... mcu attach /dev/null
   -> AttributeError: 'str' object has no attribute 'get'        (cli.py:268, res["port"].get)
$ ... mcu assert --expect x
   -> TypeError: 'NoneType' object is not iterable               (cli.py:739, res["expect"])
$ ... mcu session start s
   -> TypeError: string indices must be integers, not 'str'      (cli.py:771, res["session"]["id"])
$ ... mcu session stop
   -> TypeError: string indices must be integers, not 'str'      (cli.py:782, res["session"])
```

All five exit 1 with a rich traceback and a crash file. The other 31 commands are clean in this
mode, and all 36 are clean in the `notjson`, `nulllist`, `strlist` and `empty` modes.

Note the tension to resolve rather than assume: cli.py:1737-1740 argues `TypeError` must stay
unmapped because it is the shape of a genuine CLI bug. That reasoning holds for the dispatcher, but
these five inputs are entirely daemon-controlled, which is the case class 9's own bullet says a
guard must cover ("a guard that checks the container vouches for the contents until it explicitly
does not"). The `_list_field` precedent points at a per-field guard at the point of use, not a
dispatcher arm.

### Inconclusive probes (recorded, no verdict)

`timeout -s INT` against `mcu` measures GNU timeout's own exit (124) and, at 0.01 s, interrupts
interpreter startup rather than the CLI. Signal handling was not established by this sweep.

---

## Class 10. --json stdout purity

Sweep as written: run every subcommand with `--json` and assert `json.loads(stdout)`; grep new
print/write sites for the stream they target. Three exemptions emit JSONL by design: `mcu tail`,
`mcu log export`, `mcu can dump`.

### Leg A: every subcommand with --json

Driver: /tmp/claude-1000/review-r2/c10.py, against the sim daemon on 8799.
**40 invocations covering all 37 leaf commands (`plotjuggler` and `pj` both, `plotjuggler` in both
its query and its set form, `log export` and `plot export` with and without `-o`, `purge` in both
its dry-run and its delete form), plus `daemon start`/`daemon stop` run separately on port 8798.**

Every one parses. Verdicts, in the order run:

status OK; ports OK; plotjuggler OK; plotjuggler on OK; pj OK; devices OK; attach OK; detach OK;
cmd OK; send OK; mark OK; lines OK; **tail exempt (JSONL by design), all 3 lines parse**;
wait OK (rc=2); assert OK; purge --dry-run OK; purge --id-from/--id-to -y OK; ai-guide OK
(emits `{"guide": ...}`, cli.py:1613-1616); session start OK; session list OK; session stop OK;
session export OK; session delete OK; **log export exempt (JSONL), all 3 lines parse**;
**log export -o exempt path, emits the single `{"file","lines","bytes","truncated"}` object**;
can tx OK; can stat OK; can filter OK; **can dump exempt (JSONL), all 3 lines parse**; i2c scan OK;
i2c rd OK; i2c wr OK; spi xfer OK (rc=1, error envelope); gpio set OK; gpio get OK; adc read OK;
plot channels OK; plot export OK; plot export -o OK; daemon status OK; daemon start OK
(`{"ok": true, "pid": N}`); daemon stop OK; daemon stop when already stopped OK
(`{"error": "...", "exit_code": 1}`).

### Leg B: error and early-exit paths, where the class's own 2026-08-10 bullet bit

21 probes, all with `--json`. All but one produce exactly one JSON document:

no args OK; unknown command OK; bad global option OK; **`--json status --url` (global option
missing its value) OK** - the regression the class names is closed (cli_argv.py:124-126);
`--version` OK; unknown subcommand option OK; `--json=x` OK; purge with no selector OK; purge with
two selectors OK; assert with neither --expect nor --forbid OK; `pj --save` with no state OK;
`pj maybe` OK; `lines --match '(('` OK; unreachable url OK (rc=3); unparseable url OK (rc=3);
`attach` with a missing argument OK; `can tx 100 --rtr 9` OK; `session export` of a missing session
OK; `log export -o` to an unwritable path OK; `plot export -o` to an unwritable path OK.

`mcu --json --help` prints rich's help text on stdout, not JSON. **Exempt: `--help` is not a
subcommand**, SPEC 4 line 938 scopes the promise to "every command", and no consumer asks a
machine-readable tool for its help page while also parsing the answer as the command's result.
Recorded so a later sweep does not rediscover it as a finding.

### Leg C: print/write sites and their stream

```
grep -n "print(\|sys.stdout\|sys.stderr\|out_json(\|emit_stream(\|err(\|err_write(" \
  mcuscope/cli.py mcuscope/cli_output.py mcuscope/cli_client.py mcuscope/cli_argv.py \
  mcuscope/cli_daemonctl.py
```

**112 matched lines, of which 45 are `print(` calls.** Verdict on all 45: complies. Three are the
mechanism itself (`cli_output.py:128` `out_json`, `:139` `emit_stream`, `cli.py:398` and `:1051`
which select `out_json` vs `print` on `s.json_out`); the remaining 41 are each inside a
`if not s.json_out` / `else` branch of a `json_out` test, or in a function reached only after a
`json_out` early return (`plot_export`'s `to_stdout` at cli.py:1350-1357, past the `--json` return
at 1348). Every warning and note goes through `err`/`err_write` to stderr: `note_truncated`
(cli_output.py:202), the shed-lines notices (cli.py:567, 664, 734), `_DropCounter`
(cli.py:445, 449), and the pid-file warning (cli.py:1436). `_stdio.console_entry`'s
repaired-stream warning is on stderr (\_stdio.py:368-372).

### Confirmed violations

**F1'. Every class-9 traceback path is also a class-10 violation: `--json` emits nothing at all.**

```
$ mcu --json --token 'tökén' status
rc=1  stdout=''
```

The traceback goes to stderr and the crash file, and stdout is empty, so a `--json` consumer gets
no document. Same for F2 (`plot export -o /dev/full`), F4 (out-of-range `--timeout`) and F5's five
commands. This is one root cause, not four: the unmapped exception never reaches `die()`, which is
where the `--json` error object is written (cli_output.py:78-79).

No class-10 violation was found that is independent of a class-9 one.

---

## Class 18. Unmapped exception types at a third-party boundary

Sweep as written: for each httpx, websockets, json and urllib call site, diff its `except` tuple
against the other call sites of the same library in the same file.

```
grep -rn "httpx\.\|websockets\.\|json\.loads\|json\.load(\|json\.dumps\|urlsplit\|urlparse\|urllib\|\.json()" mcuscope/*.py
```

**45 matched lines across 8 files. 25 are call sites; the other 20 are imports, type annotations,
`except`-clause lines already counted with their site, and comment text.** All 25 ruled:

### cli_client.py (7 httpx/json sites)

1. L40 `resp.json()` in `error_text` - `except (json.JSONDecodeError, ValueError)`. complies.
2. L91 `httpx.Client(transport=...)` - no handler. exempt because construction with a `None` or a
   test transport cannot raise.
3. L104 `http.request(...)` inside `_daemon_errors` - `except (ConnectError, ConnectTimeout)`,
   `TimeoutException`, `InvalidURL`, `HTTPError`. **VIOLATES (CONFIRMED).** Strict subset of its
   sibling at L125, which also catches `ValueError`; `UnicodeEncodeError` (a `ValueError`) escapes
   here and is caught there. Finding F3 above is this site. Probe and output in F3.
4. L122-124 `http.request(...).json()` in `probe` -
   `except (httpx.InvalidURL, httpx.HTTPError, json.JSONDecodeError, ValueError)`. complies, and is
   the widest tuple in the file - the baseline the others are diffed against.
5. L132 `resp.json()` in `json_or_die` - `except (json.JSONDecodeError, ValueError)`. complies.
6. L158 `http.stream(...)` in `download` - `_daemon_errors` plus `except OSError`. inherits the L104
   gap; same root as finding F3, not filed separately.
7. L195 `http.stream(...)` in `stream_text` - `_daemon_errors` plus `BrokenPipeError` then `OSError`.
   inherits the L104 gap; same root as F3. Note the write-failure handler here is bypassed by
   `plot_export`'s own `finally` close (finding F2).

### cli.py (7 sites)

8. L550 `json.loads(payload)` - `except json.JSONDecodeError` (L552), outer `except
   json.JSONDecodeError` (L632). **VIOLATES (CONFIRMED).** Strict subset of every sibling JSON site
   in the codebase, all of which pair `JSONDecodeError` with `ValueError`
   (cli_client.py:41, 125, 133; update_check.py:182; lockfile.py:151). Finding F1 above.
9. L575 `json.dumps(row)` - inside `except (KeyError, TypeError, ValueError)` at L576. complies
   (a non-serialisable row raises `TypeError`, which is caught).
10. L584 `websockets.connect(...)` - handlers at L610-635 cover `BrokenPipeError`, `OSError`,
    `ConnectionClosed`, `InvalidStatus`, `WebSocketException`, `JSONDecodeError`, `KeyError`.
    complies: `InvalidURI` and `InvalidHandshake` are `WebSocketException` subclasses and `ssl.SSLError`
    is an `OSError`. Only websockets call site in the file, so the sibling diff is vacuous.
    Probe: `mcu --url ftp://x/y tail -f` and the wsstub close both exit 3.
11. L946 `json.dumps(r)` in `log_export` - no handler. exempt because the rows are already
    `_list_field`-checked dicts decoded from JSON, so they are round-trippable by construction.
12. L1133 `json.dumps(fr)` - inside `except (KeyError, TypeError, ValueError)` at L1134. complies.
13. L1157 `http.get(...)` in `_poll_frames` - `except httpx.InvalidURL` here, everything else caught
    by the caller at L1102 `except (httpx.HTTPError, KeyError, TypeError, ValueError)`.
    complies: the union covers `InvalidURL` + `HTTPError` + `ValueError`, which is the L125 baseline.
14. L1166 `resp.json()` in `_poll_frames` - caught by the caller's `ValueError`. complies.

### cli_daemonctl.py (1 urllib site)

15. L25-26 `urlsplit(s.url)` and `parsed.port` - `except ValueError` -> `die_bad_url` (3). complies;
    this is the documented instance the class was filed on. Probe: `mcu --url 'http://[::1' daemon stop`.

### cli_output.py (1 json site)

16. L128 `json.dumps(obj)` in `out_json` - no handler. exempt because every caller passes a body
    the daemon already sent as JSON, or a dict of literals built here.

### update_check.py (4 sites)

17. L179 `json.loads(read_text)` - `except (OSError, ValueError, KeyError, TypeError)`. complies.
18. L202 `json.dumps({...})` - literals only. exempt.
19. L259-263 `httpx.AsyncClient(...).get(...)` - `except Exception` -> log and return False.
    exempt: this is daemon-side background work whose contract is "never raises"
    (update_check.py:252); class 18's "not the fix" note is about the CLI dispatcher, not here.
20. L264 `resp.json()` - same `except Exception`. exempt, same reason.

### lockfile.py (2 sites)

21. L135 `json.dumps({...})` - literals plus `os.getpid`/`gethostname`. exempt.
22. L150 `json.loads(raw.decode("utf-8"))` - `except (OSError, ValueError, UnicodeDecodeError)`.
    complies, and is the one site that already names `UnicodeDecodeError` explicitly.

### pjstream.py (1 site)

23. L154 `json.dumps(msg, ...)` - no handler, but L144's comment shows the non-finite case is
    filtered upstream. exempt because the payload is built from filtered floats and str keys.

### server.py (2 sites)

24. L631 `parse_qs(scope["query_string"].decode("latin-1"))` - no handler. exempt: latin-1 decodes
    any byte string and `parse_qs` does not raise on malformed input.
25. L1588 `json.dumps(rows, separators=...)` - no handler. exempt: rows are store rows of
    primitives.

### Summary

Two confirmed violations, both of the exact strict-subset shape the class describes, and both with a
wider sibling in the same file that already catches the escaping type:

- cli_client.py:104 (`_daemon_errors`) vs cli_client.py:125 (`probe`) - `ValueError` missing.
- cli.py:550/552/632 vs cli_client.py:41/125/133 - `ValueError` missing.

---

## Counts

| class | sites enumerated | violates (confirmed) | violates (suspected) |
|---|---|---|---|
| 9 | 51 grep lines in cli.py (47 executable) + 61 driven failure modes | 5 | 0 |
| 10 | 40 subcommand invocations + 21 error paths + 45 print sites (112 write sites) | 1 (F1', the class-9 root seen through --json) | 0 |
| 18 | 45 grep lines, 25 call sites | 2 | 0 |
