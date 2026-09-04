# Fix-diff leg, 2026-09-04 adversarial round

HEAD `7a1120f`; the round's diff is the uncommitted working tree (tracked changes plus the
six new `host/tests/test_*.py`, three new `tests/webui_js/*.test.mjs` and this directory).

Gates re-run over the whole tree by this leg, not taken from the batch reports:

- `uv run python -m pytest -q`: **1317 passed, 1 skipped** (275 s). The one warning is
  Starlette's pre-existing httpx deprecation.
- `uv run python -m ruff check .`: clean.
- No em dash or en dash anywhere in the diff (`grep -P '\x{2014}|\x{2013}'` over the
  tracked diff and every untracked file: 0).

Thirteen findings. R1 is a data-loss regression introduced by this diff and confirmed by
driving it; R2, R3, R4, R5, R6 and R13 are the batch-boundary defects the leg was asked to
look for.

## Findings

### R1 - `mcu log export -o FILE` deletes a file it could not open (HIGH)

`host/mcuscope/cli.py:1399-1420`.

`ok = False` is set, and the `finally: os.remove(out_file)` armed, **before** the `open()`.
An `open()` that raises therefore reaches `die(...)` and then removes a file this command
never created or opened. `plot export` (cli.py:1810-1836), the sibling this fix says it
mirrors, opens first and arms the guard only afterwards.

Driven, not reasoned:

```
$ echo "PRECIOUS DATA" > keep.txt && chmod 444 keep.txt
$ mcu log export -o keep.txt
cannot write keep.txt: [Errno 13] Permission denied: 'keep.txt'   (exit 1)
$ cat keep.txt
cat: keep.txt: No such file or directory
```

The same command through `plot export` leaves the file intact. Before this diff `log
export` did too. Reachable on POSIX whenever the directory is writable and the file is not
(read-only file, a file owned by another user, an immutable-ish permission set); on Windows
`os.remove` of a read-only file raises and is suppressed, so it is a POSIX-only loss.

Minimal fix, taking plot export's shape exactly:

```python
    if out_file:
        try:
            fh = open(out_file, "w", encoding="utf-8", newline="\n")
        except OSError as exc:
            # An unwritable path is a user error, not a crash.
            die(f"cannot write {out_file}: {exc}", 1)
        ok = False
        try:
            with fh:
                for row in rows:
                    line = render(row) + "\n"
                    fh.write(line)
                    count += 1
                    size += len(line.encode("utf-8"))
            ok = True
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)
        finally:
            if not ok:
                with contextlib.suppress(OSError):
                    os.remove(out_file)
```

Registry class 49's sweep should gain the clause this instance turns on: the removal is
armed only once the open has succeeded, or the guard destroys a file the command never
wrote.

### R2 - the config loader now skips a `[[ports]]` entry SPEC says it must default (MEDIUM)

`host/mcuscope/config.py:365-377` against `docs/SPEC.md:502`.

SPEC 3.3: *"The same mistake inside a `[[ports]]` entry warns and keeps that key's default,
so one bad entry does not cost the whole file"*, with exactly one carve-out named on the
next line (a non-string `alias` skips the entry). C7 adds a second carve-out for a non-bool
`autoconnect` or `identify` and does not touch SPEC. Code and SPEC now disagree, and SPEC
wins by CLAUDE.md.

The out-of-range `baud` skip above it (config.py:355-364) contradicts SPEC:504 the same way
and is pre-existing, so the sentence has to be rewritten either way.

Failure scenario beyond the disagreement: the dialog's `PUT /config/ports` replaces the
whole array, so a port dropped at load is deleted from the file the next time anyone saves
settings. A cosmetic typo (`identify = 1`) silently loses the attachment permanently. The
`autoconnect` argument for skipping (falling back opens the port, the opposite of the
setting) does not hold for `identify`, whose safe fallback is `false`.

Minimal fix, one of:

- SPEC 3.3, replacing the two sentences at 502-503:
  `The same mistake inside a [[ports]] entry warns and keeps that key's default, except
  where the default is the dangerous answer: a non-string alias, an out-of-range baud, and
  a non-bool autoconnect or identify skip that entry instead.`
- or, for `identify` only, fall back to `False` rather than skipping (the flag is
  cosmetic and its safe direction is "do not ping").

### R3 - `--eol` with no `--send` is silently dropped by the CLI and refused by the daemon (MEDIUM)

`host/mcuscope/cli.py:1054-1057` and `:1139-1142`, against `host/mcuscope/server.py`
`_do_wait` / `_do_assert` (`eol applies to send; set send too`).

Batch A ruled that an `eol` the path never reads is a 400 rather than a setting silently
unused. The CLI does precisely the silently-unused thing: `body["eol"]` is set only inside
`if send_cmd is not None`, so `--eol` without `--send` never leaves the process.

Driven with a recording transport:

```
mcu wait   --match x --eol crlf   -> exit 2 (timeout), eol in body: False
mcu assert --expect x --eol crlf  -> exit 1 (fail),    eol in body: False
```

Minimal fix, in `wait()` and `assert_()` beside the other client-side refusals, in the
daemon's own words so both answers read alike:

```python
    if eol is not None and send_cmd is None:
        die("error: eol applies to send; set send too", 1)
```

### R4 - `connecting` is missing from the two surfaces that gloss the reasons (MEDIUM)

`host/mcuscope/cli.py:2066-2076` (AI_GUIDE) and
`host/mcuscope/webui/statusbar.js:201-206` (`DISCONNECT_WHY`).

Batch B added a fifth `disconnect_reason`, batch C owns the guide and batch D wrote the
gloss table; both lists are still the pre-batch-B four. `mcu status` prints
`disconnected (connecting)` for a port between `POST /ports` and its first resolved open
attempt, and the guide - the agent's only view of the CLI - has no entry for it, so the
agent's "what to do about each" table does not answer. The web UI falls back to the raw
token by design, which is correct but reads as a wire token in a hover that is otherwise
plain English.

Minimal fix, both sites:

- AI_GUIDE, first in the list (it is the transient one):
  `connecting    no open attempt has resolved yet; wait one retry interval`
- statusbar.js: `connecting: "opening the port for the first time",` in `DISCONNECT_WHY`.

### R5 - `mcu --version` is absent from AI_GUIDE, and the new guide test cannot see it (MEDIUM)

`host/tests/test_cli_contract.py:182-193` and `host/mcuscope/cli.py` (AI_GUIDE).

`_option_strings`'s `walk()` returns immediately once a command has subcommands, so a
group's own params are never collected: the root group's global options (`--json`, `--url`,
`--port`, `--token`, `--version`) are outside a test whose docstring claims "every
non-hidden option of every non-hidden subcommand". Run with the group params included, the
test finds one real miss:

```
MISSING <root> --version
```

`--version` is in SPEC 4 (line 999) and not in the guide, which is the CLAUDE.md rule the
test exists to enforce.

Minimal fix, in `walk()`, hoisting the params loop above the recursion:

```python
    def walk(command, path):
        for param in command.params:
            for opt in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
                if opt.startswith("-"):
                    found.append((" ".join(path) or "<root>", opt))
        for name, sub in (getattr(command, "commands", None) or {}).items():
            if not getattr(sub, "hidden", False):
                walk(sub, [*path, name])
```

plus one AI_GUIDE line, in the block that already names `--json` / `--url` / `--port` /
`--token`: `--version                       client version and interpreter (honours --json)`.

### R6 - two comments state a mechanism batch B removed in the same diff (MEDIUM)

`host/mcuscope/cli.py:457-460` and `host/tests/test_cli_contract.py:229-233`.

Both say `send_raw` strips a trailing CR/LF so a newline character would be a zero-byte
write reported as done. Batch B deleted that strip in the same working tree: a newline in
`/send` is now a 400 (`line must not contain embedded newlines`). The parametrised case
`"\x00"` was never stripped by `rstrip("\r\n")` even before, so the message was wrong for
one third of its own inputs. Per CLAUDE.md the comment should carry the constraint, not the
incident, and here the incident it carries is no longer true.

Minimal fix, both sites, same wording:

```python
        # One byte, unterminated, is the whole point of SysRq; anything the wire cannot
        # carry as a single visible character is a usage error, not a write to attempt.
```

and the test's assertion message: `"a non-printable SysRq character is bad usage, not a
write the daemon has to refuse"`.

### R7 - `monitor_mark`'s new refusal is not in the contract that declares it (MEDIUM)

`firmware/monitor/monitor.c:770-780`, against `firmware/monitor/monitor.h:100-105` and
`docs/SPEC.md:1152-1156`.

E2 made whitespace-only text a `MONITOR_ERR_BADARG`. The header comment and SPEC 5's mirror
of it both still say the refusal is for "empty or NULL" plus the tick-sigil case. Three
downstream projects vendor this header by hand and read exactly that comment; a caller
passing `"\t"` now gets a non-zero return the documentation says cannot happen (class 41's
"the contract states the obligation in the header that declares it", from the other side).

Minimal fix, identical clause in both places: `... for text that emits nothing: NULL, empty,
or only spaces and tabs, and (on a port with no tick_ms) text whose first word is itself an
"@<digits>" tick sigil.`

### R8 - `/plot/export` freezes its window only when `last_ms` is given (MEDIUM-LOW)

`host/mcuscope/server.py:1625-1640`.

`if last_ms is not None and id_to is None: id_to = store.max_id()`. With no `last_ms` (the
default: the whole capture, or a session with an open end) the count and the stream are
still two queries against a moving capture, so the count-based refusal
(`export too large`) guards a smaller set than the CSV then streams. On a port at a few
hundred lines a second an export sitting just under the cap streams past it, and the reply
is already streaming when that happens, which is the exact reason the comment above the
count gives for refusing rather than truncating.

Minimal fix: drop the `last_ms is not None` half of the condition, so every request fixes
one upper bound.

```python
        if id_to is None:
            id_to = store.max_id()
```

The `_do_assert` twin (server.py:2252-2256) has the same shape but no second query outside
the `last_ms` case, so it is correct as written.

### R9 - the burst-cap test can fail on any loop stall, not only the injected one (MEDIUM-LOW, Windows)

`host/tests/test_wait_repeat.py:352-376`, `assert min(gaps) >= 0.010`.

`_repeat_send` re-anchors with `next_at = max(next_at + period_s, loop.time())` and then
sleeps `next_at - loop.time()`. Whenever a tick overruns its period the sleep is zero and
the next write goes out immediately - one write, which is the intended re-anchor, not a
burst. The test drops only the gap caused by its own injected 200 ms stall (`starts[1:]`);
any other overrun past 20 ms produces a second near-zero gap and fails the assertion. A
20 ms event-loop stall on a loaded Windows runner (GC, the 15.6 ms timer granularity, the
other suites' threads) is ordinary, and the failure would read as a real burst.

Minimal fix, asserting the invariant the class actually states (no *backfill*, i.e. at most
one immediate write per stall) rather than a floor no scheduler guarantees:

```python
    assert sum(g < 0.010 for g in gaps) <= 1, gaps      # one re-anchor, never a run of them
    assert max(gaps) < 0.2, gaps                        # and the cadence resumed
```

### R10 - the socket break refusal is case-sensitive where the URL grammar is not (LOW)

`host/mcuscope/link.py:124`, `self._socket_drain = device.startswith("socket://")`.

`validate_device` lowercases the scheme before checking the allowlist and pyserial's
`serial_for_url` matches the protocol case-insensitively, so `SOCKET://127.0.0.1:9900` is
an accepted, working socket port for which `_socket_drain` is False. It then takes the
native drain branch (pre-existing) and, as of B1, the native break branch: `/break` answers
200 for a break that never leaves the host, which is the defect B1 exists to remove.

Minimal fix: `self._socket_drain = device.lower().startswith("socket://")`.

### R11 - `mon_can_stat`'s output contract still says nothing about `*state` (LOW)

`firmware/monitor/monitor.h:157-158`, against `monitor_cmds.c:209`.

E1 took class 41's caller-side arm (pre-set plus a NULL re-check) and left the contract
silent, so the next shim author has no way to know whether `*state` must be written. One
clause on the declaration closes it for the three vendored copies too.

Minimal fix: `const char **state);  // init; state = current, may be left untouched`.

### R12 - a changed `last_write_error` with an unchanged count leaves a stale hover (LOW)

`host/mcuscope/webui/statusbar.js:211-215` and `:270-278`.

`portsSig` gained `write_failures` but not `last_write_error`, while the new badge renders
`last_write_error` as its title. The counter normally moves with the error so the chips
repaint, but the one case that does not is a port whose failure count is capped or reset
while the message changes; the hover then names an error that is no longer the last one.
This is the same gap the file's own comment above `portsSig` was written for
("compare what the chips actually display").

Minimal fix: add `p.last_write_error || ""` to the signature array.

### R13 - close 1008 now means two things, and the one client that reads it says the wrong one (MEDIUM)

`host/mcuscope/server.py:1714` and `host/mcuscope/cli.py:997-1002`.

A8 gave 1008 a second meaning (an unattached `port` alias) without a close reason, and
`_follow_ws`'s 1008 arm - written for the Host/origin/token refusal - answers
`stream refused by daemon: not authorised`. `mcu -p typo tail -f` (cli.py:895-897 appends
`?port=` whenever `-p` or a config port is set) therefore reports an authorisation failure
for a typo'd or detached alias, sending the operator after a token that is not the problem.
SPEC 3.4's `/ws` paragraph now lists both meanings under the same code, so the ambiguity is
in the contract as well as the code.

Minimal fix, naming the refusal on the wire and letting the client print it:

```python
            await websocket.close(code=1008, reason=f"no such port: {port}")
```

```python
            if exc.rcvd is not None and exc.rcvd.code == 1008:
                die(f"stream refused by daemon: {exc.rcvd.reason or 'not authorised'}", 1)
```

(The auth closes at server.py:598 and :736 send no reason, so they keep the old wording.)

## Per-batch verdict

- **A (server/store scope, `/wait`, `/ws`, sessions):** findings R8 (its own window freeze
  covers only the `last_ms` case), R9 (its burst-cap test), R13 (1008 overloaded without a
  reason string). Everything else checked out: the `_do_wait` finally nests correctly so
  `watch.close()` cannot be skipped, `suppress(Exception, CancelledError)` covers the
  BaseException that `Exception` alone would miss, the session-stop lock plus
  act-on-the-result gives exactly one 200, and `_encode_wire` is a staticmethod so the
  pre-check call is sound.
- **B (link, serial_link, store writer):** clean. R10 is a pre-existing case-sensitivity
  that B1 inherits rather than introduces. The `send_raw` strip removal matches SPEC:679,
  which already forbade CR/LF in the body; `_writer`'s new `except Exception` sees `batch`
  bound in every path that can reach it; `_writer_exited` returns early on both the
  cancelled and the clean-sentinel exits, so `stop()` keeps sole ownership of the queue.
- **C (CLI, config):** findings R1 (data loss), R2 (SPEC conflict), R3, R5, R6. The rest is
  sound: all three `--timeout` callbacks take a one-argument wrapper, `_mapped_exit` is
  applied to both the returned and the raised exit (click with `standalone_mode=False`
  returns), `reset_output_state` is per invocation, and `_to_devnull` survives a stream with
  no `fileno`, so the new tests behave the same on Windows.
- **D (web UI):** findings R4 (half of it; the other half is C's guide) and R12.
  `collapsedMem` is correct against an empty set (a `Set` is truthy) and returns a copy;
  the seed guards are per group and per row as the live path is.
- **E (firmware):** findings R7 and R11, both contract text rather than code. The clamp is
  expressed in `MON_OK_PAYLOAD_MAX` as class 48 requires, and `monitor_mark` still emits the
  original `text` (leading whitespace intact) rather than the skipped pointer, so the host's
  own strip stays authoritative.

## SPEC versus code: every behaviour change in the diff

| Change | SPEC | Verdict |
|---|---|---|
| `socket://` break refused (`link.py`) | 683 "a transport that cannot send a break (a `socket://` link) is a 400 too" | documented; the code now conforms |
| `/send` no longer strips a trailing CR/LF | 679 "The line body itself may still not contain CR or LF" | documented; the code now conforms |
| `disconnect_reason` starts at `connecting` | 601, 616 (enum line plus the sentence below) | documented in SPEC; **missing from AI_GUIDE and the web UI gloss (R4)** |
| `SourceLink(on_break=)` and `SimEndpoint.breaks` | none | test seam, no wire surface; correctly undocumented |
| store writer death fails queued futures | none (`/status.writer_alive` already exists) | internal; no SPEC change owed |
| non-bool `autoconnect`/`identify` skips the port entry | 502-503 say warn-and-default, alias the only exception | **conflict (R2)** |
| out-of-range `baud` skips the port entry | 495, 504 say warn and fall back to the default | pre-existing conflict, same sentence (R2) |
| `PUT /config/ports` keeps a saved `eol` | 561-562 | documented |
| negative `limit` is 422 on `/lines`, `/can/frames`, `/plot/series`, `/sessions` | 704 for `/lines`; `/can/frames` inherits it explicitly at 719 | documented for the two that state a contract; `/plot/series` and `/sessions` state no limit contract either way, so nothing to correct |
| `/wait` and `/assert` refuse `eol` without `send` | 719 ("same on `/assert`") | documented; **the CLI does not implement it (R3)** |
| `/wait` `repeat_ms` validates the body before the first write | 729 | documented |
| concurrent repeats are not serialized beyond the port write lock | 730 | documented (statement of existing behaviour) |
| `/ws` closes 1008 for an unattached alias | 826-827 | documented, but 1008's two meanings are now indistinguishable to a client (R13) |
| `POST /sessions/stop` acts on the stop's result | 798 | documented |
| `/plot/export` and retrospective `/assert` freeze one window | none (REVIEW class 44 records it) | internal correctness; no SPEC change owed, and R8 is the incomplete half |
| `GET /config` `exists` off the loop; session export `mkstemp` off the loop | none | internal (class 1); no SPEC change owed |
| Ctrl-C inside a command exits 1 | 1008 "Ctrl-C anywhere else is `1`" | documented; the code now conforms |
| an unwritable stdout exits 1 instead of 120 | SPEC 4's exit table (1 = error) covers it by generality | acceptable; no new sentence needed |
| `mcu cmd`/`wait --timeout` refuse 0; `assert --min-window` bounded client-side | SPEC 4 documents the flags, not their bounds; a client-side refusal is bad usage, exit 1, which SPEC 4 states | documented by generality |
| `mcu lines/tail/can dump/session list` refuse a negative count | as above | documented by generality |
| `mcu sysrq` takes a printable character | 1016 table row | documented |
| `mcu log export -o` removes a partial file | none | undocumented, and R1 is a defect in it; SPEC 4's `log export` row would carry one clause ("a run that does not complete removes the file") |
| `mcu wait --repeat-ms` reads `sends` with `.get` | none (skew tolerance, class 46) | internal; correctly undocumented |
| AI_GUIDE gains `--raw`, `--baud`, `--wide`, `--save`, `--note`, `--sim`, `--config` | all six already in SPEC 4 | documented; `--version` is the one gap (R5) |
| `monitor_mark` refuses whitespace-only text | 1152-1155 still say "NULL or empty" | **conflict (R7)**, and `monitor.h:102` with it |
| `cmd_can_stat` tolerates a NULL `*state` | none | defensive, no wire change; the header should state the obligation (R11) |
| `emit_hex_resp` clamps to `MON_OK_PAYLOAD_MAX` | 5.x constants already govern the payload | no wire change at shipped limits |
| port chip shows `write_failures` / `last_write_error`, glossed reason | 1382 (9.1 chip line) | documented |
| `timeMode` enum, seed guards, offline line-ending render | none | internal UI behaviour; no SPEC change owed |

## The two questions

**1. What am I least confident about here, and how did I recheck it?**

R9, the claim that the burst-cap test can fail on a stall it did not inject. It is the one
finding I reasoned about rather than drove: I read the re-anchor expression
(`next_at = max(next_at + period_s, loop.time())` followed by `sleep(next_at - loop.time())`)
and concluded that any tick overrunning its period yields a zero-length sleep and therefore
a near-zero gap, which the assertion forbids. I did not reproduce it, and I have no Windows
box, so the frequency is an estimate from the platform's 15.6 ms timer granularity, not a
measurement. Everything else I flagged as a behaviour claim was driven: R1 with a
`chmod 444` file (and the `plot export` control, which kept its file), R3 with a recording
`MockTransport` that shows `eol` absent from both bodies, R5 by re-running the test's own
walker with group params included (one real miss, `--version`), and R2/R7 by reading the
SPEC and header sentences the code now contradicts. The full suite and ruff were re-run
here rather than taken from the batch reports.

**2. What should we have checked that nobody asked for?**

The clients of the refusals this round added, as opposed to the refusals themselves. Every
batch tested its new 400 or 1008 from the requester's side and none asked what the shipped
clients do when they receive it. That gap produced R13 (`mcu tail -f` reports "not
authorised" for a typo'd alias) and R3 (the CLI performs the silent drop the daemon now
refuses). The same question is still open in two places nobody has driven:

- The web UI's `PUT /config/ports` against the new `422` and `400` arms: `settings.js`
  renders the daemon's error string, but nothing checks what a save shows when the daemon
  answers a refusal the dialog cannot make the user fix (`eol` is not offered by it at all).
- `mcu` against an *older* daemon, which is class 46's mirror: this round's client-side
  refusals (timeout floor of 1, `min_window` bounds) are tighter than an older daemon's, so
  a command that used to work now fails in the client. That is the intended direction, but
  no test pins that the message names the option rather than reading as a daemon fault.
