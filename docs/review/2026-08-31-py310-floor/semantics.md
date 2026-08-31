# Python 3.10 vs 3.11+ stdlib-semantics review of `host/mcuscope`

Repo: /home/daniel/git/mcuscope @ 8028f03 (`host: support Python 3.10 - tomlkit for config reads, asyncio.TimeoutError handlers`)
Interpreters: `/tmp/mcu310/bin/python` (3.10.20) and `host/.venv/bin/python` (3.13.5, via `uv run python`).
Method: every candidate below was driven by a script run on both interpreters. No repo file was edited.

Result: **2 confirmed differences, both LOW.** The commit's own three fixes are complete: an AST sweep of every
`asyncio.wait_for` call site in the package confirms all three are guarded by `asyncio.TimeoutError`, not the builtin.

---

## Confirmed differences

### 1. LOW - `asyncio.wait_for` resolves a deadline tie in the opposite direction on 3.10

Sites: `host/mcuscope/serial_link.py:1012`, `host/mcuscope/server.py:1618`, `host/mcuscope/server.py:1793`.

When the awaited result becomes available in the *same* event-loop cycle as the timeout expires,
3.10 delivers the result and 3.11+ raises `TimeoutError` and discards the already-set result.

Script (`/tmp/probe3.py`, 300 iterations each, `call_later(0.001, ...)` against `timeout=0.001`):

```
--- 3.10.20 ---
t3 race: got=300 timeouts=0 ITEM-LOST-on-timeout=0
t4 future race: ok=300 timeout=0 RESULT-DROPPED=0
--- 3.13.5 ---
t3 race: got=0 timeouts=300 ITEM-LOST-on-timeout=0
t4 future race: ok=0 timeout=300 RESULT-DROPPED=300
```

`RESULT-DROPPED=300` on 3.13 means the future *had* its result set and `wait_for` still raised. On 3.10 that
never happened. No item is lost from an `asyncio.Queue` on either version (`ITEM-LOST=0` both), so the WS
keepalive paths at `server.py:1618`/`1793` are safe; the observable effect is confined to `serial_link.py:1012`,
where a monitor response landing exactly at the deadline is reported `status: "ok"` on 3.10 and
`status: "timeout"` (seq marked dead, response logged but not delivered) on 3.11+.

3.10 is the *lenient* side here, so this is not a 3.10 defect - it is a portability hazard for tests tuned on one
version. The one test that sits on this boundary already tolerates both outcomes
(`host/tests/test_e2e.py:176` posts `timeout_ms: 1` and branches on `if r["status"] == "timeout":` at line 184),
and `timeout_ms: 0` behaves identically on both (verified: `wait_for(q.get(), 0)` and `timeout=-1` on a
*non-empty* queue raise `TimeoutError` and leave the item queued on both interpreters).

Fix: none to the code. Add a one-line note to `docs/ARCHITECTURE.md` beside the `/cmd` timeout contract that a
boundary-exact response is version-dependent, and keep any new boundary test written the way
`test_e2e.py:176` already is (accept either outcome).

### 2. LOW - `mcuscoped --help` renders differently (argparse option-grouping changed in 3.13)

Site: the parser built by `host/mcuscope/daemon.py` `build_parser()` (any option with both a short and long form
that takes a value, and any pair of long aliases sharing a metavar).

Script: `p = daemon.build_parser(); p.format_help()` on both, diffed.

```
3.10:   -c PATH, --config PATH
                              Path to config.toml (env MCUSCOPED_CONFIG; default:
3.13:   -c, --config PATH     Path to config.toml (env MCUSCOPED_CONFIG; default:

3.10:   --plotjuggler [HOST:PORT], --pj [HOST:PORT]
3.13:   --plotjuggler, --pj [HOST:PORT]
```

`mcu-sim`'s parser is byte-identical on both (no short/long value-taking pairs). No test asserts on this text
(`test_scaffold.py:79` and `test_cli.py:411` only check exit status), and nothing in `README.md` or `docs/`
quotes it, so nothing is broken today.

Fix: none needed. If a doc or screenshot ever pastes `mcuscoped --help`, generate it on the newest supported
interpreter and do not assert on it in a test.

---

## Environment note (not a finding)

The two interpreters do not link the same SQLite: `sqlite3.sqlite_version` is **3.53.1** on the 3.10 env and
**3.47.1** on 3.13. The 3.10 leg therefore exercises a *newer* SQLite, so it does not probe the "older Python
implies older SQLite" risk that a real 3.10 user might hit. All `sqlite3` behaviour tested below was identical
anyway, but a 3.10 CI leg pinned to an older libsqlite would be a stronger check.

---

## Checked and refuted

Each was driven on both interpreters and observed identical unless noted.

**Exception aliasing**
- `socket.timeout is TimeoutError` -> `True` on **both** (unified in 3.10, not 3.11). `sim.py:707`
  (`except TimeoutError` around `srv.accept()` with `settimeout(0.5)`) is correct on 3.10; the live `accept`
  raised the builtin on both. Same for `tests/test_sim_tcp.py:43,198`, `tests/test_sim.py:511`,
  `tests/test_plotjuggler.py:138,154,172` (`sock.recv` under `settimeout`).
- `regex`'s `timeout=` raises the **builtin** `TimeoutError` on both, so `cli.py:449` and `store.py:394`
  (`except TimeoutError` around `pat.search(..., timeout=)`) are correct on 3.10. Verified by running
  `regex.compile(r"(a|a)+$").search("a"*30+"b", timeout=0.01)` on both.
- `concurrent.futures.TimeoutError is TimeoutError` is `False` on 3.10, `True` on 3.13 - but the package never
  calls `Future.result(timeout=)`, `futures.wait`, or `as_completed`. Both `ThreadPoolExecutor`s
  (`serial_link.py:42 _join_pool`, `store.py:349 _match_pool`) are only reached through
  `loop.run_in_executor(...)`, which returns an asyncio future.
- `queue.Empty` is not a `TimeoutError` subclass on either; no `queue.Queue.get(timeout=)` in the package.
- `asyncio.CancelledError` derives from `BaseException` on both (identical MRO); every
  `contextlib.suppress(asyncio.CancelledError, ...)` and `except asyncio.CancelledError` behaves the same.
- The dangerous asymmetry **`asyncio.TimeoutError` is not an `OSError` on 3.10 but is on 3.11+** was confirmed
  to exist, then refuted at every site: an AST sweep for `try` blocks that catch `OSError` *and* contain a
  timeout-capable call found 11 blocks, none containing a `wait_for`, and the only one with a real socket
  timeout (`sim.py:705`) orders `except TimeoutError` before `except OSError`. Standing rule for future work:
  never put `except OSError` around an `asyncio.wait_for` - it swallows the timeout on 3.11+ and not on 3.10.
- Handler ordering: `serial_link.py:1013` `except asyncio.TimeoutError` precedes `except BaseException`, correct
  on both (`asyncio.TimeoutError` subclasses `Exception` on 3.10, `OSError` on 3.13; neither shadows the other).

**tomlkit vs tomllib (the commit's central swap, `config.py:136` / `config.py:381`)**
- 60 hand-written edge-case documents parsed three ways (tomllib on 3.13, tomlkit on 3.10, tomlkit on 3.13):
  empty file, comments only, UTF-8 BOM, dates/local dates/times/offset datetimes/fractional seconds beyond
  microsecond precision, inline tables, arrays of tables, arrays of tables interleaved with other tables,
  sub-tables inside an AoT, dotted keys, quoted dotted keys, out-of-order sub-tables (legal TOML: `[a.b]` before
  `[a]`), duplicate keys, duplicate tables, duplicate keys in an inline table, AoT/table conflicts, underscores,
  hex/octal/binary integers, `+99`, leading zeros, over-64-bit integers, `inf`/`-inf`/`nan`/`1e400`, `-0.0`,
  multiline basic and literal strings, line-ending backslash continuations, escapes, `\uZZZZ`, unterminated
  strings, control characters, NUL bytes, CR-only line endings, CRLF, empty table names, trailing text,
  non-UTF-8-representable input, and a realistic full `config.toml`.
- **Zero** accept/reject disagreements and **zero** value disagreements. tomlkit 0.15.1 on 3.10 and on 3.13
  produced byte-identical JSON. `unwrap()` returns exact builtin types (`type(v) is int/str/bool/float/dict/list`,
  `datetime.datetime/date/time`), not tomlkit subclasses, so `config.py`'s `isinstance` gates in `_as_bool` /
  `_as_int` / `_as_str` see the same thing they saw under `tomllib`.
- Only difference: the exception class. `tomllib.TOMLDecodeError` is a `ValueError`; `tomlkit`'s errors derive
  from `TOMLKitError(Exception)` and are **not** `ValueError`. `config.py:138` catches
  `tomlkit.exceptions.TOMLKitError` explicitly and before the `except (TypeError, ValueError, AttributeError)`
  clause, so the mapping to `ConfigError(f"{path}: invalid TOML: ...")` is preserved and clause order is
  irrelevant. Identical on both interpreters. `host/tests/test_review_r2_config.py` + `test_config_api.py`:
  49 passed on 3.10.

**Enums (`protocol.py:56 LineClass(str, Enum)` with an explicit `__str__`)**
- `f"{lc}"`, `format(lc)`, `f"{lc:>10}"`, `"%s" % lc`, `"%r" % lc`, `str()`, `repr()`, `",".join([lc])`,
  `json.dumps({"k": lc})`, `json.dumps({lc: 1})`, `lc == "event"`, `hash`, `list(LineClass)`,
  `LineClass("event")`, `lc.upper()`, `sorted(LineClass, key=str)`, `bool`, `len` - all identical on both.
  The 3.11 `Enum.__format__`/`__str__` change does not bite because the class defines `__str__` returning
  `self.value`, which is also what `str.__format__` produced on 3.10. `x in LineClass` (the 3.12 `__contains__`
  change) is never used.

**asyncio semantics**
- `asyncio.run` + SIGINT to a real subprocess: `finally:` runs, `KeyboardInterrupt` surfaces at the caller,
  rc 0, empty stderr - identical (this is `cli.py:672`/`673`, the `mcu tail --follow` Ctrl-C exit contract).
- `asyncio.to_thread` still running at `asyncio.run` teardown: both block 2.01 s on
  `loop.shutdown_default_executor` and exit 0.
- `ThreadPoolExecutor` with a busy worker at interpreter exit: both wait 3.08 s, rc 0.
- `asyncio.wait(set_of_tasks, timeout=...)` -> same done/pending split. No coroutine is ever passed to
  `asyncio.wait` (removed in 3.11): `serial_link.py:380`, `store.py:558` and `cli.py:526` all pass Tasks.
- Cancelling a task that is inside `wait_for` raises `CancelledError` at the awaiter on both.
- Cancelling an `asyncio.to_thread` task raises `CancelledError` on both.
- `asyncio.Queue`/`Event`/`Lock` constructed *outside* a running loop then used inside one (and reused across a
  second `asyncio.run`) work identically - relevant to `store.py:435,442,526,916`, `server.py:366`,
  `serial_link.py:289,295,296,1096`.
- No `get_event_loop`, `TaskGroup`, `asyncio.timeout`, `asyncio.Runner`, `Task.cancelling()`/`uncancel()`,
  `loop_factory`, `ExceptionGroup`/`except*` anywhere in the package.

**sqlite3 (`store.py`)**
- The invariant stated at `store.py:372` and relied on at `store.py:1564` - "SQLite reports the raised
  TimeoutError to the caller as a generic OperationalError" - holds on **both**: a `create_function` callback
  raising `TimeoutError` (and one raising `ValueError`) surfaces as
  `sqlite3.OperationalError: user-defined function raised exception`, with `__context__` None and the callback
  invoked once. The 3.12 sqlite3 callback changes do not affect this path, so
  `MatchBudgetExceeded` still fires via `rx.timed_out` on 3.10.
- No `Connection.serialize`/`blobopen`/`setlimit`/`autocommit`/`enable_shared_cache`/`threadsafety` use.

**`re`**
- Every module-level compiled pattern in the package (11 across `config`, `protocol`, `pidfile`, `pjstream`,
  `update_check`, `server`, `serial_link`) run against a 40-string corpus (protocol frames, TOML fragments,
  device URLs, versions, control chars, 300-char lines, non-ASCII, regex metacharacters): identical spans,
  groups and exceptions on both.
- No atomic groups `(?>...)` or possessive quantifiers (3.11-only `re` syntax) anywhere; user patterns go to
  `regex`, which supports them on both.

**typing / dataclasses**
- Every package module imports cleanly on 3.10 (checked in-process, no `import_fail`). No `Self`,
  `LiteralString`, `Required`/`NotRequired`, `assert_never`, `TypeAlias`, `ParamSpec`, `typing.get_type_hints`
  at runtime. Dataclasses are plain `@dataclass` / `@dataclass(frozen=True)` - no `slots=` or `kw_only=`.
  `from __future__ import annotations` is present in every module, and the FastAPI/pydantic models in
  `server.py` validate on 3.10 (config API tests pass there).

**Other 3.11+ names and behaviours, all absent or identical**
- `mimetypes.guess_type` for `.js .mjs .css .html .json .svg .png .ico .map .webmanifest .txt .md .wasm .toml
  .csv .xhtml`: identical (the 3.12 `.js` -> `text/javascript` change predates 3.10.20's data). Relevant to the
  web UI served from `server.py`.
- `ipaddress` `is_private`/`is_global`/`is_loopback`/`is_link_local` for 16 addresses including `0.0.0.0`,
  `100.64.0.1`, `192.0.0.1`, `192.88.99.1`, `64:ff9b::1`: identical (the gh-113171 reclassification is
  backported into 3.10.20). Relevant to `server.py` and `pjstream.py` host checks.
- Not used anywhere: `tomllib`, `enum.StrEnum`, `enum.verify`, `logging.getLevelNamesMapping`,
  `contextlib.chdir`, `hashlib.file_digest`, `zipfile`/`tarfile` extraction filters, `datetime.UTC`,
  `operator.call`, `sys.exception()`, `BaseException.add_note`, `math.exp2`/`cbrt`,
  `inspect.getmembers_static`, `itertools.batched`, `asyncio.Runner`.
- `traceback.format_exc()` at `_stdio.py:343` differs only in PEP 657 `^^^` anchor lines on 3.11+ - a cosmetic
  change inside a crash log that nothing parses.
