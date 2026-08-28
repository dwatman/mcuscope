# Review round 2, CLI + config leg

HEAD checked: `fd76735 POST /ports held to the config-write bar` (matches the expected fd76735).

Modules read end to end: `host/mcuscope/cli.py`, `cli_argv.py`, `cli_client.py`, `cli_daemonctl.py`, `cli_output.py`, `daemon.py`, `config.py`, `lockfile.py`, `pidfile.py`, `update_check.py`.
Read-only: nothing in the repo was edited. Probes live in `/tmp/claude-1000/`.

Counts: 1 HIGH, 5 MED, 8 LOW.

---

## F1 (HIGH, CONFIRMED) `mcu daemon start` can report success with a dead pid and overwrite the live daemon's pid record

- File: `host/mcuscope/cli.py:1397-1456` (spawn, pid write at 1420-1429, success report at 1451-1456).
- Invariant broken: pidfile.py's module docstring states the rule the whole module exists for, that a record naming a **live** process is never overwritten, because "overwriting let the loser of the bind race take the winner's record". `pidfile.claim()` honours that with `O_EXCL` plus a liveness re-check. `mcu daemon start` bypasses it entirely: it writes `proc.pid` to the same path with `open` + `replace_atomic`, with no read, no liveness check and no comparison against what is there.
- Second invariant broken: `daemon start` judges success by "something mcuscoped answers at this URL", never by "the child I spawned is the thing answering".

Probe (real daemons, isolated `XDG_DATA_HOME=/tmp/claude-1000/xdg/d`, port 8791, two concurrent invocations):

```
--- s1
started mcuscoped (pid 2718364)
--- s2
started mcuscoped (pid 2718365)
--- live procs
2718364 .../python3 -m mcuscope.daemon --host 127.0.0.1 --port 8791
GET /status -> "pid": 2718364
recorded in mcuscoped-127.0.0.1-8791.pid: 2718365     <-- dead
```

Both invocations printed success; only one daemon exists. The loser's child died on `daemon._port_conflict`, and the loser's parent then replaced the winner's correct record (2718364, written by `pidfile.claim`) with its dead child's pid.

Failure scenarios:
- `pid=$(mcu --json daemon start | jq .pid)` yields a dead pid; a later `kill $pid` hits nothing, or an unrelated process once the pid is recycled. `daemon start` in `--json` mode emits `{"ok": true, "pid": <dead>}` with no way to tell.
- Worse variant on the same path: if the loser's readiness wait had failed instead of seeing the winner's `/status`, `_abandon_daemon` -> `_remove_pid_record(pid_path, proc.pid)` matches the record it just wrote and **deletes** it, leaving the live daemon with no record at all.
- Also reachable without a race: any live daemon whose `/status` does not answer within the 1.0 s probe at `cli.py:1397` (large capture, loaded host) makes `daemon start` spawn a doomed second daemon and clobber the first's record.

`mcu daemon stop` currently recovers because `_serving_pid` prefers `/status`'s `pid` over the record, which is why this is not catastrophic; but the record is the documented locator and it is now wrong.

Suggested shape of the fix (not applied): write the record through the same guarded path as `pidfile.claim` (refuse when the existing record names a live process), and treat "the URL answered but the spawned child is dead" as failure rather than success.

## F2 (MED, CONFIRMED) `--json` prints nothing on three of the dispatcher's error arms

- File: `host/mcuscope/cli.py:1712-1714` (Abort), `1728-1730` (KeyboardInterrupt), `1731-1742` (KeyError/IndexError).
- Invariant broken: SPEC 4, "With `--json`, every command prints exactly one JSON object". The usage-error arm at 1705-1711 was explicitly fixed for this and emits `{"error", "exit_code"}`; the three arms beside it were not.

Probe (`/tmp/claude-1000/probe_a2.py`, mocked transport answering `{"ok": true}`):

```
['--json', 'purge', '--all', '-y']        rc=1 STDOUT='' STDERR="unexpected response from daemon: 'deleted'"
['--json', 'plotjuggler', 'on', '--save'] rc=1 STDOUT='' STDERR="unexpected response from daemon: 'enabled'"
```

Failure scenario: an agent runs `mcu --json purge ...` against a version-skewed or proxied daemon, gets exit 1 and an empty stdout, and cannot distinguish it from a command that produced no output. This is exactly the case the KeyError arm was added to convert from a traceback into a contract answer, and it stopped one step short.

## F3 (MED, CONFIRMED) `_pid_file()` can raise OSError outside every guard, crashing `mcu daemon stop` and orphaning a daemon on `start`

- Files: `host/mcuscope/cli.py:1420` (called after `subprocess.Popen` at 1419) and `cli.py:1463`; `cli_daemonctl.py:32-36`; `pidfile.py:59-64` (`os.makedirs(data_dir, exist_ok=True)`).
- Invariant broken: SPEC 4's exit-code contract. `pidfile.claim()` wraps the same call in `try/except OSError`; the two CLI call sites do not.

Probe (`/tmp/claude-1000/probe_c.py`, `XDG_DATA_HOME` pointing at a regular file):

```
ESCAPED NotADirectoryError [Errno 20] Not a directory: '/tmp/claude-1000/dh_file/mcuscope'
STDOUT='' STDERR=''
```

The exception escapes `_dispatch` (its `OSError` arm re-raises anything that is not a `BrokenPipeError`, `cli.py:1724-1725`) and lands in `_stdio.console_entry`, which writes a crash log and re-raises: a traceback, not an exit code, and nothing at all on `--json` stdout.

Failure scenarios: a read-only or full home, an `XDG_DATA_HOME` that is a file or a dangling symlink, a data dir owned by another user. On `daemon start` the throw happens **after** the child is spawned, so the daemon is left running with no record written and the user sees a traceback.

## F4 (MED, CONFIRMED) `load_config` does not catch OSError, so an unreadable config file crashes the daemon with a traceback

- File: `host/mcuscope/config.py:128-141`. `cfg_path.exists()` then `cfg_path.read_text(...)`, with `except tomllib.TOMLDecodeError` and `except (TypeError, ValueError, AttributeError)` only.
- Invariant broken: SPEC 3.3's loader contract ("the daemon refuses to start, naming the file and the key") and the docstring intent that a config problem is a startup failure, not a crash. The write path is asymmetric: `_read_doc` at `config.py:355` catches bare `Exception` and converts to `ConfigError`.

Probes:

```
load_config('/tmp/claude-1000/cfgdir')      -> UNCAUGHT IsADirectoryError [Errno 21]
load_config('/tmp/claude-1000/noread.toml') -> UNCAUGHT PermissionError [Errno 13]

$ mcuscoped --config /tmp/claude-1000/noread.toml --port 8792
...
PermissionError: [Errno 13] Permission denied: '/tmp/claude-1000/noread.toml'
rc=1
```

Failure scenarios: a config file owned by root (a shared bench), a `MCUSCOPED_CONFIG` pointing at a directory, and the TOCTOU between `exists()` and `read_text()` when the config is being replaced by an editor or by `_write_doc`'s `os.replace` from a second daemon. The exit code happens to be 1 either way, so the cost is entirely diagnostic: the message names the traceback frame instead of the file.

## F5 (MED, CONFIRMED) A corrupt `.lock` holder record turns "capture in use" into a traceback

- File: `host/mcuscope/lockfile.py:69-87`. `_describe()` accepts any `int`/`float` for `started` and hands it straight to `time.localtime`.
- Invariant broken: the module's own promise that a second daemon "reports who holds the lock instead of just that it could not get it". The metadata is untrusted: it lives in a hand-editable file beside the capture and is parsed by `_read_holder`, which validates only that the JSON decodes.

Probe (`/tmp/claude-1000/probe_b.py`):

```
1e+300  -> RAISED OverflowError timestamp out of range for platform time_t
-1e+300 -> RAISED OverflowError timestamp out of range for platform time_t
True    -> 'capture database is already in use by another mcuscoped: /tm'
```

Failure scenario: the raise happens while constructing `LockError` inside `CaptureLock.acquire`'s `except OSError` block, so `daemon.main`'s `except LockError` (`daemon.py:279`) never sees it and neither does the `except OSError` beside it. The startup message documented in SPEC 3.2, including the `--ignore-capture-lock` hint, is replaced by an `OverflowError` traceback. `time.localtime` on a large-but-in-range value can also raise `ValueError` on some platforms, same path.

## F6 (MED, CONFIRMED by code) `mcu purge --before-days` accepts a negative and silently means "delete everything"

- File: `host/mcuscope/cli.py:863-865` (option, `callback=finite_option`) and `cli.py:887` (`body["before_ts"] = time.time() - before_days * 86400`).
- Invariant broken: none stated, which is the point. `finite_option` was added to reject `nan`/`inf` on this very option, so the sign was considered and left open.
- Failure scenario: `mcu purge --before-days -1 -y` computes a `before_ts` one day in the **future** and deletes the entire capture, under a flag whose whole grammar reads as "older than N days". With `-y` the preview is computed but never shown to a human, and the only feedback is "deleted N lines" after the fact. A typo of `-1` for `1`, or a shell variable that expanded to a negative, is enough. `--all` exists for this and is deliberately explicit; this is a second, unlabelled route to it.

## F7 (LOW, SUSPECTED) `_stop_running_daemon` removes the pid record with a bare `os.remove`

- File: `host/mcuscope/cli_daemonctl.py:169-171` and `177-179`.
- `_remove_pid_record(path, pid)` exists a few lines above (`:87-101`) with a docstring stating exactly why a record must only be removed while it still names the pid being acted on: "removing that one leaves a live daemon with nothing addressing it". Both removals in `_stop_running_daemon` skip it.
- Failure scenario: daemon A is stopped, A's own `pidfile.release` removes the record, daemon B starts and claims the same host:port, and `mcu daemon stop`'s trailing cleanup then deletes B's fresh record. The user also sees the misleading "a process is still answering at ... after stopping pid N" refusal, which is B answering, not A. Narrow window, but the guard that closes it is already written and already imported in this file.

## F8 (LOW, CONFIRMED) Hoisting misses `--json` when a subcommand option's value itself looks like an option

- File: `host/mcuscope/cli_argv.py:107`. The guard tests `argv[i-1] in value_opts`, i.e. the literal previous token, not whether that token was consumed as a value.

Probe (`/tmp/claude-1000/probe_d.py`):

```
['lines','--match','--limit','--json'] -> ['lines','--match','--limit','--json']   (not hoisted)
['lines','--match','-p','--json']      -> ['--json','lines','--match','-p']        (correct)
```

Failure scenario: `mcu lines --match --limit --json` (regex `--limit`) leaves `--json` after the subcommand, click rejects it, and because `wants_json(head)` is false the usage-error arm emits no JSON object either, so a `--json` consumer gets exit 1 and empty stdout. Degradation only, but it compounds F2.

## F9 (LOW, CONFIRMED) Any token starting with `-p` and longer than two characters is hoisted as the global port option

- File: `host/mcuscope/cli_argv.py:129`.

Probe: `['send','-pulse'] -> ['-pulse','send']`, so `--port` becomes `ulse` and `send` reports a missing `TEXT` argument.

Failure scenario: a raw line or marker text beginning with `-p` (`mcu send -pulse`, `mcu mark -progress`). Click would reject these too, but the hoisting turns "no such option `-p...`" into a wrong-port setting plus a missing-argument error, which points the user at the wrong place. Only a positional in the first position of the token stream is affected; a value guarded by `value_opts` is safe.

## F10 (LOW, SUSPECTED) Fixed `.tmp` sibling names in the two atomic writers

- Files: `host/mcuscope/update_check.py:203` (`update.json.tmp`) and `config.py:383` (`<config>.toml.tmp`).
- Failure scenario: two daemons for the same user share `user_cache_dir`, so both write the same `update.json.tmp`; one `replace_atomic` then acts on the other's bytes or fails with `FileNotFoundError` (swallowed as a debug line). For the config, two daemons pointed at one `--config` file additionally lose updates outright: the read-modify-write in `save_*` has no cross-process interlock, so the second save overwrites the first's section. SPEC 3.3.1 promises hand edits made while the daemon runs survive; it says nothing about a second daemon, and the normal setup gives each its own file, which is why this is LOW rather than higher.

## F11 (LOW) `mcuscoped` startup failures print to stdout, not stderr

- File: `host/mcuscope/daemon.py:270, 281, 283-288, 293, 306`.
- `mcuscoped --port 99999 >/dev/null` (or any redirect of stdout in a wrapper script or unit file) discards every refusal message, including the capture-lock and port-conflict ones, leaving only a bare exit 1. The windowless-Windows rationale that motivates `print` is served just as well by stderr, which `_stdio` repairs alongside stdout.

## F12 (LOW) `mcu session delete` resolves names against a 1000-row page

- File: `host/mcuscope/cli.py:840` (`/sessions?limit=1000`), then a linear scan.
- With `auto_session = true` a session is created per daemon run, so a bench that restarts often crosses 1000 in normal use; an existing older session then reports "no such session: NAME" and exits 1, which reads as "already deleted".

## F13 (LOW, SPEC vs code) SPEC 4 lists `--token T` as an option of `mcu daemon start`

- SPEC `docs/SPEC.md:935` writes `mcu daemon start [--config FILE] [--sim] [--timeout S] [--token T]`.
- `daemon_start` (`cli.py:1370-1383`) has no `--token` parameter; the token comes from the global option, and `mcu daemon start --token X` works only because `cli_argv` hoists it (probe: `['daemon','start','--token','X'] -> ['--token','X','daemon','start']`).
- My read: the code is right. One token option that both forwards to the daemon and authenticates this CLI's own requests is the better design, and `daemon_start`'s docstring says so. SPEC's table should name it as the global rather than as a subcommand flag.

## F14 (LOW) Two smaller things worth a line each

- `cli_daemonctl.py:54`: `DAEMON_START_TIMEOUT_S` is evaluated at import, so `MCUSCOPE_START_TIMEOUT` is fixed for the process. Harmless for a one-shot CLI; it does mean the value cannot be varied between two `main()` calls in one interpreter, which is how the tests drive it.
- `daemon.py:88`: `if args.host:` means `--host ""` is silently ignored rather than treated as a wildcard bind or refused, unlike `--port`, which uses `is not None` precisely so `--port 0` is refused. Same flag pair, two rules.
- `daemon.py:251-261`: `_release_pid_on_terminating_signal` calls `signal.signal`, which raises `ValueError` when `main()` is not on the main thread. In-tree callers are fine; an embedder is not, and the exception lands between `pidfile.claim` and the `try` that owns the release.

---

## Checked and found sound

Recorded so a later round does not re-walk them:

- `update_check.py` against SPEC 3.6: `env_override` treats unrecognised values as a veto and empty as unset; `resolve_enabled` is applied both in `__init__` and in `set_enabled`, so the environment wins over the config file in both directions and at runtime; `status()` reports `null` while disabled even with a warm cache; a non-finite or future `checked_at` is rejected or clamped; a non-plain release records `checked_at` and reports `latest: null`.
- `pidfile.claim`'s `O_EXCL` plus settle-window plus re-read-before-remove sequence, and the residual Windows window it documents rather than claims to have closed.
- `read_pid_record`'s decimal-token grammar and `1..PID_MAX` bound, and `pid_running`'s Windows handle-wait (not `GetExitCodeProcess`) plus the Linux zombie check.
- `lockfile`'s byte-0 lock with metadata from byte 1, so a Windows reader can still name the holder. Only the timestamp formatting is unsafe (F5).
- `config._as_int` / `_as_bool` / `_as_str` / `_as_cap` / `_check_shape` against SPEC 3.3's value rules, including strict-vs-lenient per-port handling, the alias grammar, the device-nulled-before-the-guard ordering and the 1 MiB cap floor shared with the write path.
- Newline discipline on every text write: `_write_doc` `newline=""`, `log export` `newline="\n"`, `plot export` `newline=""`, the pid write `newline=""`, and `update.json` written as bytes.
- All paths go through platformdirs; no hard-coded `/etc`, `~/.config` or `%APPDATA%` anywhere in the leg.
- `cli_client._daemon_errors` as the single statement of the SPEC 4 transport mapping, and `timeout_code=1` genuinely reaching `mcu assert` through `post(**kw)`.
- Ctrl-C exit 0 on both follow loops (`_follow_ws`, `_dump_follow`) and exit 1 elsewhere, per SPEC 4.
- `download` and `plot export -o` both delete a truncated output file on failure.
- The JSONL emitter set (`log export`, `tail`, `can dump`) matches SPEC 4's enumeration; `plot export --json` correctly wraps CSV in one object.
