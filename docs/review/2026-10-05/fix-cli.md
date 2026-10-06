# Fix batch: cli (2026-10-05)

Files: `host/mcuscope/cli.py` (with `AI_GUIDE`), `cli_output.py`, `cli_client.py`, `cli_daemonctl.py`, `render.py`, `docs/CLAUDE_SNIPPET.md`, `README.md`, `host/README.md`, SPEC 3.5 (one paragraph) and 4. `config.py` untouched: `load_config` already serves OP-6.
Revert-verification: `~/tt-data/mcuscope-2026-10-05/fix-cli/mut/mutate.py`, 70 mutations, all caught (`results-run1.txt` plus `results.txt` for the three re-runs below); every file compared equal to its snapshot afterwards.
Behaviour driven against an isolated `mcuscoped --sim` on 18900 (and a `daemon start` on 18902), both stopped by PID.

## Findings

**SEC-5** `cli_output.py:58` `visible()`: a line whose last kept SGR is not a reset gets `ESC[0m` before its LF, or at the end of the write.
- Test: `test_cli_output_sgr_reset.py` (9).
- Mutations `sgr-reset`, `sgr-no-double` caught.

**RES-5** `cli_output.py:219` `AtomicOut`: a temp file beside the file `-o` resolves to, `os.replace`d on commit only.
- A device or FIFO (`/dev/null`) is written in place; a replaced file keeps its mode, a new one gets the umask's.
- Every `-o` routes through it: `_OutFile` (`cli.py:1901`; log, plot and can exports) and `Client.download` (`cli_client.py:341`). `remove_partial` is deleted.
- Tests: `test_cli_output_atomic_export.py` asserts the target still holds its old bytes mid-stream (the structural stand-in for a signal), for all six export paths. Also `/dev/null`, modes, and the error naming the path typed.
- Mutations `atomic-*` (6) caught.

**AGENTUX-4 (client)** `cli.py:1490`: `status: send_failed` exits 1 and prints `--send '<cmd>' failed: <ERR or no response>; nothing was waited for`, with no line on stdout.
- The old "ERR, then still a match/timeout" branches are gone; the daemon no longer answers that shape.
- Tests: `test_cli_verdict_outcomes.py::test_a_refused_send_ends_the_wait_at_once` (live stack) and `test_cli_send_verdicts.py`.
- Mutation `wait-sendfail` caught.

**AGENTUX-6 / OP-10**
- `cli_output.py:124` and `:139`: `die(msg, code, kind)`. The JSON object is `{"error", "kind", "exit_code"}`, with `error: ` stripped.
- `kind` defaults: `unreachable` for exit 3, `usage` otherwise.
- `Client.fail` (`cli_client.py:298`) classifies the daemon's text. A residual 400/413/422 is `usage`; anything else is `daemon_error`.
- A 422's field names are mapped to flags by `cli_field_names` (`cli_client.py:160`): `timeout_ms:` becomes `--timeout:`, `forbid.16:` becomes `--forbid #17:`.
- Explicit kinds at the regex, session and daemon-side `die` sites (cli.py, cli_daemonctl.py), the WebSocket 1008 reason, and the dispatcher's arms.
- Test: `test_cli_output_error_kind.py` (21).
- Mutations `err-*`, `kind-*`, `fail-*`, `field-index`, `ws-kind`, `dispatch-kind`, `usage-kind`, `malformed-kind`, `sess-kind` caught.
- The kind tags at the other daemon-side sites (daemon start/stop failures, follow give-ups) are constants that no test pins one by one.

**AGENTUX-7**
- `cli_output.py:738`: a `cmd` timeout reads `timeout: no response to 'ping' on port b within 250 ms; raise --timeout, or check the port with 'mcu status'`.
- `cli.py:1514` `_down_state`: a `wait` timeout on a port that is not connected appends `; port b2 is disconnected (open_failed)`. This applies to the `-p` port, or to the only attached port.
- `cli.py:2326`: `can dump -f` says `warning: daemon unreachable at URL: ...; retrying for 30s` on its first failed connect. The episode closes with `warning: N consecutive polls failed`; a malformed answer keeps `skipping bad update`.
- The server's `timeout_ms` wording was the server batch's.
- Tests: `test_cli_messages.py` and `test_cli_verdict_outcomes.py`.
- Mutations caught.
- The `held` clause first written in `_down_state` was redundant (a held port reads `connected: false`), and no mutation could catch it. It is deleted.

**AGENTUX-8**
- Guide: `ping` prints `monitor 1 <board>`. The mark/wait example is now `--chan marker`. `--last-ms` on an ended `--session` is documented. Sessions are described as an auto session at start and after a named stop, with non-unique names (newest wins; an id reaches any). Purge's non-tty refusal comes before any count. The `--session` example says it takes no `--timeout`.
- Snippet: the live-window line no longer reads as `--session --timeout`.
- `-p` against `/ports` (attached or stored): `cli.py:175`, `:179`. It applies to `status`, `ports`, `devices`, `plotjuggler`/`pj`, `attach`, `detach`, `session`, `purge`. `ai-guide` and `config` ignore it, `daemon` is not checked, and every other command already sends `-p` to the daemon.
- Test: `test_cli_port_scope.py`.
- Every touched example was run on the sim, with exit codes as claimed.

**AGENTUX-9**
- `purge -y --json` with nothing to delete prints `dry_run: false` (`cli.py:1864`).
- `send --json` passes through the daemon's `line_id`. The guide says to use it as `--since-id`; checked live.
- The JSONL truncation note is documented.
- `plot export --json` (OP-11.1, `cli.py:2651`): `rows` is a list of objects keyed by the header. A decimal cell is an int or a float, an empty cell `null`, an enum label or `name` a string. A row that does not fit its header is a `daemon_error`.
- Tests: `test_cli_messages.py`.

**AGENTUX-10**
- `session list --limit 0` prints `--limit 0 lists no sessions`.
- `session delete` prints `deleted session NAME; N lines deleted`, adding `(its lines are kept; --data deletes them)` without `--data`.
- `[-]` for daemon rows: `render.py:31`. It also changes the daemon's text export and bundles, through the shared `fmt_line`.
- A `--names` entry no sample carried is warned about on stderr after `lines`, `log export` and `tail` (not before a `tail -f` follow). See `cli_output.py:484` and `cli.py:927`.
- `EMPTY` (and the new `INCOMPLETE`) go to stdout.
- The guide lists `--limit 0` / `-n 0` per command.
- Tests: `test_cli_messages.py`, `test_cli_verdict_outcomes.py` and `test_cli_port_scope.py`.

**status / ports fields** `cli.py:207`, `:221`, `:265`
- `expired=` beside `trimmed=`.
- `CAPTURE STOPPED: <capture_error>; restart the daemon (mcu daemon restart)` replaces the writer line when a cause is known.
- `CAPTURE BLOCKED: another process has held a write lock on the capture since <time>; close it`.
- Per port, in both `status` and `ports`: `dropped=`, `rx_replaced=`, `plot_name_refused=`, each only when non-zero.
- Test: `test_cli_status_capture_health.py`.

**OP-1**: both READMEs state the single-user trust boundary, in the server batch's SPEC 3.1 wording.

**OP-6** `cli_client.py:71`, `:98`
- One precedence for every command: `--url`, `MCUSCOPE_URL`, the config's `[server]` (`MCUSCOPED_CONFIG` or the default file), then the default. `0.0.0.0` is reached on `127.0.0.1` and `::` on `[::1]`.
- `daemon start`/`restart` (`cli.py:2759`) re-resolve from their own `--config`. They pass `--host/--port` only for a flag or env address (`cli.py:2770`), so a `0.0.0.0` bind stands.
- The pid record is keyed by the bind host (`cli_daemonctl.py:34`), as the daemon claims it.
- A start whose address later commands would not find prints a note naming `--url` and `MCUSCOPED_CONFIG` (`cli.py:2822`).
- The start hint now keys on the URL's source (`cli_client.py:123`).
- An unreadable config prints a stderr warning, and the default address applies.
- Test: `test_cli_client_url_precedence.py`.
- Driven: `daemon start --config` with `0.0.0.0:18902` listened on `0.0.0.0`. `status`, `restart` and `stop` via `MCUSCOPED_CONFIG` all worked, and the pid record was `mcuscoped-0.0.0.0-18902.pid`.

**OP-7**
- Guide PITFALLS, SPEC 4 and the 3.5 paragraph: a `-p X` read returns port-`""` rows; `wait`/`assert` judge only X's.
- Driven: `-p sim lines`, `tail -f` and `log export` all showed an unscoped marker.
- Test: `test_cli_port_scope.py::test_a_p_read_returns_the_daemons_own_rows_but_a_verdict_does_not` (live stack).

**OP-8** `cli.py:943` `_port_column`
- Finished results use the stream rule: attached, stored and the result's own ports as one set.
- It asks `/ports` only when the result has rows.
- Test: `test_cli_port_scope.py`. Mutations `col-rule` and `col-skip-empty` caught.

**OP-9**
- `assert --allow-dropped` sends `allow_dropped: true` (`cli.py:1561`).
- `incomplete` exits 1 and prints `INCOMPLETE  N lines were shed unjudged, ...` to stdout.
- `send_failed` exits 1 on `wait`.
- Tests: `test_cli_verdict_outcomes.py`.
- `wait` has no `--allow-dropped`: see Not done.

## Existing tests edited

- `test_cli_contract.py`: four exact JSON error objects gain `kind` (interrupted, aborted, the KeyError arm). The 503-cap object loses its `error: ` prefix and is `daemon_error`.
- `test_cli_daemonctl.py::test_a_refusal_in_json_mode_is_the_one_json_object`: `kind: usage`.
- `test_cli.py`:
  - `stream closed by daemon` gains `kind: unreachable`.
  - `test_plot_export_json_wraps_the_csv` asserts row objects.
  - `test_tail_without_follow_makes_one_rest_fetch` counts row fetches, not the `/ports` question.
  - `test_can_dump_follow_survives_a_failed_poll` expects the unreachable wording.
- `test_cli_prompts.py`: the session delete wording.
- `test_cli_read_scope.py`:
  - The one-board case uses the attached alias `p0`, since a row from an unattached port is now a second board.
  - The `--since-id` handler answers `/ports`.
- `test_cli_send_verdicts.py`:
  - The three refused-send `wait` tests use the daemon's `send_failed` shape.
  - The unanswered send is exit 1, not 2.
  - `test_wait_whose_send_was_refused_is_1_even_on_a_match` is deleted: the daemon no longer answers a match after a refused send.
  - `EMPTY` is asserted on stdout.
- `test_cli_transport_timeouts.py`: the `cmd` timeout message.
- `test_cli_export_files.py`:
  - The `remove_partial` import becomes `AtomicOut`.
  - The newline spies look at the temp file.
  - The dying-stream tests assert the target keeps its old bytes and no temp is left.
  - The FIFO `remove_partial` test is replaced by a `/dev/null` symlink test.

New files:
- `test_cli_output_sgr_reset.py`
- `test_cli_output_error_kind.py`
- `test_cli_output_atomic_export.py`
- `test_cli_client_url_precedence.py`
- `test_cli_verdict_outcomes.py`
- `test_cli_status_capture_health.py`
- `test_cli_port_scope.py`
- `test_cli_messages.py`

## SPEC edits

- 3.5: a paragraph saying port `''` rows come back on a `port=` read, while `/wait` and `/assert` judge only the named port.
- 4:
  - The URL precedence, what `daemon start` passes, and the pid key.
  - The `[port]` rule for finished results and `[-]`.
  - `-p X` reads include daemon rows.
  - Where an unknown `-p` is refused.
  - The JSON error object and the `kind` vocabulary.
  - SGR reset.
  - The first-failed-poll line of `can dump -f`.
  - Atomic `-o`.
  - The `--names` warning.
  - JSONL truncation and the error object.
  - Table rows for status, ports, cmd, send, wait, assert, session list/delete, purge, plot.

## Changelog

- `mcu` finds the daemon at `--url`, `MCUSCOPE_URL`, then the config's `[server]` address (a `0.0.0.0` bind reached on `127.0.0.1`); `mcu daemon start` binds the config's address instead of always `127.0.0.1:8558`.
- `--json` errors carry `kind` (`ambiguous_port`, `no_such_port`, `port_disconnected`, `no_such_session`, `bad_regex`, `usage`, `unreachable`, `daemon_error`), and `error` no longer starts with `error: `.
- `mcu wait --send` ends at once with `send_failed`, exit 1, when the command is refused or unanswered (an unanswered one was exit 2 after the full timeout).
- `mcu assert --allow-dropped`; a window that lost lines is `INCOMPLETE`, exit 1.
- Exports with `-o` appear whole or not at all; a failed or killed export leaves the previous file in place.
- Text reads tag `[port]` whenever more than one board is attached or stored, whatever the result holds; daemon rows show `[-]`.
- `mcu status`/`ports` show `expired=`, `CAPTURE STOPPED: <cause>`, `CAPTURE BLOCKED`, `rx_replaced=`, `plot_name_refused=`.
- `mcu plot export --json` gives `rows` as objects instead of a CSV string.
- An unknown `-p` is refused by `status`, `session`, `purge` and the other commands that ignored it.
- On a terminal, a device's colour codes are reset at the end of each row.
- Clearer messages: `cmd` timeouts, `wait` on a disconnected port, `can dump -f` against a stopped daemon, `session list --limit 0`, `session delete`, `--names` that match nothing; `EMPTY` goes to stdout.
- READMEs: the daemon is for a single-user machine.

## Not done

- **OP-9 on `wait`**: one-line question. Recommended: no `--allow-dropped` on `wait`, and the guide says to retry a timeout whose `dropped` is non-zero (the stderr warning already says so).
  - The brief names `--allow-dropped` on assert *and* wait, but `/wait` has no `allow_dropped` and no `incomplete` status, and the server batch handed over assert only.
  - A client-only flag on `wait` would need the CLI to invent `incomplete` for a timeout with `dropped > 0`. That changes exit 2 to 1 with no daemon field behind it.
- `conftest.isolate_user_dirs` clears `MCUSCOPE_*_DIR` but not `MCUSCOPED_CONFIG`. A developer who exports it now points every in-process `cli.main` without `--url` at that config's address. Suggested: `monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)` there.
- webui (SPEC 9.2): unchanged by me. `[-]` changes the daemon's text export, but no JS test pins `[]`.

## Doubts

- Least sure: the `kind` boundary between `usage` and `daemon_error` for daemon 4xx.
  - Today a 400 the classifier does not recognise is `usage` ("you asked wrongly"). That includes `port b is not connected` before the classifier saw it, and any future daemon 400 that is really a daemon state.
  - A new daemon refusal needs a classifier line, or it reads as usage.
- OP-6 consequence: a daemon started with `daemon start --config X` on a non-default port is not found by later plain commands unless `MCUSCOPED_CONFIG=X` or `--url` is set. The start prints that note. Before, `start` ignored X's address altogether.
- Not checked: Windows (`AtomicOut`'s `os.replace` onto a file another process holds open fails, which is exit 1 `cannot write`; the chmod of a replaced file is a no-op there). Also IPv6 `[::1]` against a real `::` bind.
- A killed export leaves a hidden `.<name>.*.partial` beside the target; nothing sweeps it.

Scratch: `~/tt-data/mcuscope-2026-10-05/fix-cli/`
- `orig/`: pre-batch copies.
- `mut/`: `mutate.py`, results, snapshots.
- `gates/`: runner and logs.
- `run/`, `op6/`: daemon configs and captures.
- `out/`, `ws.out`, `m`: the wrapper.

All daemons are stopped.
