# Fix-diff and test-quality leg, 2026-09-04

HEAD `7a1120f`.

Scope: `git diff 412c014..HEAD` in full - the eol config/request/CLI/web UI change, `POST /break` with `mcu break` / `mcu sysrq`, `/wait --repeat-ms`, `disconnect_reason`, and the SPEC, README, `mcu ai-guide` and test changes in the same range.
`serial_link.py` in isolation is another agent's leg; this one reads the diff as a whole and the four new test files.

Method.
Part A: `docs/REVIEW.md` read in full, then every hunk of the range read against the registry, with three suspicions driven rather than reasoned about (a probe file in the worktree, deleted afterwards).
Part B: 34 mutations across the four features, applied one at a time in a throwaway worktree at `/tmp/mcuscope-mut`, each followed by `pytest tests/test_break.py tests/test_eol.py tests/test_port_health.py tests/test_wait_repeat.py -q -x -p no:randomly` and reverted from the in-memory original.
Baseline: 108 passed in 38 s.

How the mutation runs were verified to import the mutated module.
The editable install is a plain `.pth` holding `/home/daniel/git/mcuscope/host`, not an import-hook finder, so `PYTHONPATH` wins on `sys.path` order.
Checked directly before the first run:

```
$ PYTHONPATH=/tmp/mcuscope-mut/host .../host/.venv/bin/python -c "import mcuscope.serial_link as s; print(s.__file__)"
/tmp/mcuscope-mut/host/mcuscope/serial_link.py
```

The main tree was read-only throughout except for this file; `git status` on it is clean.

---

## Findings

### D1 (HIGH) - a repeat task that dies with anything but `PortError` costs a 500 and leaks the capture watch for good

`host/mcuscope/server.py:1917-1928` (`_repeat_send`) and `host/mcuscope/server.py:2028-2037` (`_do_wait`'s `finally`).

`_repeat_send` catches only `PortError`, and the `finally` awaits the task before `watch.close()`.
Any other exception kills the task, `repeater.cancel()` on an already-done task is a no-op, `await repeater` re-raises it out of the `finally`, and `watch.close()` never runs.

Failure scenario, driven and confirmed:
`send_raw` reaches `store.add_line`, which raises `StoreError("store writer is not running")` whenever the writer thread has died - the exact condition class 12 exists for.
A `/wait ... repeat_ms` during it answers **500** and leaves a subscriber registered forever.

```
STATUS 500 {"error":"store writer is not running"}
SUBSCRIBERS before/after 0 1
```

`MAX_SUBSCRIBERS` is 256 (`store.py:173`), so 256 such waits and every later `/wait`, `/assert` and WS follower is refused with "too many subscribers" for the daemon's life.
The same call on the **match** path returns the answer and then raises over it, so a successful interception is reported as a 500 with the matched line discarded.

Registry: class 39 (the raced task consumed on every exit - it is cancelled but not *tolerated*), compounded by class 16 (one bad write ends the loop) and class 12 (the wait reports timeout while its writer is dead).
The non-repeat `/wait` path does not have this: its `finally` has no task in it. Class 38's shape - the variant path missing the discipline the original has.

Minimal fix, two edits.

```python
# _repeat_send
        except PortError:
            tally.failures += 1
```
->
```python
        except Exception:   # a store hiccup is the item's failure, not the loop's
            tally.failures += 1
```

```python
    finally:
        if repeater is not None:
            repeater.cancel()
            with suppress(asyncio.CancelledError):
                await repeater
        watch.close()
```
->
```python
    finally:
        try:
            if repeater is not None:
                repeater.cancel()
                with suppress(Exception, asyncio.CancelledError):
                    await repeater
        finally:
            watch.close()   # never behind the repeater's own exit
```

Test that fails without it (`tests/test_wait_repeat.py`): monkeypatch the port's `send_raw` to raise `StoreError`, POST `/wait` with `repeat_ms`, assert 200 with `status == "timeout"` and `send_failures >= 1`, and assert `len(stack.app.state.store._subscribers)` is back to its pre-call value.

### D2 (HIGH) - `/break` over a URL transport reports a break that never left the host, and SPEC promises the opposite

`host/mcuscope/link.py:177-181` (`SerialLink.send_break`), reaching `host/mcuscope/serial_link.py:1120-1123`.

The "transport cannot do this" test is `hasattr(self._ser, "send_break")`.
Every pyserial URL handler inherits or defines `send_break`, so the guard never fires and the `False` branch is dead.
`serial.urlhandler.protocol_socket.Serial.send_break` is a documented no-op (`self.logger.info('ignored send_break(...)')`), and `protocol_loop` is the same.

Driven against a real listener:

```
hasattr send_break: True
SerialLink.send_break -> True     # zero bytes reached the peer
```

So `POST /break` on a `socket://` port answers `{"ok": true}` and writes the sys row `port board: break 250 ms` for something that did not happen, and `mcu sysrq b` reports success against a target it can never reset.
SPEC 3.4 states: "a transport that cannot send a break (a `socket://` link) is a 400 too". Code and SPEC disagree, and SPEC wins.

Registry: class 17 (the reported value is the request, not the result) with class 12's face - the surface says the operation landed.

Minimal fix: decide on the transport, not on the attribute. In `link.py`:

```python
    def send_break(self, seconds: float) -> bool:
        if not hasattr(self._ser, "send_break"):
            return False   # a URL handler without the method; say so rather than raise
        self._ser.send_break(seconds)
        return True
```
->
```python
    def send_break(self, seconds: float) -> bool:
        # Every pyserial URL handler *has* send_break and the socket/loop ones ignore it,
        # so the capability is the transport, not the attribute (SPEC 3.4).
        if self._url or not hasattr(self._ser, "send_break"):
            return False
        self._ser.send_break(seconds)
        return True
```

(`SerialLink` already carries the device string it was opened with; gate on `is_url_device(self._device)` if there is no `_url` flag, and exempt `rfc2217://`, whose handler implements a real break, with a stated reason.)

Test that fails without it: attach a `socket://` port against a bare TCP listener, `POST /break`, assert 400 with "transport cannot send a break", and assert no `break` sys row was written.

### D3 (MED) - a `PUT /config/ports` that omits `eol` silently resets a saved `crlf` port to `lf`, and that is exactly what the web UI sends

`host/mcuscope/server.py:319` (`ConfigPortEntry.eol: Eol = PortConfig.eol`) against `host/mcuscope/webui/settings.js:collectPorts`.

`identify` is `bool | None` precisely so an omitted field keeps the saved value, and SPEC says so: "An omitted `identify` keeps the saved value for that alias (the settings dialog does not offer it), so a hand-written `identify = false` survives a save."
`eol` is in the identical position - the settings dialog does not offer it either, and `collectPorts` builds `{alias, autoconnect, device?, serial_number?, baud}` with no `eol` - but it defaults to `"lf"` instead of `None`.

Driven and confirmed:

```
after explicit save:   [{... 'eol': 'crlf' ...}]
after a UI-shaped save:[{... 'eol': 'lf' ...}]
AssertionError: an omitted eol silently reset the port to lf
```

Any Save in the settings Ports section wipes a hand-written `eol = "crlf"`, and `save_ports` then drops the key from `config.toml` entirely. On a CRLF-only target every command stops working after the next unrelated save, with nothing in the log naming the cause.

Registry: no clean existing class. **New class candidate: a write-back model field whose default is a real value silently overwrites the saved one, where its sibling with the same "the UI does not offer it" status uses `None` to mean keep.**
Sweep: for every write-back body model, list the fields the UI does not populate; each is `X | None = None` with a keep-the-saved branch, or is argued.

Minimal fix, mirroring `identify` exactly:

```python
    eol: Eol = PortConfig.eol
```
->
```python
    eol: Eol | None = None   # omitted: keep the saved value for this alias
```

and in `_register_routes`'s ports write-back (server.py:1217), alongside the existing `identify=` line:

```python
                    eol=entry.eol,
```
->
```python
                    eol=(entry.eol if entry.eol is not None
                         else saved_by_alias.get(entry.alias, PortConfig()).eol),
```

using the same lookup `identify` already uses. SPEC 3.3.1's `identify` sentence gains `eol`.

Test that fails without it: PUT ports with `eol: "crlf"`, then PUT the same list with no `eol` key, then `GET /config` and assert `eol == "crlf"`.

### D4 (MED) - the break's transport leg has no coverage at all; every assertion is on the sys row, which echoes the request

`host/tests/test_break.py` (whole file).

Four mutations that make the break not happen, or happen wrongly, all survive the full suite:

- M15: drop the `if not link.send_break(...)` refusal, keeping the call - SURVIVED.
- M31: never call `link.send_break` at all, keeping the not-connected guard - SURVIVED.
- M32: pass the duration in the wrong unit (`ms / 1000000`) - SURVIVED.
- M34: `SourceLink.send_break` succeeds on a closed link instead of raising - SURVIVED.

M13 ("break is a no-op") *was* caught, but by `test_break_on_a_disconnected_port_is_400` - it detected the missing refusal, not the missing break. Shared wording between two paths, and the suite cannot tell them apart.

The cause is that `SerialEndpoint`/`SourceLink` record writes (`stack.sim.written`, added by this same diff for the eol tests) but nothing records a break, so a break is invisible to every test.
Registry: class 17 in the tests - the assertion reads the request back.

Fix: give the sim endpoint the same treatment `written` got. In `host/tests/support.py`, add `self.breaks: list[float] = []` to `SimEndpoint`, and have `SourceLink.send_break` append `seconds` through the same `_Unpluggable` shim `feed` uses.
Then: `test_break_reaches_the_transport` asserts `stack.sim.breaks == [0.005]` after `POST /break {"ms": 5}` (kills M31, M32 and M15 at once), and one test drives `SourceLink.send_break` on a closed link asserting `SerialException` (kills M34).

### D5 (MED) - the repeat loop's re-anchor, the whole reason class 36 is cited in its comment, has no test

`host/mcuscope/server.py:1929-1931`.

M16 replaces `next_at = max(next_at + period_s, loop.time())` with `next_at = next_at + period_s` - the backfill the comment says must not happen - and the suite passes (SURVIVED).
A write that blocks out several periods (a `WRITE_TIMEOUT` stall, a Windows suspend) is then followed by that many writes back to back, each taking `_raw_lock` and a real serial write.
Bounded by the window (`repeat_ms >= 10`, `timeout_ms <= 300000`), so not the 324,008-line shape class 36 was named for, but the invariant is stated and unenforced - a stated invariant is a claim, not a mechanism.

Fix: no code change; the code is right. Add a test that makes one write take several periods (monkeypatch `send_raw` to sleep 200 ms once, `repeat_ms=20`), then asserts the total `sends` over a 600 ms window is close to `600/20` and not `600/20 + 10`, and that no two writes land within one period of each other after the stall.

### D6 (MED) - `mcu wait --repeat-ms` reads `res["sends"]` unguarded, so a daemon that ignores `repeat_ms` is a traceback

`host/mcuscope/cli.py:1047-1049`.

```python
    if repeat_ms is not None and not s.json_out:
        err(f"sent {res['sends']} times, {res['send_failures']} writes failed")
```

`WaitBody` does not forbid extra fields, so an older daemon accepts `repeat_ms`, ignores it, and answers without `sends`/`send_failures`.
The CLI then raises `KeyError` out of `main()`: a traceback to the user, which SPEC 4 says is a defect.
Version skew between `mcu` and `mcuscoped` is normal - `mcu daemon start` is a convenience, not a requirement, and the CLI otherwise defends against exactly this (`_list_field`, the shape guards of class 9).

Registry: class 9 (every path out of `mcu` maps to 0/1/2/3).

Fix:

```python
        err(f"sent {res.get('sends', 0)} times, {res.get('send_failures', 0)} writes failed")
```

Test: `run_mcu_canned` returning `{"status": "timeout", "line": None, "waited_ms": 1.0, "cmd_result": None, "dropped": 0}` for a `wait --repeat-ms` invocation; assert exit 2 and no traceback in stderr.

### D7 (LOW) - nothing asserts the repeater keeps writing after a detach, only after a reconnect

`host/tests/test_wait_repeat.py`.

M17 turns `if port is None: raise PortError(...)` into `return`, so the loop exits silently the moment the alias is detached - SURVIVED.
`test_the_match_lands_once_the_port_connects_mid_wait` covers `POST /ports/{alias}/reconnect` (which does catch M21, the captured-port mutation), but not `DELETE /ports/{alias}` followed by a fresh `POST /ports` under the same alias, which is the case the `ports.get(alias)` per tick is written for.
The docstring says the loop counts the failure and continues; nothing tests it.

Fix: no code change. Add a test that detaches mid-wait, sleeps two periods, re-attaches the same alias, and asserts the match still lands and `send_failures >= 1`.

### D8 (LOW) - three defensive guards in the diff have no driving test

- `_encode_wire`'s "refuse rather than default" on an unknown `eol` (`serial_link.py:1063-1066`): M2 (default to `b"\n"` instead of raising) SURVIVED. The comment argues the branch is unreachable from user input; a test calling `SerialPort._encode_wire("x", "cr")` and asserting `PortError` costs one line and pins the argument.
- `status()`'s `None if self.connected else ...` mask (`serial_link.py:1236`): M12 SURVIVED. The window is real - `hold()` sets `disconnect_reason = "manual"` before `await self.stop()`, so a `/status` in between sees `connected=True` with a reason set. A unit test that sets both and reads `status()` pins it.
- `SourceLink.send_break`'s closed-link raise (`link.py:272-281`): M34 SURVIVED; folded into D4's fix.

### D9 (LOW) - SPEC's `PUT /config/ports` body signature and `GET /config` response do not mention `eol`

`docs/SPEC.md:564` still reads `PUT /config/ports {ports: [{alias, device?, serial_number?, baud?, autoconnect?, identify?}]}`.
The `eol` bound is documented 40 lines above in the 3.3.1 bounds list, and the implementation both accepts it on the body and returns it from `GET /config` (`server.py:1066`), but the signature a reader copies from omits it.
Fix: add `eol?` to the signature, and (with D3) the keep-the-saved sentence.

### D10 (LOW) - `mcu sysrq $'\n'` passes the one-character check and reports success for a zero-byte write

`host/mcuscope/cli.py:429-432` and `serial_link.py:1085`.

`len(char) != 1` accepts `"\n"` and `"\r"`; `send_raw` then does `line.rstrip("\r\n")`, so an empty body is written with `eol: "none"` - nothing goes on the wire, and the CLI prints `sysrq \n (break 250 ms)`.
Harmless but a silent no-op reported as done (class 12's small face).
Fix: extend the guard to `if len(char) != 1 or not char.isprintable():` with the message naming the reason. Test: `run_mcu(stack, "sysrq", "\n")` asserts exit 1.

### D11 (LOW) - concurrent `/wait` repeats on one port are unbounded and undocumented

Nothing caps how many `_repeat_send` tasks target one alias.
Ten concurrent `/wait ... repeat_ms=10` put 1000 writes/s through one `_raw_lock`; each individually is fine and the lock serializes them, so this is a documentation gap rather than a defect: SPEC 3.4 says nothing about what a second concurrent repeat does.
Fix: one SPEC sentence stating that repeats are not serialized against each other beyond the port's write lock, or a per-port cap of one repeater with a 409. No test change if the sentence is the answer.

---

## Mutation table

34 mutations, 8 survived.

| # | Mutation | File | Caught by / SURVIVED |
|---|---|---|---|
| M1 | eol never appended (always LF) | serial_link.py | `test_cli_sysrq_breaks_then_sends_one_bare_character` |
| M2 | unknown eol defaults instead of refusing | serial_link.py | **SURVIVED** (D8) |
| M3 | port ignores its configured eol | serial_link.py | `test_status_reports_the_ports_eol` |
| M4 | `send_raw` ignores the request eol | serial_link.py | `test_cli_sysrq_breaks_then_sends_one_bare_character` |
| M5 | `send_command` ignores the request eol | serial_link.py | `test_cmd_honours_a_request_eol` |
| M6 | `held` no longer protects the manual reason | serial_link.py | `test_manual_survives_a_reader_callback_that_lands_after_hold` |
| M7 | connect does not clear `disconnect_reason` | serial_link.py | `test_a_connect_clears_the_reason_of_the_episode_before_it` |
| M8 | `open_failed` reported as `no_device` | serial_link.py | `test_reason_is_open_failed_when_the_device_is_there_but_busy` |
| M9 | absent node reported as `open_failed` | serial_link.py | `test_reason_is_no_device_when_the_open_fails_on_an_absent_node` |
| M10 | `read_error` reported as `no_device` | serial_link.py | `test_reason_is_read_error_when_the_link_drops_mid_session` |
| M11 | `hold()` does not set `manual` | serial_link.py | `test_manual_survives_a_reader_callback_that_lands_after_hold` |
| M12 | status reports the reason while connected too | serial_link.py | **SURVIVED** (D8) |
| M13 | break is a no-op (whole `to_thread` dropped) | serial_link.py | `test_break_on_a_disconnected_port_is_400` (for the wrong reason, see D4) |
| M14 | break on a closed port silently succeeds | serial_link.py | `test_break_on_a_disconnected_port_is_400` |
| M15 | break ignores a transport that cannot do it | serial_link.py | **SURVIVED** (D4) |
| M16 | repeat loop backfills (no re-anchor) | server.py | **SURVIVED** (D5) |
| M17 | repeat stops silently when the port detaches | server.py | **SURVIVED** (D7) |
| M18 | repeater is not cancelled on exit | server.py | `test_no_repeat_task_outlives_the_response` |
| M19 | every repeated write is stored | server.py | `test_only_the_first_write_is_stored` |
| M20 | `repeat_ms` is never refused | server.py | `test_repeat_without_send_is_refused` |
| M21 | repeater captures the port once | server.py | `test_the_match_lands_once_the_port_connects_mid_wait` |
| M22 | `repeat_ms` ceiling (`timeout_ms`) dropped | protocol.py | `test_repeat_above_the_timeout_is_refused` |
| M23 | `repeat_ms` floor dropped | protocol.py | `test_repeat_below_the_floor_is_refused` |
| M24 | `repeat_ms` no longer requires raw | protocol.py | `test_repeat_with_a_command_send_is_refused` |
| M25 | config eol accepts anything | config.py | `test_loader_warns_and_defaults_on_a_bad_eol` |
| M26 | `sends`/`send_failures` not reported on match | server.py | `test_match_on_the_first_tick_sends_once` |
| M27 | CLI `--eol` not sent on `send` | cli.py | `test_cli_send_eol_none_reaches_the_wire` |
| M28 | CLI `sysrq` sends with the port default terminator | cli.py | `test_cli_sysrq_breaks_then_sends_one_bare_character` |
| M29 | CLI `--repeat-ms` no longer implies `--raw` | cli.py | `test_cli_refuses_a_period_outside_the_window` |
| M30 | status omits the disconnect reason | cli.py | `test_status_names_why_a_disconnected_port_is_down` |
| M31 | break never reaches the transport (guard kept) | serial_link.py | **SURVIVED** (D4) |
| M32 | break duration in the wrong unit (`ms/1e6`) | serial_link.py | **SURVIVED** (D4) |
| M33 | non-repeat wait no longer counts its send | server.py | `test_without_repeat_the_counts_are_still_reported` |
| M34 | `SourceLink` break succeeds on a closed link | link.py | **SURVIVED** (D4, D8) |

Survivor clustering: 4 of the 8 are the break's transport leg, 2 are the repeat loop's stated-but-untested invariants, 2 are defensive guards.
The `disconnect_reason` and `eol` legs killed every mutation aimed at them; `test_port_health.py`'s reason tests in particular drive the real reader thread and pin the branch, which is why M8 and M9 (the two directions of the same predicate) both die.

## The two questions

**1. What am I least confident about, and how I rechecked it.**

The claim that `SerialLink.send_break` answers True over `socket://` (D2).
Reading pyserial's `protocol_socket.py` is not the same as running it - the `send_break` there is a stub whose body depends on `self.logger`, and I could have been reading a version the venv does not install.
Rechecked by driving it: a real listener, `serial.serial_for_url("socket://127.0.0.1:PORT")`, a real `SerialLink`, and printing the return value. `hasattr` True, `send_break` returns True, zero bytes to the peer.
Second on the list was D3, which I also drove rather than reasoned about, because "the UI omits the field" is the kind of claim a `collectPorts` I misread would invert.
Not driven, and stated as such: the M16 backfill's real-world magnitude on Windows across a suspend. I bounded it analytically (`timeout_ms / repeat_ms` <= 30,000) and did not measure it, and there is no Windows machine on this leg.

**2. What should have been checked that nobody asked for.**

The `mcu`-to-`mcuscoped` **version skew** surface of this diff, which produced D6 and is not in any registry sweep.
The round's whole framing is "the daemon and the CLI ship together", and every new field was reviewed for what the daemon does with a bad value from a client. Nobody asked the mirror: what the CLI does with a *missing* field from an older daemon.
This diff adds four such reads (`res["sends"]`, `res["send_failures"]`, `pt["disconnect_reason"]`, `port["eol"]`), and only `disconnect_reason` was written defensively - `test_status_names_why_a_disconnected_port_is_down` even has a case commented "An older daemon", so the concern was in the author's hands on one field and not carried to the sibling three.
Worth a registry class: **a client that reads a field a newer daemon added must tolerate its absence; the `.get` is the mechanism and one canned-response test per new field is the sweep.**

Second, smaller: `POST /break` bypasses the write-health accounting that `_write_bytes` maintains, so a port whose breaks all fail keeps `write_failures` at 0 and reads healthy in `mcu status`. Not driven; a `DEGRADED` port is the surface that would be expected to say so.
