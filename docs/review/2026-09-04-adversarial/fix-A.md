# Fix batch A, 2026-09-04

Base HEAD `7a1120f`, working tree (uncommitted). Files touched: `host/mcuscope/server.py`,
`host/tests/test_wait_repeat.py`, new `host/tests/test_server_scope.py`, new
`host/tests/test_config_ports_eol.py`, `docs/SPEC.md` (3.3.1 ports signature, 3.4 `/wait`,
`/ws`, `/sessions/stop`).

## What changed

**A1 (S2 / D1)** `_repeat_send` now catches `Exception`, not `PortError` alone: `send_raw`
reaches `store.add_line`, whose `StoreError` is a plain `RuntimeError`. The first failure of
a task logs at warning, the rest are counted only. `_do_wait`'s `finally` wraps the
cancel-and-await in a `try` whose own `finally` runs `watch.close()`, and awaits under
`suppress(Exception, CancelledError)`, so a repeater that died cannot skip the close or
re-raise over a response already built.

**A2 (L2)** `_do_wait` validates the body once before creating the repeater:
`port_obj._encode_wire(body.send, body.eol or port_obj.eol)` inside `try/except PortError ->
_bad_request`. Port-state failures stay counted, as designed.

**A3 (W1 / D3)** `ConfigPortEntry.eol` is `Eol | None = None`. `put_config_ports` builds
`saved_eol` beside `saved_identify` and falls back to the saved value, or `PortConfig.eol`
for a new alias. SPEC 3.3.1's signature gained `eol?` and the keep-the-saved sentence now
names both fields.

**A4 (S4 + S5)** `/plot/export` and `_do_assert`'s retrospective branch freeze the window
once per request: `if last_ms is not None and id_to is None: id_to = store.max_id()`, placed
before the first store call. `_window_floor` (store.py:1282) anchors at the ts of the newest
row `<= id_to` from then on, and `max_id()` is the `SEARCH lines` MAX optimisation - one
seek, per the class 20 sweep.

**A5 (S6, narrowed)** `limit` on `/lines`, `/can/frames`, `/plot/series` and `/sessions` is
`Query(..., ge=0)`: a negative is a 422, `limit=0` stays the CLI's no-backfill probe
(cli.py:503, SPEC 3.4). The store's upper clamp is untouched.

**A6 (S7)** `/wait` and `/assert` answer 400 `eol applies to send; set send too`. Checked
first: `cli.py:1036` and `:1112` set `body["eol"]` only inside `if send_cmd is not None`, so
no CLI invocation can trip it.

**A7 (S9)** `POST /sessions/stop` acts on `stop_session()`'s result (`None` -> 400 "no
session is running"). The `auto` refusal is kept as a pre-check, and the whole
check-stop-reopen is held under a new `app.state.session_stop_lock`: without it the loser's
stop landed on the automatic session the winner had just reopened and answered 200 with it,
which the test caught. That lock is the one addition beyond the filed fix.

**A8 (S10)** `/ws` closes 1008 (after accept, where the 1013 subscriber-cap refusal already
lives) when `port` names no attached port. SPEC's `/ws` paragraph states the refusal and why
the read endpoints are exempt.

**A9 (S12)** `GET /config` takes `path.exists()` through `asyncio.to_thread`. The session
export handler moved `_export_tmp_dir`, `mkstemp` and `os.close` into a `build()` closure
run by the existing `to_thread` hop; `build()` unlinks its own temp file on any exception so
the handler's cleanup did not have to stay on the loop.

**A10 (D5, D7)** Tests only, in `test_wait_repeat.py`: a write that blocks ten periods is
not followed by a burst (asserted on the gaps between writes, not on a count), and a detach
mid-wait followed by a fresh attach of the same alias still lands the match.

**A11 (D11)** One SPEC sentence in the `/wait` repeat paragraph: concurrent repeats on one
port are serialized only by the port's write lock. A second sentence there records A2's
refusal.

## Revert verification

Each fix reverted by hand in `mcuscope/server.py`, the named test run, then restored from a
known-good copy. Every case failed with the fix out and passes with it in.

| Item | Reverted to | Test | Reverted |
|---|---|---|---|
| A1 | `except PortError` + the old `finally` | `test_a_store_failure_is_counted_and_leaves_no_subscriber_behind` | FAIL |
| A2 | `_encode_wire` pre-check removed | `test_an_unsendable_body_is_refused_before_the_first_write` | FAIL |
| A3 | `eol: Eol = PortConfig.eol`, `eol=entry.eol` | `tests/test_config_ports_eol.py` | FAIL |
| A4 (export) | `id_to = store.max_id()` removed | `test_plot_export_streams_the_window_its_count_guarded` | FAIL |
| A4 (assert) | `id_to = store.max_id()` removed | `test_a_retrospective_assert_judges_every_pattern_over_one_window` | FAIL |
| A5 | `limit: int = 100` / `= 50` / `= 10000` | `test_a_negative_limit_is_refused`, `..._plot_series_limit...`, `test_limit_zero_is_still_the_no_backfill_probe` | FAIL |
| A6 | both guards removed | `test_wait_refuses_an_eol_with_nothing_to_send`, `test_assert_...` | FAIL |
| A7 | pre-check only, no lock | `test_two_concurrent_stops_give_exactly_one_success` | FAIL |
| A8 | 1008 guard removed | `test_ws_refuses_a_port_no_attached_alias_can_satisfy` | FAIL |
| D5 | `next_at = next_at + period_s` | `test_a_blocked_write_is_not_followed_by_a_backfill_burst` | FAIL |
| D7 | `return` instead of `raise PortError` | `test_a_detach_mid_wait_is_counted_and_the_loop_survives_it` | FAIL |

A9 has no behavioural test by design (it needs a slow filesystem); `test_sessions.py` and
`test_config_api.py` stay green and ruff is clean.

Two tests needed a widened window to be deterministic rather than lucky, and both say so in
their docstrings: the concurrent-stop test slows the first `stop_session` by 300 ms so both
requests are certainly inside the handler, and the two window-freeze tests replace
`mcuscope.store.time` with a clock that advances 10 s per `_window_floor` call only (the
frame check keeps the writer, the sessions table and the retention loop on the real clock).

## Not done

- **`test_e2e.py::test_ws_port_filter` now fails and I may not edit that file.** Its second
  half asserts the behaviour A8 replaces: it connects to `/ws?port=ZZZ_nope` and expects a
  silent, empty stream, which is now a 1008 close. The half that matters (the `board` filter
  carrying only `board` rows) is unaffected. The fix is to replace the "filter that matches
  no port" block with an assertion that the connect is refused with 1008 - `test_server_scope.py::test_ws_refuses_a_port_no_attached_alias_can_satisfy`
  is the replacement coverage, so deleting that block is also correct.
- Five `test_wait_repeat.py::test_cli_*` failures are **not from this batch**: another
  agent's in-flight edit to `cli.py:372` (`timeout_ms_option`) makes every `mcu wait`
  invocation raise `TypeError: '<=' not supported between instances of 'int' and 'Context'`.
  Reverting my server.py changes does not clear them; `uv run mcu wait --match x` reproduces
  it with no daemon involved.
- `ruff check .` reports one error in `mcuscope/link.py:19` (`typing.Callable`), also another
  agent's file. `ruff check mcuscope/server.py tests/` is clean.
