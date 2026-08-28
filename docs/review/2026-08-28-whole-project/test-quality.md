# Test-quality leg (leg 6) - review round r2

## Provenance

- Worktree HEAD **at start**: `b1559e1 Registry class 39: a raced task orphaned by the exceptional exit`.
- **The worktree was 11 commits BEHIND the expected `fd76735`.** `git log --oneline fd76735..b1559e1` was empty and `b1559e1..fd76735` was 11, so `b1559e1` is a strict ancestor.
- The gap contained the entire target of this leg: `host/tests/test_plotjuggler.py`, `host/tests/webui_js/settings_pj.test.mjs`, `host/mcuscope/pjstream.py` and the `cli.py` split did not exist at `b1559e1`.
- `fd76735` was present in the local object store, so I `git checkout --detach fd76735` rather than review code that predates the work under review. **All results below are against `fd76735`.**
- Baseline before any mutation: `939 passed, 1 skipped` in 336 s. The one skip is honest (`test_reconnect.py:83`, Windows COM enumeration).
- Worktree left clean: `git status --short` is empty. Nothing committed.

## Method

40 source mutations across ~25 tests, each applied to the source the test guards, run, then reverted automatically by a harness (`/tmp/claude-1000/review-r2/mutate.py`, cases in `cases1..4.json`, raw output in `run1..4.txt`).
Weighted to (a) the PlotJuggler round and the last 15 commits, (b) tests whose names promise a refusal or a bound, (c) tests with sleeps or timing margins.
A test that survives its mutation is a finding.

Result: **33 KILLED, 7 SURVIVED.** Of the 7 survivors, 2 are equivalent-mutant artifacts of my own probe (recorded below as non-findings) and 5 are real.

## Findings

### F1 - HIGH (CONFIRMED). The streamer's torn-read fix ships with no check at all

`d426ed3` ("PlotJuggler streamer made torn-read-proof") added two invariants, both stated in `host/mcuscope/pjstream.py`'s module docstring and in `PlotJugglerStreamer.__init__`:

- `_target` is one immutable `(socket, sockaddr)` pair, swapped whole and read once in `send`.
- A replaced socket goes to `_retired` and is closed only on the *next* swap, because an in-flight `send` on the loop may still hold it and an immediate close could hand its fd to an unrelated socket mid-`sendto`.

Neither has a test.

Mutation M11, reverting the deferred close to an immediate one:

```python
# before
old, self._target = self._target, target
retired, self._retired = self._retired, (old if old is not target else None)
if retired is not None:
    retired[0].close()
# after
old, self._target = self._target, target
if old is not None and old is not target:
    old[0].close()
```

Outcome: `43 passed` - the entire `tests/test_plotjuggler.py` is green with the fix reverted. Restored: `43 passed`.

Supporting probe M29 replaced `send`'s single `target = self._target` read with two `self._target` loads: also `43 passed`. That mutation is semantically equivalent under a single thread, so it does not prove a defect - it does show that no test constrains the read shape either.

Why it matters here: leg 2's own rule is that a fix leaves a check behind. A cheap deterministic one exists and was not written: after `configure(True, a)` then `configure(True, b)`, assert `pj._retired` is the old pair and that `pj._retired[0].fileno() != -1` (still open); after a third `configure`, assert the first socket is closed.

### F2 - HIGH (CONFIRMED). The class-39 fix is in two halves and the regression test pins only one

`568741d` ("A snapshot that raises must not orphan the recv it raced") says in its own message: "Both consumers now cancel and await the task on the way out, in `_stage_backfill` and around the staged drain in `_follow_ws`; the new test forces the teardown shape a merely hanging fake cannot reproduce, and fails without the cleanup."

That is true of half A only.

- **M30, half A** - removed the `recv.cancel(); await recv` block from `_stage_backfill`'s `except BaseException`. `tests/test_cli.py::test_stage_backfill_consumes_its_recv_when_the_snapshot_raises` **KILLED** (1 failed). The test does discriminate.
- **M31, half B** - removed the identical cleanup around the staged drain in `_follow_ws` (`host/mcuscope/cli.py`, the `if pending is not None: pending.cancel() ...` block), leaving only `raise`. Ran the whole file, not one selector: **`160 passed in 97.32s`. SURVIVED.**

So the half that runs on the ordinary `mcu tail -f | head` path through `_follow_ws` can be deleted and the suite stays green. This is precisely the shape leg 6 already records ("revert each half separately, or the test pins the half you did not break"), recurring in the commit that cites it.

The existing test also has the weaker shape of asserting an absence (`assert [m for m in reports if "never retrieved" in m] == []`), which is satisfied by anything that stops the handler firing. M30 shows it is not vacuous for half A, but a half-B analogue needs writing, driving `_follow_ws` with a `pending` recv and a `handle` that raises `BrokenPipeError`.

### F3 - MED (CONFIRMED). `settings_pj.test.mjs` asserts the checkbox value the test itself wrote

`host/tests/webui_js/settings_pj.test.mjs`, test "a change applies live and echoes the daemon's answer":

```js
box().checked = true;
dest().value = "10.0.0.5:9870";
dest().emit("change", {});
await tick(0);
...
assert.equal(box().checked, true);
```

The test sets `box().checked = true` before firing the event, so the assertion holds whether or not `applyPj` echoed the daemon's answer back. `dest().value` is not asserted after the round trip at all.

Mutation N4 deleted both echo lines from `applyPj` in `host/mcuscope/webui/settings.js`:

```js
// removed
$("cfgPjEnabled").checked = st.enabled;
$("cfgPjDest").value = st.dest;
```

Outcome: **`1 passed` (the whole node suite green). SURVIVED.** Restored: passes.

The behaviour the removed lines exist for is documented one line above them - "Echo the daemon's answer, so a kept-previous dest (blank field) becomes visible" - and that path is never driven either. Mutation N5 changed `dest: dest || null` to `dest: dest` (sending `""`, which the daemon answers 422 for) and also **SURVIVED**: no JS test leaves the dest field blank.

The three refusal-path tests in the same file are sound - N1 (drop the re-sync GET), N2 (ungate the save on a successful apply) and N3 (also wipe the typed dest) were all KILLED.

Fix: assert the fields against a daemon answer that differs from what the test typed (leave `dest` blank and have the stub return the previous dest; set the checkbox to `false` and have the stub answer `true`).

### F4 - MED (CONFIRMED). `test_parse_dest_refuses` cannot tell two refusal paths apart, and masks a wrong message

`tests/test_plotjuggler.py::test_parse_dest_refuses` asserts only `pytest.raises(ValueError)` over 18 parameters, two of which (`"2001:db8::1"`, `"::1"`) carry the comment "a bare IPv6 literal must not donate its last group as the port".

Mutation M3 disabled the dedicated branch in `pjstream.parse_dest`:

```python
# before
if ":" in host:
    raise ValueError(f"IPv6 literal must be bracketed: [{host}]:{port}")
# after
if False:
    ...
```

Outcome: **`18 passed`. SURVIVED.** Both bare-IPv6 cases still raise, but from `_HOST_RE.fullmatch` two lines below (`"destination host '2001:db8:' is not a hostname or address"`), a different path with different wording.

Two consequences:

1. The guidance branch and its message are untested; only the exception type is pinned. This is leg 6's "shared wording between two refusal paths" in its weaker form - shared *exception type*.
2. It masks a real message defect. The branch is live, and its output is wrong:

```
parse_dest("2001:db8::1") -> ValueError: IPv6 literal must be bracketed: [2001:db8:]:1
parse_dest("::1")         -> ValueError: IPv6 literal must be bracketed: [:]:1
```

`host` and `port` here are the results of `rpartition(":")`, so the message tells the user to write `[2001:db8:]:1` - a truncated address and a port they never typed. The correct suggestion is `[2001:db8::1]:<port>`. No test would notice, because no test reads the message.

Fix: use `pytest.raises(ValueError, match=...)` on the cases that have a distinct intended message, and correct the suggestion to use the whole input.

### F5 - LOW (CONFIRMED). The suite's offline veto is a `setdefault`, and nothing notices when it is defeated

`host/tests/conftest.py:18`:

```python
# No test may reach out to PyPI. ... set the environment veto before anything imports the
# daemon, so the suite stays offline whatever a test's config says.
os.environ.setdefault("MCUSCOPE_UPDATE_CHECK", "0")
```

`setdefault` yields to a value already in the environment, so a developer or CI job with `MCUSCOPE_UPDATE_CHECK=1` exported gets the opposite of the stated invariant, silently. Mutation M37 forced `os.environ["MCUSCOPE_UPDATE_CHECK"] = "1"`: **`33 passed`. SURVIVED** - nothing in the suite detects it.

`tests/test_config_api.py:344` already works around the veto by hand (`checker.enabled = True  # the suite's environment veto would refuse every check`), so the veto is load-bearing and known to be so.

This is a stated invariant with no mechanism. Fix is one word: assign instead of `setdefault`. Environment-dependence sweep otherwise came back clean - no test depends on DNS (the PlotJuggler resolver failures are monkeypatched, deliberately, and the docstring says why), on locale, on timezone, or on the real config dir (the autouse `_isolated_user_dirs` fixture redirects all three platformdirs functions).

## Non-findings (probe defects, recorded so they are not re-run)

- **M22** - I mutated `mcu pj --save`'s body to `{"dest": dest or body["dest"]}`, which is equivalent to the original for both legs the test drives. SURVIVED for that reason only. Re-run sharply as **M32** (`"dest": "127.0.0.1:1"`): **KILLED**. `test_cli_save_persists_to_config` does discriminate that the saved dest is the daemon's echo, not the request.
- **M29** - two `self._target` loads instead of one is a semantically equivalent mutant under a single thread. Folded into F1 as supporting evidence, not counted as an independent finding.

## Sample list with verdicts

`discriminates` = the mutation of the source it guards made it fail.

| # | Test | Mutation | Verdict |
|---|---|---|---|
| M1 | `test_parse_dest_refuses` | port grammar -> bare `int()` | discriminates |
| M2 | `test_parse_dest_accepts_host_port` | IPv6 brackets not stripped | discriminates |
| M3 | `test_parse_dest_refuses` (IPv6 cases) | bracket-guidance branch removed | **wrong-surface (F4)** |
| M4 | `test_parse_dest_refuses` | `_HOST_RE` check removed | discriminates |
| M5 | `test_non_unicast_dest_refused` | multicast/unspecified check removed | discriminates |
| M6 | `test_reserved_alias_is_renamed` | `ts`/`tick` rename removed | discriminates |
| M7 | `test_non_finite_values_dropped_not_emitted` | `math.isfinite` filter removed | discriminates |
| M8 | `test_send_swallows_socket_errors` | `except OSError` removed | discriminates |
| M9 | `test_bad_configure_keeps_previous_state` | `self.dest` mutated before the failing statement | discriminates |
| M10 | `test_dest_changed_while_disabled_wins_on_enable` | dest set while disabled dropped | discriminates |
| M11 | `tests/test_plotjuggler.py` (all 43) | retired socket closed immediately | **tautological (F1)** |
| M12 | `test_rest_put_is_denied_from_network_without_token` | config-write bar removed from PUT /plotjuggler | discriminates |
| M13 | `test_attach_denied_from_network_without_token` | config-write bar removed from POST /ports | discriminates |
| M14 | `test_startup_with_dead_resolver_serves_disabled` | startup resolver failure not caught | discriminates |
| M15 | `test_rest_runtime_and_saved_are_separate` | /config/plotjuggler also applies at runtime | discriminates |
| M16 | `test_rest_bad_dest_is_400_and_state_holds` | /config/plotjuggler grammar pre-check removed | discriminates |
| M17 | `test_config_malformed_dest_warns_and_defaults` | loader fallback removed | discriminates |
| M18 | `test_daemon_flag_overrides_config` | bare `--pj` overwrites the configured dest | discriminates |
| M19 | `test_cli_plotjuggler_and_alias` | status prints the pj line when off | discriminates |
| M20 | `test_cli_plotjuggler_and_alias` | `--save` without on/off no longer refused | discriminates |
| M21 | `test_cli_plotjuggler_and_alias` | bad state word no longer refused client-side | discriminates |
| M22 | `test_cli_save_persists_to_config` | equivalent mutant | probe defect, see M32 |
| M25 | `test_sim_plot_lines_arrive_as_datagrams` | `enabled=false` made a no-op | discriminates |
| M26 | `test_env_overrides_config_in_both_directions` | env override made one-way | discriminates |
| M27 | `test_env_veto_treats_an_unrecognised_value_as_a_veto` | unrecognised value stops vetoing | discriminates |
| M28 | `test_disabled_checker_reports_nothing_despite_a_warm_cache` | `status()` ignores `enabled` | discriminates |
| M29 | `tests/test_plotjuggler.py` | `_target` read twice | equivalent mutant, see F1 |
| M30 | `test_stage_backfill_consumes_its_recv_when_the_snapshot_raises` | half A cleanup removed | discriminates |
| M31 | `tests/test_cli.py` (all 160) | half B (`_follow_ws`) cleanup removed | **tautological (F2)** |
| M32 | `test_cli_save_persists_to_config` | `--save` persists a dest never applied | discriminates |
| M33 | `test_overlong_match_rejected` | `MAX_MATCH_LEN` 200 -> 5000 | discriminates |
| M34 | `test_rebound_host_refused_even_with_matching_origin` | Host guard disabled | discriminates |
| M35 | `test_host_allowed_logic` | `_host_allowed` returns True | discriminates |
| M36 | `test_attach_rejects_dangerous_device_over_api` | refusal message genericised | discriminates |
| M37 | `tests/test_update_check.py` (all 33) | offline veto forced to `1` | **environment-dependent (F5)** |
| N1 | `settings_pj` "refused change re-syncs" | re-sync GET result dropped | discriminates |
| N2 | `settings_pj` "save saves nothing when refused" | save ungated from apply | discriminates |
| N3 | `settings_pj` "typing survives" | typed dest also wiped | discriminates |
| N4 | `settings_pj` "echoes the daemon's answer" | echo removed | **tautological (F3)** |
| N5 | node suite | blank dest sent as `""` not `null` | **wrong-surface (F3)** |

## Platform-inert check (Linux)

No test found inert-on-Linux beyond the one honest skip.

- `tests/test_reconnect.py:83` (Windows COM enumeration) skips with a reason. Correct, and already tracked in the project's Windows checklist.
- `tests/test_stdio.py` branches on `sys.platform` inside three tests rather than skipping, but each branch asserts something on both platforms (`test_console_ctrl_handler_is_windows_only` asserts `False` plus `have_console() is True` on POSIX). Not inert.
- `tests/test_regressions.py:608` (`_same_path`) carries an in-line note that its normalisation cases were moved *out* of the `os.name == "nt"` guard for exactly this reason. Only the case-folding cases remain Windows-gated, correctly.

## Not covered by this leg

- The firmware C suite (`firmware/tests/test_monitor.c`) was not mutation-probed; the Python-side wiring (`test_firmware_monitor.py`) skips cleanly without a toolchain and did not skip in this run. Worth a mutation pass in the next round.
- Non-PlotJuggler webui_js files (`api_*.test.mjs`, `plots_*.test.mjs`, `freeze.test.mjs`) beyond the `settings_pj` set.
- `test_hardening.py`'s size-cap and backlog-split bounds were listed as candidates and not probed for time; they name bounds, so they are the highest-value remaining sample.
