# Docs pass, 2026-09-23 round

## What changed

- `CHANGELOG.md` `[Unreleased]`:
  - A lead line: entries marked **Upgrade:** change behaviour a script may rely on.
  - Changed: 11 **Upgrade:** entries at the top (CLI-3 `empty`, CLI-10 exit 1, CLI-18 `-p`, unknown port incl. `/marker`, `--since-id`, tx rows skipped, `--send` ERR, prompts need a TTY, `send -`, CAN `x` filter, event overflow notice), then the other Changed items.
  - Added: `/assert` `cmd_result`/`reason`, `/can/frames` `port`, alias-move note, size-trim sys row, `MON_NO_*` flags, `monitor_eventf` format check, `monitor_poll()` from a handler, reload badge.
  - Fixed: CLI, daemon, link, server, store, plot and web UI entries from every fix report's Changelog section.
  - New `### Security`: Sec-Fetch-Site guard, framing headers, CSV `raw` formula guard, strict validation (marked **Upgrade:**), terminal escapes.
  - Removed three earlier Unreleased entries this round reversed: the command bar's sole-connected `auto`, CSV `raw` "carried through unchanged", and `mcu wait`-only never-answers exit 1 (now every command).
  - Merged overlaps: the `/can/frames` port (store and server), tokenizer (firmware, link, panes, chrome), non-finite value (firmware, panes), start-race logs (cli, daemon).
- `README.md`:
  - `--json` names the JSONL streams.
  - Exit codes: 1 includes a daemon that stopped answering, and 2 is a timeout the board or the wait reported.
  - New line after the agent examples: writes need `-p` when several ports are attached.
  - Verdicts: an `empty` window is exit 1, with `--allow-empty`.
- `host/README.md`: the same exit-code, `--json` and `-p` fixes, plus an `empty` sub-bullet under Agent primitives.
- `docs/CLAUDE_SNIPPET.md`:
  - Reflowed to one sentence per line.
  - Exit codes corrected.
  - A Pitfalls list: `-p` with several ports, `--send` never matches its own command, `empty` verdict, `--since-id` pages forward.
- `docs/ARCHITECTURE.md`:
  - The executor paragraph now names four pools, checked against the code: `match_executor` for history reads, `_live_scan`'s pool, `_join_pool` and `_write_pool` in `serial_link.py`, and the export pool (2 workers + 2 queued).
    It also says config writes and streamed exports still use the default executor.
  - Store: the plot summary is subtracted on delete, marked dirty only at start or when a delete takes a channel's newest sample; `query_plot_channels` is test-only; the id resync only moves up.
  - CLI: the exit-code line says 2 is a timeout the daemon reported, and a stopped-answering daemon is 1; `assert`'s 1 includes "checked no lines".
  - `probe` routing (lines 110-112) already matched the code and the `cli_client.py` docstring, so it is unchanged.
- `firmware/monitor/README.md`: no change. It already names `MON_NO_<FAMILY>`, the `families` make targets and INTEGRATION.md for the figures (updated in a49e52d).

## Verified

- Every Changelog item in fix-chrome, fix-cli, fix-daemon, fix-firmware, fix-link, fix-panes, fix-server and fix-store is folded in.
  fix-tests has none that users see.
  fix-chrome's `<U+XXXX>` note and fix-link's tokenizer note, both outside their Changelog sections, are folded in too.
- Code checks (grep and read):
  - pools: `serial_link.py:43,52`; `server.py:2344,2682-2696`; `store.py:413`;
  - streamed exports on `asyncio.to_thread`: `server.py:2294`;
  - resync: `store.py:1041-1051`;
  - summary subtraction: `store.py:1602-1637`;
  - `_daemon_errors` and `probe`: `cli_client.py:87-165`;
  - `--since-id` flow: `cli.py:620-640,909-950`;
  - `empty` verdict: `server.py:2971`, `cli.py:1469`;
  - framing headers and Sec-Fetch: `server.py:695,869`;
  - strict bodies: `server.py:208`;
  - CSV guard with `dir` exempt: `server.py:3297-3344`.
- `uv run mcu ai-guide` was read in full, and the snippet and READMEs follow it.
- No U+2013 or U+2014 in the six files (`grep -P`).
  No added bullet is longer than 200 characters (awk over `git diff -U0`).

## Not verified

- No docs build or markdown render.
  The nested list in the ARCHITECTURE executor bullet was checked by eye only.
- Whether `mcu can dump --json` passes the new `port` field through: the entry names only `/can/frames` rows.
- CLI examples were not run against a live daemon.

## Stale outside my files

- `docs/SPEC.md` 3.4 "Automatic sessions" could say a crashed run's automatic session is closed at its newest row's id and time, with no end marker (fix-store "Not done").
- `docs/REVIEW.md` class 1 could name device writes beside the reader join as work that never queues on the default executor (fix-link "Not done", for the orchestrator).
- Code, not docs, from fix-store "Not done": in `server.py`, `POST /sessions/stop` could pass `reopen_auto=`, and the lifespan's `elif open_session is not None: await store.stop_session()` branch is unreachable.
  I did not check whether the server batch applied these.
