# Fix-diff review: cli (2026-10-05)

Scope: `git diff 5eaaeeb` of `cli.py` (with `AI_GUIDE`), `cli_output.py`, `cli_client.py`, `cli_daemonctl.py`, `render.py`, `conftest.py`, SPEC 3.5 and 4, both READMEs, `CLAUDE_SNIPPET.md`, and the eight new `test_cli_*` files. `cli_argv.py` is unchanged.
Gate: all 16 cli test files run one by one, all green (`gate.sh`, `gate.sum` in scratch).

## Findings

### FD-CLI-1 MEDIUM CONFIRMED: `can dump -f` poll refusals bypass `Client.fail`, so their `kind` and wording differ from every other command

- `cli.py:2437` `_poll_frames`: every 4xx is `kind: usage` unless the classifier knows it, and a 422 keeps the daemon's field names.
- The same refusal on the follow's first request (through `Client.fail`) says otherwise. Driven with a canned daemon whose 4th `/can/frames` answer fails:

| answer | first request | later poll |
|---|---|---|
| 401 `invalid or missing token` | `daemon_error` | `usage` |
| 409 `port b is busy` | `daemon_error` | `usage` |
| 422 `since_id: ...` | `--since-id: ...`, `usage` | `since_id: ...`, `usage` |

- SPEC 4 restricts `usage` to 400, 413 and 422. A token refusal after a daemon restart tells the agent to fix its command line.
- Untested: mutating the line to `kind` passes test_cli_output_error_kind, test_cli_contract and the can/follow tests (`mut/results.txt`, `poll-kind: MISSED`).
- Fix: `if 400 <= resp.status_code < 500: client.fail(resp)`, and a test that drives a 401 on a later poll.
- Class 54 (a wire shape built by hand at two sibling sites).

### FD-CLI-2 MEDIUM CONFIRMED: `mcu -p X purge` validates X, then purges every port

- The ruling CLI-3 check (`cli.py:167`, `_PORT_UNUSED`) makes `-p` on `purge` look supported. The guide now names purge among the commands that refuse an unknown `-p`.
- Purge never sends `-p`: `-p sim purge --all --dry-run --json` counted ids 1..19379, the whole capture with the daemon's own rows. Without `-p` it counted the same range.
- An agent that writes `mcu -p b purge --all -y` to clear one board deletes every board's capture. The behaviour predates the batch, but the batch's validation makes it more convincing.
- Fix (owner pick): refuse `-p` on `purge` with `purge spans every port; -p does not scope it`, or implement a per-port purge. `session` has the same look but deletes nothing per port.

### FD-CLI-3 LOW CONFIRMED: `AtomicOut` refuses exports that a plain open accepted, and drops file identity

`cli_output.py:219`, POSIX, each case driven with `log export --limit 3 -o`:

- A writable file in a read-only directory: `cannot write .../ro/f.txt: Permission denied`, exit 1. A plain `open(..., "w")` on the same file succeeds. `mkstemp` needs a writable directory.
- A legal 245-character file name: `[Errno 36] File name too long`. The temp name `.<name>.<random>.partial` is past NAME_MAX.
- A hard-linked target: the link is broken (link count 1, and the other name keeps the old bytes). Owner, group, ACLs and xattrs are not carried over either.
- SPEC 4 says "written whole or not at all" but names none of these.
- Fix:
  - Use a short temp name (`.mcu-XXXXXX.partial`).
  - For the read-only directory, fall back to writing in place with a stderr note, or refuse with a message that names the directory.
  - SPEC states the identity loss.
- Class 49 (its fix).

### FD-CLI-4 LOW SUSPECTED (Windows only): a replace fails over a file another process holds open, after the whole download

- `AtomicOut.commit` calls `os.replace`, which needs delete access to the target.
- A reader that opened the file without `FILE_SHARE_DELETE` blocks it: Python's own `open`, and most viewers and tailers.
- Before the batch, `open(path, "w")` worked under that reader's share-write. Now the full export lands in the temp file and is then discarded: exit 1, `cannot write`.
- The message is `exc` from `os.replace`, which names the hidden `.partial` temp as well (the `__init__` path rewrites `exc` to the typed path; commit does not).
- Second case: for a read-only target, `os.chmod(tmp, 0o444)` makes the temp read-only. The `open` then fails, and on Windows `discard`'s `os.remove` of a read-only file also fails (suppressed), so the temp is left behind.
- Confirm on the desktop:
  - `python -c "f=open('x.txt');input()"` in one shell, then `mcu log export -o x.txt` in another.
  - `attrib +r y.txt`, then `mcu log export -o y.txt`, then `dir /a`.
- Fix: map a commit `OSError` to the typed path, as `__init__` does. Clear the read-only bit before `os.remove` in `discard`, or apply the mode after writing.
- Class 13.

### FD-CLI-5 LOW CONFIRMED: `-p X <cmd> --help` asks the daemon before printing help

- `_check_port` runs in the group callback, before click parses the subcommand's `--help`.
- `mcu -p bogus status --help` gives `error: no such port: bogus`, exit 1.
- With no daemon, `mcu --url http://127.0.0.1:18999 -p x status --help` gives `daemon unreachable`, exit 3.
- Fix: call the check from each command body (one helper call in nine commands), or skip it when `ctx.resilient_parsing` is set or `--help` is among the remaining args.

### FD-CLI-6 LOW CONFIRMED: `[-]` changes the daemon's text export and bundles, and SPEC 3.4 still describes `[<port>]`

- `render.py:31` is shared with `server.py:3768` (`/lines/export?format=text`) and with the bundle's `lines.txt` (`server.py:1870`).
- SPEC 4 states `[-]`, but the export contract at `SPEC.md:836` still reads `[<port>]`, which for port `""` is `[]`.
- The rendering itself is consistent: the CLI and the daemon agree, and no alias can be `-`.
- The only test is a direct `fmt_line` call (`test_cli_port_scope.py:112`). Nothing pins the export or the bundle.
- Fix: add `(the daemon's own rows: [-])` to SPEC 3.4 line 836, and add one server export test with a port-`""` row across two ports.

### FD-CLI-7 LOW CONFIRMED: three guide and SPEC lines claim what the code does not do

- `cli.py:3268`: "--allow-empty and --allow-dropped work as in PITFALLS and wait". `wait` has no `--allow-dropped`.
- The owner's OP-9-on-wait ruling ("the guide tells agents to retry a timeout whose `dropped` is non-zero") is not in `AI_GUIDE`. Grepping it for `dropped` finds only the assert lines and the status fields.
- The guide's "An unknown -p is refused ... by every command that talks to the daemon" (SPEC 4 has the same claim without the exemption) is wrong for `daemon`: `-p bogus daemon status` exits 0.
- Fix: reword the three lines. Class 58 / N9.

### FD-CLI-8 LOW CONFIRMED: changed lines no test catches

Re-driven by mutation (`mut/mutate.py`, `mut/results.txt`):

- `cli.py` `_csv_value`, `cell.removeprefix("-")` dropped: MISSED. `-5` then comes back as `-5.0`, and no fixture has a negative integer cell. Add one.
- `cli.py:183`, the `isinstance(stored, list)` guard replaced by `or []`: MISSED. Test it or delete it.
- `_poll_frames` kind (FD-CLI-1): MISSED.
- The batch already lists the `daemon_error` constants at about 15 daemon-control and follow `die` sites as unpinned (`cli_daemonctl.py:213,350,354,358,422,430,432,446`, `cli.py:2765,2885,2905,2909,2985,3002-3014,2343,2347`). One parametrized test over the stop/start refusals would close them.
- Caught as claimed: `down-default` and `tmp-chmod`.

### FD-CLI-9 LOW SUSPECTED: `daemon start --config BAD` probes 8558 and can report "daemon already running"

- `_start_daemon` (`cli.py:2759`) re-resolves through `config_url`, which turns an unreadable config into a warning plus the default URL.
- If another daemon (the bench's real one) answers on 8558, the start exits 1 with `daemon already running` instead of the config error.
- Not driven: it needs a daemon on 8558, which the brief forbids touching.
- Fix: `start`/`restart` with an explicit `--config` refuse an unreadable one (`die(str(exc))`) rather than fall back.

### FD-CLI-10 NIT CONFIRMED: `--names` on an empty window is silent

- `lines --names zz --from 2020-01-01T00:00 --to 2020-01-01T00:01` prints nothing, exit 0.
- `_decode_pages` warns only when a decoder was built (`dec is not None`), and none is built for an empty result.
- SPEC 4 says "a --names entry no sample in a finished result carried is named". Warn when `names` was given and `dec` is None too.

### FD-CLI-11 NIT: Ctrl-C and click's abort are `kind: usage`

- `cli.py:3459,3503,3520` are pinned by test_cli_contract as `usage` ("fix the command line"), which does not fit an interrupt.
- Either document `usage` as "including an interrupt" or leave the kind out of those arms.

## Checked and fine

- All 16 cli test files pass alone: the 8 new ones plus contract, daemonctl, prompts, read_scope, send_verdicts, transport_timeouts, export_files and test_cli (`gate.sum`).
- OP-6, driven with isolated daemons on 18981/18982:
  - `daemon start --config wild.toml` (`0.0.0.0`) listened on `0.0.0.0` and printed the `--url`/`MCUSCOPED_CONFIG` note. The pid record was `mcuscoped-0.0.0.0-18981.pid`.
  - Via `MCUSCOPED_CONFIG`: `status` worked, `restart` re-bound `0.0.0.0` (`ss -ltn`) and `stop` signalled the recorded pid.
  - `--url http://127.0.0.1:18981 daemon stop` (pid key mismatch) stopped it through `/shutdown`, signalling nothing.
  - A `::` bind is reached on `[::1]`, with pid key `mcuscoped----18982.pid`, the same as the daemon's claim.
  - Reading the config costs about 24 ms per command.
- An unknown `-p` was refused (exit 1, `no_such_port` in `--json`) by lines, tail, tail -f, log export, can dump, can tx, plot channels/export, mark, wait, assert, cmd, send, break, i2c, status, session list/export, purge and devices.
- Guide examples run on the sim, all as claimed:
  - `cmd ping` prints `monitor 1 sim`.
  - `mark X; wait --chan marker --match X` exits 2.
  - `-p sim mark` works.
  - `send --json` returns `line_id`.
  - `session list --limit 0`.
  - `wait --send bogus` gives `send_failed`, exit 1, about 10 ms.
  - `assert --allow-dropped` passes.
  - A session start/stop/assert --session/delete round.
  - A non-tty purge is refused before any count.
  - `plot export --json` rows are objects (`tick_ms` int, `value` float).
- Lane names in `--names` (`led`, `pwm_en`) raise no false warning.
- Every `die()` site was enumerated by AST (`dies.py`): each `kind` is in the vocabulary, and the ones left at their default (usage for exit 1, unreachable for exit 3) fit SPEC's definitions.
- Client-side `daemon_error_kind` was checked against the daemon's actual wordings (`no such port`, `no such session`, `port X is not connected`, `port is ambiguous`, every regex and match-budget refusal).
- `visible()`: reset placement for the trailing-LF, already-reset, `ESC[m` and escaped-ESC cases (read; the batch's 9 tests and 2 mutations cover them).
- `AtomicOut` on POSIX:
  - A mid-stream target keeps its old bytes and no temp is left.
  - Modes are preserved and the umask applies.
  - `/dev/null` is written in place.
  - A dangling symlink creates its target and keeps the link.
- `_down_state`: no claim with several ports and no `-p`, or with no `connected` field (`down-default` mutation caught).
- Tree unchanged: `git diff --stat` reads 47 files and +1904/-559 before and after the mutations, the untracked set is unchanged, and every daemon was stopped by PID.

## Not covered

- Windows: FD-CLI-4, and `-o NUL` (whether `os.stat` of `\\.\NUL` reports a character device so `AtomicOut` writes in place). Neither was run.
- `restart` when the running daemon was started with flag `--host/--port` but is resolved through a config naming another port (reasoned only: it could start a second daemon).
- The SGR reset on a real terminal (only the batch's unit tests).
- `can dump -f`'s first-failed-poll warning against a daemon stopped live (canned tests only).

## The two questions

1. Least confident: FD-CLI-4. The claim that Windows readers block `os.replace` is reasoned from sharing modes, and the earlier behaviour (open-for-write succeeding under a share-write reader) is untested here. It needs the two-shell repro on the desktop before it is acted on.
2. Not thought about: commands that validate `-p` but ignore it. Validation was added as a pure refusal, but on a destructive command (purge) it reads as scoping (FD-CLI-2). The sibling to sweep is any command whose help or guide line could be read as per-port while its request carries no port.

Scratch: `~/tt-data/mcuscope-2026-10-05/fixdiff-cli/` (8.9 MB)
- `gate.sh` and `gate*.log`
- `dies.py`
- `mut/` (runner, results, `.orig` copies)
- `test_probe_kinds.py`
- `run/`, `op6/`: daemon configs, databases and data dirs
- `ro/`, `long/`, `h1`, `h2`: AtomicOut probes
