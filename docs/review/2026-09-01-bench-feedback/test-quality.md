# Test-quality review of 9dd2290

Worktree HEAD was `4ab081a` (stale). Checked out `9dd2290` (detached) before any work; tree
restored clean afterwards.

Baseline: `tests/test_timeline.py tests/test_port_health.py tests/test_sessions.py` = 47 passed.
`firmware/tests`: 232/232 checks passed. Note `pytest-randomly` is active, so order is already
randomised on every run.

Method: 55 source mutations, each applied to the file it targets, the owning test run, the file
restored from an in-memory copy. 3 further mutations were re-run against the **whole** host suite
to size the blast radius. Every run below was executed; nothing is inferred.

---

## (a) Revert-verification table

`KILLED` = the test failed with the mutation and passes without it (the test does its job).
`SURVIVED` = the test passed with the behaviour broken (a finding).

### Timeline: paging / clock bounds / decode (`host/mcuscope/cli.py`, `cli_output.py`)

| # | Mutation | Test driven | Result |
|---|---|---|---|
| M01 | `_fetch_lines` breaks after the first page | `test_lines_limit_above_the_cap_is_honoured` | KILLED |
| M02 | `_fetch_lines` always returns `truncated: False` | same | KILLED |
| M03 | `log export` default limit back to 1000 (not `sys.maxsize`) | same | KILLED |
| M04 | `--json lines` does not restore the API's newest-first order | test_timeline.py | KILLED |
| M05 | `_clock_bounds` ignores `--to` (returns `id_to=None`) | `test_from_and_to_select_by_clock_time` | KILLED |
| M06 | `_clock_bounds` ignores `--from` (`since_ts=None`) | same | KILLED |
| M07 | `--to` past everything yields an empty window (`id_to=1`) | same | KILLED |
| M08 | rows fed to the decoder newest-first (`reversed` dropped) | test_timeline.py | KILLED |
| M09 | `--changes` keys every stream on one shared key | test_timeline.py | KILLED |
| M10 | `prime()` drops `keep_existing=True` (not newest-first) | test_timeline.py | KILLED |
| M11 | decoder primed without the window bound (`id_to=None`) | `test_decode_primes_from_a_definition_outside_the_window` | KILLED |
| M12 | bit-lane names no longer answer `--names` | `test_decode_renders_fields_and_changes_filters` | KILLED |
| M13 | `--names` with nothing left keeps the sample | `test_line_decoder_edges` | KILLED |
| M14 | `!pd` rows not dropped from decoded output | test_timeline.py | KILLED |
| M15 | `fmt_age` does not clamp negatives | `test_fmt_age` | KILLED |
| M16 | `fmt_age` unit order reversed | `test_fmt_age` | KILLED |
| M17 | `clock_option` does not validate at parse time | `test_a_malformed_clock_is_a_usage_error` | **SURVIVED** |
| M18 | `parse_clock` combines HH:MM with a fixed date, not today | test_timeline.py | KILLED |

### Port health (`host/mcuscope/serial_link.py`, `cli.py`)

| # | Mutation | Test driven | Result |
|---|---|---|---|
| M19 | drop `else: self.write_failures = 0` in `_write_bytes` | `test_write_failures_are_counted_named_and_reset` | KILLED |
| M20 | `_on_disconnect` does not reset `write_failures` | same | KILLED |
| M21 | streak suffix never appended (`if True:`) | same | KILLED |
| M22 | `last_write_error` not recorded | same | KILLED |
| M23 | `_identify` never sets `target` | `test_connect_pings_and_reports_the_target` | KILLED |
| M24 | `_identify` never runs at all | same | KILLED |
| M25 | `status()` hides `target` | test_port_health.py | KILLED |
| M26 | `_port_state` never says DEGRADED | `test_status_shows_a_degraded_port_and_the_target` | KILLED |
| M27 | `_port_target` prints nothing | test_port_health.py | KILLED |
| M28 | `--retry-ms` retries **any** error, not only `busy` | `test_retry_ms_retries_busy_until_the_deadline` | **SURVIVED** |
| M29 | retry deadline ignored, always one attempt | same | KILLED |
| M30 | retry happens even without `--retry-ms` | same | KILLED |
| M31 | `plot channels --active` does not filter | `test_plot_channels_shows_age_and_active_filters` | KILLED |
| M32 | `--active` filter not applied to the `--json` body | same | KILLED |
| M33 | channel age always `?` | same | KILLED |
| M34 | empty `--active` prints the generic "no plot channels captured yet" | same | KILLED |

### Sessions (`host/mcuscope/server.py`)

| # | Mutation | Test driven | Result |
|---|---|---|---|
| M35 | shutdown closes a **named** session too | `test_named_session_survives_a_daemon_restart` | KILLED |
| M36 | shutdown never closes **any** session | same | **SURVIVED** (killed by the full suite) |
| M37 | startup does not resume a named session | same | KILLED |
| M38 | the `resuming session:` sys row loses the name | same | KILLED |

### Firmware (`firmware/monitor/monitor.c`)

| # | Mutation | Result |
|---|---|---|
| MC01 | `plot_reject` emits on every call, not once per sid | KILLED |
| MC02 | `plot_reject` never emits | KILLED |
| MC03 | `monitor_init` does not clear `g_plot_rejected` | KILLED |
| MC04 | the `len` rejection reports `def` | KILLED |
| MC05 | the `body` rejection reports `def` | KILLED |
| MC06 | the rejected-`len` rollback `s->used = false` removed | KILLED |

### Whole-suite blast radius for the survivors

| # | Mutation | Full `host` suite |
|---|---|---|
| S17 | `clock_option` does not validate at parse time | **SURVIVED** (whole suite green) |
| S28 | `--retry-ms` retries any error | **SURVIVED** (whole suite green) |
| S36 | shutdown never closes any session | KILLED (another session test catches it) |

### The six adjusted existing tests (mutation = revert the adjustment)

| # | Mutation | Test | Result |
|---|---|---|---|
| A1 | `--drop-response 2` back to `1` | `test_cli.py::test_cmd_timeout_exit2` | KILLED (value is load-bearing) |
| A2 | same | `test_e2e.py::test_cmd_timeout_on_dropped_response` | KILLED |
| A3 | same | `test_regressions.py::test_wait_with_send_still_matches_...` | KILLED |
| A4 | same | `test_regressions.py::test_assert_with_send_still_judges_...` | **SURVIVED** |
| A5 | `parse_args(["--drop-response","2"])` back to `1` | `test_cancelling_a_command_does_not_leak_its_pending_entry` | KILLED |
| A6 | `2 in port._pending` back to `port._pending` | `test_cancelling_a_command_mid_write_...` | SURVIVED (a strengthening, not a weakening: see note N1) |
| A7 | `send_raw` writes a `>`-prefixed payload (is the new `startswith(b">")` filter too broad?) | `test_reconnect.py::test_write_goes_to_the_link_...` | KILLED (filter is not too broad) |
| A8 | reader misfiles debug lines onto `chan="cmd"` (does the widened `not in ("sys","cmd")` hide it?) | the two adjusted reader tests | KILLED (exclusion is not too broad) |

Order-independence of the module-scoped `stack` in `test_timeline.py`: 10 `--randomly-seed` runs
(1..10) all green, plus each test run alone. Every test scopes its rows with a distinct `--match`
prefix (`^bulk`, `^clk-`, `^!p[ds] 7 `, `^!ps 6 `), and the sim emits no `!p*` traffic without
`--plot`, so cross-test contamination is structurally prevented. No finding.

---

## (b) Findings

### F1 - `test_port_health.py:132-139` - "only busy is retried" is a tautology

Claim: `--retry-ms` retries only `ERR 6 busy`, never another error.

The `not_busy` handler is the only one in the file with no call counter, and the assertion is
`rc == 1 and "ERR 2 badarg" in err`. When `_run_cmd` retries *every* error, it still ends by
emitting the same `badarg` answer at the deadline, so `rc` and the stderr text are identical on
both branches. Verified: mutating `cli.py:_run_cmd` to `busy = res.get("status") == "err"` leaves
this test green **and the entire host suite green** (S28). The contract that a `badarg` is not
retried is pinned nowhere in the repo.

Concrete scenario it lets through: a caller writes `mcu cmd 'gpio get nope' --retry-ms 5000`
against a target that answers `badarg` immediately; the CLI silently spins for 5 s at 50 Hz,
re-sending a command that can never succeed, and returns the same failure. On a bench script that
wraps several such calls this turns a fast failure into a multi-second hang, and it re-drives the
target for a typo.

The fix shape (not applied): give `not_busy` the same `calls` counter and assert `calls["n"] == 1`.

### F2 - `host/mcuscope/cli_output.py:212-216` + `test_timeline.py:109-112` - the parse-time validation callback is untested

Claim (the docstring): "reject a malformed `--from`/`--to` **at parse time**".

`test_a_malformed_clock_is_a_usage_error` asserts only `returncode == 1` and the message
`expected HH:MM[:SS[.mmm]]`. Both are produced identically by the *second* `parse_clock` call
inside `_clock_bounds`, which runs later on the same code path. Verified: deleting the callback
body entirely (`clock_option` becomes `return value`) leaves this test green and the whole host
suite green (S17).

Concrete scenario: someone deletes `callback=clock_option` from `FROM_OPTION`/`TO_OPTION` as dead
weight; the suite stays green, and the regression only shows up on a command that reaches the
network before `_clock_bounds` (or on a future subcommand that takes `--from` without calling
`_clock_bounds` - `tail` already takes `--decode` but not `--from`, so this is one option away).
The distinguishing surface the test needs is that **no HTTP request is made**: point the CLI at an
unreachable `--url` and assert it still exits 1 with the usage message rather than exit 3
"not running".

### F3 - `test_sessions.py:808-812` - the last block of `test_named_session_survives_a_daemon_restart` asserts nothing

Two problems in three lines.

1. `assert all(s["id"] != auto_id or s["ended_ts"] is not None for s in body["sessions"])` is
   **vacuously true**. Probed directly against `_mk_app`: `auto_id` is 3, and run 3 lists ids
   `[4, 2]` - the empty automatic session is *deleted* by `stop_session`, never listed. The
   comment on line 808 ("dropped here: no device traffic") shows the author knew, yet the
   assertion was left in a form that can only be satisfied by the row's absence.
2. The stated claim, "An automatic session still ends with the daemon run", is not pinned by the
   surviving half either. Verified (M36): removing the shutdown close entirely - `pass` in place
   of the `if active["auto"]: await store.stop_session()` block - leaves this test green, because
   the *next* startup's `start_session` closes the stale auto session anyway, so
   `body["active"]["id"] != auto_id` still holds. The test cannot tell "closed at shutdown" from
   "closed at the next startup".

Mitigation: M36 is caught elsewhere in the suite (S36 killed), so this is a weakness of the new
test, not an uncovered behaviour. But the block as written contributes nothing, and a reader
would wrongly believe the shutdown path is covered here.

Concrete scenario: someone reworks the shutdown handler so it stops closing auto sessions; this
test stays green and the failure surfaces in an unrelated session test, pointing the reader at the
wrong module.

### F4 - `test_regressions.py:901` - the adjusted `--drop-response 2` is not load-bearing in the /assert test

The /wait sibling (line 883-884) asserts its precondition explicitly:
`assert body["cmd_result"]["status"] == "timeout"` with the comment "the precondition this test
needs to hold". The /assert test has no such assertion. Verified (A4): reverting it to
`--drop-response 1` - which now drops the *connect-time ping* instead of the test's own `send` -
leaves the test green.

Concrete scenario: the `send` gets a prompt response, the CAN heartbeat arrives inside the window,
`checked_lines > 0` and `status == "pass"`. The regression the test names in its own docstring
("`/assert` kept `if remaining <= 0: break` ahead of its drain") is never driven. The commit's
adjustment did not cause this, but it silently depends on the same ordering assumption its sibling
verifies and this one does not. One line - `assert body["cmd_result"]["status"] == "timeout"` -
closes it.

### F5 - `firmware/tests/test_monitor.c:875-892` - the registry-exhaustion path emits an untested and misleading reason

`monitor.c:696-700` routes both causes of a NULL `plot_alloc` through `plot_reject(sid, "def")`:
a genuinely bad definition **and** "all 4 stream slots are taken". `test_plot_registry_full`
(line 890) checks only the return code, never `fake_tx()`, so the `!e plot 4 badarg def` line the
5th stream now emits is asserted nowhere.

Concrete scenario: firmware registers a 5th plot stream. The bench sees `!e plot 4 badarg def` and
the engineer goes looking for a syntax error in a definition that is perfectly valid, when the
actual cause is `MON_PLOT_MAX_STREAMS`. The header's new wording ("so a stream that never appears
on the host says why") makes the wrong answer worse than none. Either a distinct reason (`full`)
or an assertion pinning the current text belongs in that test.

### F6 - `monitor.c:687-692` - two rejection paths are still silent, contradicting the new header contract

`monitor.h` now promises: "The first rejection of a sid also emits `!e plot <sid> badarg
def|body|len` once". The two guards ahead of `plot_reject` return `MONITOR_ERR_BADARG` with no
event at all:

- `if (!def || !def->sid || !def->body)` - a null body or a zero sid
- `if (def->sid < '0' || def->sid > '9')` - a non-digit sid

No test in `test_monitor.c` drives either against the new promise (grepped: no bad-sid case
exists). This is the exact class the commit set out to fix - "a rejected stream is otherwise
invisible" - left open for the two cases a hand-written `mon_plot_def_t` is most likely to hit
(`.sid = 0` from a partially initialised struct, `.sid = 'A'` from someone assuming hex sids).

Note the sid-range guard is what keeps `1u << (sid - '0')` in `plot_reject` inside `uint16_t`;
that is correct, but it is an undocumented coupling between the two.

### F7 - wall-clock thresholds (runbook forbids these)

- `test_port_health.py:152` - `assert "age=5s" in out`. The test computes `now = time.time()`,
  and `plot_channels` computes its own `now` moments later; `fmt_age` truncates to whole seconds,
  so **any** delay of 1 s or more between the two reads renders `age=6s` and the test fails. This
  is the tightest margin in the new tests. A loaded CI runner, a GC pause or a debugger breakpoint
  between the two lines is enough. Asserting on `age=4d` (a 24 h margin) is safe; the 5 s one is
  not. The fix shape is to monkeypatch `time.time` or to assert `re.search(r"age=\ds", out)`.
- `test_port_health.py:37` - `abs(st["last_write_error_ts"] - time.time()) < 5`. Same class,
  5 s of slack, low practical risk.
- `test_port_health.py:130` - `calls["n"] >= 2` with `--retry-ms 100` and a 20 ms sleep. If the
  very first in-process `MockTransport` round trip takes >100 ms the deadline is already past and
  `calls["n"] == 1`. Unlikely, but it is a wall-clock lower bound.
- `test_timeline.py:115-123` (`test_parse_clock_forms`) - reads `datetime.date.today()` in the
  test and `parse_clock` reads it again inside. A run that crosses midnight between the two lines
  compares against the wrong day. One-second-per-day window; cheap to remove by freezing the date.

### F8 - `test_timeline.py:60` - only one of `note_truncated`'s two remedy branches is reachable

`note_truncated` (`cli_output.py:342`) chooses `"raise --limit or use --since-id"` when
`got == limit` and `"use --since-id"` otherwise. With `_fetch_lines` now paging until it has
`limit` rows, `truncated=True` implies `got == limit` on every ordinary path, so the second branch
is only reachable through the `oldest <= 1` / non-int-id escapes inside `_fetch_lines`. The test
asserts the substring `"truncated at 1150 rows"` and neither remedy, so the branch is neither
covered nor known to be dead. Low severity; worth one assertion on the remedy text so the dead
branch is visible if it becomes truly unreachable.

### N1 - not a finding, recorded for completeness

`test_regressions.py:1011` (`while 2 not in port._pending` / `assert 2 in port._pending`) survives
reverting to the old `port._pending` form (A6). That is because the old form also passes here, not
because the new form is weak: the new form is strictly stronger (it pins that the *test's* command,
seq 2, registered, rather than accepting the connect ping's entry). The adjustment strengthened
the test.

---

## (c) Could not verify

1. **Windows.** Nothing here was run on Windows; no Windows host was available. Reading the new
   code, the specific surfaces at risk and why each looks safe:
   - `time.strftime("%H:%M:%S", time.localtime(...))` in `serial_link._write_bytes` - locale-free
     directives, safe.
   - `test_lines_limit_above_the_cap_is_honoured:74` compares stdout against
     `f"wrote 1200 lines to {out_file}"`; on Windows the path carries backslashes on both sides of
     the comparison, and the CLI writes with an explicit `newline=`, so `read_text().splitlines()`
     should still count 1200. Both are inference, not observation.
   - Subprocess text mode is `CHILD_TEXT` from `tests/support.py`, shared with the existing CLI
     suite, so the new `run_mcu` calls inherit whatever that already gets right.
2. **Python 3.10 (the declared floor).** The venv here resolves to 3.12.11; `uv venv --python 3.10`
   was not attempted (it would have changed the environment the rest of the review ran in). The
   divergence is real and measured on 3.12: `datetime.time.fromisoformat` accepts `"1953"`,
   `"T19:53"`, `"19"` and 2-digit fractions like `"19:53:35.25"` on 3.11+, and rejects several of
   them on 3.10 (the leading `T` and non-3/6-digit fractions were added in 3.11). `parse_clock`
   therefore accepts a different set of `--from` forms on the floor than on the dev interpreter,
   and `test_parse_clock_forms` pins only the three forms that work on both. Not a failure, an
   untested boundary that will read as a user-facing inconsistency.
   DST is a second unverified case: `parse_clock` builds a naive local datetime and calls
   `.timestamp()`, so an `HH:MM` inside a DST gap or fold resolves ambiguously. Two hours a year;
   no test drives it.
3. **The `--decode` priming cap.** `_make_decoder` primes from at most 40 `^!pd ` rows
   (`cli.py:_make_decoder`). The module fixture holds 3, so the cap is never approached. On a real
   bench, with the monitor's 5 s `!pd` rebroadcast, 40 rows is roughly 200 s of capture per stream
   - a window older than that would fail to prime and samples would render raw. Untested, and not
   reproducible without either a fixture that writes 40+ `!pd` rows or bench hardware.
4. **The `--drop-response` ordering assumption itself.** The four adjusted tests now assume the
   connect-time ping is command #1 at the sim. `send_command` assigns the seq inside `_cmd_lock`,
   and `_identify`'s task is created inside `_on_connect` (one loop callback), so it reaches the
   lock before any later-arriving HTTP request - but that ordering is enforced by scheduling
   latency, not by code. A1/A2/A3/A5 confirm the value is load-bearing today. What could not be
   driven is the reconnect case: if a port drops and reconnects mid-test, a *second* identity ping
   consumes `cmd_count` 2 and the drop lands on the wrong command. No mechanism in the tests
   prevents that; forcing it would need a fault-injecting link the suite does not have.
   `test_reconnect.py:679-681` already concedes the related weaker fact - the ping "may or may not
   have been written by now" - so the suite now holds two different assumptions about the same
   ping's ordering.
