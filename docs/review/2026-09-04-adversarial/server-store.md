# Adversarial leg: server.py + store.py

- HEAD: `7a1120f`
- Scope: `host/mcuscope/server.py`, `host/mcuscope/store.py`, `host/mcuscope/daemon.py` and `host/mcuscope/serial_link.py`/`link.py` only where a handler calls into them. `docs/SPEC.md` is the contract.
- Method: registry sweeps for classes 1, 16, 17, 20, 31, 36, 37, 39, 40, 44 run mechanically over the scope; then a target-free read of all 35 routes and the WS handler; then a live probe against a throwaway daemon (`--port 8571 --config /tmp/mcurev/cfg.toml --sim`, `db_path=/tmp/mcurev/capture.db`) and an `EXPLAIN QUERY PLAN` sweep driving the real `Store` on a temp file with no `sqlite_stat1` and two ports.

Counts: 1 HIGH / 4 MED / 7 LOW.

---

## S1 (HIGH) `POST /break` reports success for a break that was never sent

`host/mcuscope/link.py:177-181` (`SerialLink.send_break`), reached from `host/mcuscope/serial_link.py:1115` and `host/mcuscope/server.py:1438`.

The capability probe is `hasattr(self._ser, "send_break")`, but `serial.serialutil.SerialBase` always defines `send_break`, so the guard never fires for any URL handler. pyserial's `socket://` handler implements `_update_break_state` as a no-op log line, so the call returns having sent nothing and without even holding the duration.

Failure scenario, driven:

```
$ POST /break {"port":"sim","ms":250}   ->  {"ok":true}
```

and against real pyserial:

```
serial_for_url("socket://127.0.0.1:<p>").send_break(0.05)  ->  returns in 9.5e-07 s
```

The caller is following SPEC 686 ("Linux magic SysRq over a serial console (break, then one character sent with `eol` `none`)") or intercepting a bootloader. It receives `{"ok": true}`, sends the follow-up character, and the target never saw a break. Neither the duration nor the signal happened, and no field on any surface says so. Note this is the shipped path for every network-attached target: CLAUDE.md's cross-platform mandate says to prefer `socket://` everywhere, and `rfc2217://` (which does carry break out of band) is the only URL scheme that works.

`SourceLink.send_break` (`link.py:272`) returning `True` is deliberate and documented, and is why the `--sim` probe above passes; it is not the defect, but it is why no test can see it (class 27).

Registry class 17 (reported value is the request, not the result), new instance. The `hasattr` line was written for exactly this hazard and does not detect it.

Minimal fix, in `link.py:177`:

```python
    def send_break(self, seconds: float) -> bool:
        # SerialBase always defines send_break, so hasattr proves nothing: pyserial's
        # socket:// handler implements _update_break_state as a no-op log line, returning
        # instantly having sent nothing. Only a native port and the URL schemes that wrap
        # one (or carry break out of band) can actually hold the line low.
        scheme = self.device.split("://", 1)[0] if "://" in self.device else ""
        if scheme and scheme not in ("rfc2217", "spy", "alt", "hwgrep"):
            return False
        self._ser.send_break(seconds)
        return True
```

`serial_link._break_locked` already turns `False` into `PortError("... transport cannot send a break")`, which `POST /break` maps to a 400 naming the transport.

Test that fails without the fix: open a link through `link.open_link("socket://127.0.0.1:<listener>", 115200)` against a throwaway listener and assert `send_break(0.05) is False`; and an e2e assert that `POST /break` on a `socket://`-attached port answers 400 with `cannot send a break` in the message. Both pass today with `ok: true`.

---

## S2 (MED) A `/wait` repeater killed by a non-`PortError` stops resending silently, then hijacks the response

`host/mcuscope/server.py:1919-1932` (`_repeat_send`) and `2024-2029` (`_do_wait`'s `finally`).

`_repeat_send` catches only `PortError`. The first *successful* write is logged (`log=tally.sends == 0`), and `SerialPort.send_raw` therefore calls `store.add_line`, which raises `StoreError` (`store.py:209`, a `RuntimeError`, **not** a `PortError` subclass - verified) whenever the writer is dead or a queued write fails. Two consequences, both confirmed by reproducing the coroutine shape:

1. The repeat task dies on that tick. No further writes are attempted for the rest of the window, `tally.sends` stays frozen at 1, and the response reports `"sends": 1, "send_failures": 0` - which reads as "one write, no failures", not "the spray stopped". SPEC 724 promises the loop continues past a failed write.
2. The `finally` does `repeater.cancel()` then `await repeater` under `suppress(asyncio.CancelledError)` only. A task that already finished exceptionally ignores `cancel()`, so the `await` re-raises `StoreError` out of the `finally`, discarding a `{"status": "match"}` the handler had already built and answering 500 instead.

```
handler returns {"status":"match"}  ->  HANDLER RAISED instead: StoreError capture writer is not running
```

Registry classes 16 (one bad item ends the loop) and 35 (a teardown failure hijacks the exit the result owned).

Minimal fix:

```python
        except Exception:          # not PortError alone: add_line raises StoreError
            tally.failures += 1
```

and in the `finally`:

```python
            repeater.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await repeater
```

(the `suppress(Exception)` still retrieves the exception, so class 39 stays satisfied; log it at `log.warning` before dropping it).

Test that fails without the fix: a fake port whose `send_raw` raises `StoreError` on the logged write and succeeds silently otherwise; drive `POST /wait` with `repeat_ms=20, timeout_ms=200, send_mode="raw"` and assert the response is a normal `timeout` body with `send_failures >= 5` - today it is a 500 with `send_failures: 0`.

---

## S3 (MED) `disconnect_reason` is null on a port that is down and has never tried

`host/mcuscope/serial_link.py:345` (initialised `None`) and `:1237` (`status()`), surfaced by `POST /ports` (`server.py:947`) and `GET /status`.

SPEC 619: "`disconnect_reason` says why a port is down and is null while it is connected." `PortManager.attach` returns as soon as `port.start()` has spawned the reader thread, so the attach response is built before the first open attempt has produced a reason.

Driven:

```
POST /ports {"alias":"ghost","device":"/dev/ttyNOPE9"}
  ->  {"alias":"ghost","connected":false,"held":false,"disconnect_reason":null}
2 s later, GET /ports
  ->  {"alias":"ghost","connected":false,"held":false,"disconnect_reason":"no_device"}
```

The response to the request that created the port is the one an agent reads to decide whether the attach worked, and it is exactly the one that carries the contradictory pair. Class 17: the field reports the moment of the request rather than a result.

Minimal fix: give the never-attempted state a name rather than reusing `null`. In `SerialPort.__init__`, `self.disconnect_reason: str | None = "connecting"`, cleared to `None` in `_on_connect` (which already does `self.disconnect_reason = None`), and add `connecting` to the SPEC 602 enum and the 619 sentence. `hold()` and `_on_error` already overwrite it, so no other site changes.

Test that fails without the fix: attach a device that cannot open and assert the *attach response itself* has `disconnect_reason == "connecting"`, then poll until it becomes `no_device`. Asserting only the second half is what the current suite does.

---

## S4 (MED) `/plot/export` re-evaluates `last_ms` three times, so the row-count guard is not the window streamed

`host/mcuscope/server.py:1581-1620`; `store._window_floor` (`store.py:1282`).

With no `id_to` in force, `_window_floor` anchors a `last_ms` window at `time.time()` on every call. `/plot/export` makes up to three separate store calls with the same `last_ms`: `export_sids_safe` (wide only), `count_plot_export_safe`, then `open_plot_export`. Each computes its own "now", so the window's old edge slides forward between them.

Failure scenario: a busy plot stream, `GET /plot/export?names=temp&last_ms=3600000`. `count_plot_export_safe` answers 999,000 - under `MAX_EXPORT_ROWS` - and takes 400 ms off the loop. `open_plot_export` then re-anchors 400 ms later. Rows at the old edge that the count included are gone from the CSV, rows at the new edge that the count did not see are in it, and if the capture rate exceeds the slide the stream can pass `MAX_EXPORT_ROWS` and be silently cut at `iter_plot_export`'s `cap=1_000_000` - the exact "short CSV byte-indistinguishable from a complete one" the count exists to prevent (the comment at `server.py:1592`). For `format=wide` the sid check is judged over a third window again.

Registry class 44's server-side twin (a relative bound re-evaluated per request across one logical walk) and class 17 (the guard reports a different query than the one answered).

Minimal fix: resolve the relative bound once in the handler, before the first store call, and pass the absolute form to all three:

```python
        if last_ms is not None and id_to is None:
            id_to = store.max_id()      # freeze the window's live edge for this export
```

`_window_floor` then anchors every subsequent call at that id, which is what it was written to do. Same three calls, one window.

Test that fails without the fix: monkeypatch `time.time` in the store to advance 10 s per call, export with `last_ms=5000` over a fixture with rows spread across 20 s, and assert the CSV row count equals the count the handler refused against. Today they differ.

---

## S5 (MED) Retrospective `/assert` judges each pattern over a different window

`host/mcuscope/server.py:2222-2242`.

The retrospective branch issues one `query_lines_safe(match=pat, ...)` per pattern (up to `MAX_ASSERT_PATTERNS` = 16) and then one `count_lines_safe`, all carrying `last_ms` unchanged. Each re-anchors at its own `time.time()`, so a single verdict can span 17 different windows.

Failure scenario: `POST /assert {"expect": [...8 patterns...], "forbid": [...8...], "last_ms": 2000}` on a capture where each match query takes ~120 ms off the loop (a regex over a large window). By the time the eighth forbid runs, its window's floor is ~2 s later than the first expect's. A line that satisfied `expect[0]` is outside the window `forbid[7]` was judged over, and `checked_lines` reports a window neither of them used. The response reads as one authoritative verdict over "the last 2 seconds". This is the same defect class the handler already refuses `session`/`last_ms` on the live branch to avoid (`server.py:2213-2217`): the scope judged is not the scope answered about.

Registry class 44 / class 17.

Minimal fix, same shape as S4, in the retrospective branch before `scope` is built:

```python
        if body.last_ms is not None and id_to is None:
            id_to = store.max_id()      # one window for every pattern and the count
```

Test that fails without the fix: a `time.time` that advances 1 s per store call, a fixture with one line 1.5 s old, `last_ms=2000`, two expect patterns both matching that line; assert both report `matched: true`. Today the second is a miss.

---

## S6 (LOW) `limit <= 0` yields an empty page that claims more rows follow

`host/mcuscope/server.py:1461` (`limit: int = 100`, no `Query` bound) and `1503`; `store.query_lines:1413` / `query_can_frames:1632` clamp with `max(0, min(int(limit), 1000))` and then fetch `limit + 1`.

Driven:

```
GET /lines?limit=0      ->  {"lines": [], "truncated": true}
GET /lines?limit=-5     ->  {"lines": [], "truncated": true}
GET /can/frames?limit=0 ->  {"frames": [], "truncated": true}
GET /sessions?limit=-1  ->  {"sessions": []}
```

`truncated: true` with no rows is a page with no cursor. A pager that advances on the last row's id has nothing to advance to; `cli.py:510` and `:527` happen to guard on `not page` / `isinstance(last, int)`, so the shipped client survives, but the server's own report is self-contradictory and nothing else is obliged to guard.

Minimal fix: bound the parameter where the other integers are bounded - `limit: int = Query(default=100, ge=1, le=1000)` on `/lines`, `/can/frames`, `/plot/series` and `/sessions`, so a nonsense value is a 422 naming the bound rather than a lie. Leave the store's clamp as the second gate (class 19 does not apply: the store clamp exists for internal callers).

Test: assert `GET /lines?limit=0` is a 422, and that no `{"lines": [], "truncated": true}` response is reachable.

---

## S7 (LOW) `eol` and `send_mode` are accepted and silently ignored when nothing is sent

`host/mcuscope/server.py:231-233` (`WaitBody`), `254-256` (`AssertBody`), read only inside `if body.send is not None`.

Driven:

```
POST /wait   {"match":"...","eol":"crlf","send_mode":"raw","port":"sim"}  -> 200, eol never used
POST /assert {"expect":["daemon"],"eol":"crlf"}                           -> 200 pass, eol never used
```

Registry class 31, repeat instance. Both handlers already carry the mirror guards this one is missing (`min_window_ms needs a live window`, `send needs a live window`, `session needs a retrospective window`), so the discipline exists and one field escaped it. The consequence is mild - a caller who believes it set a terminator for a write that never happened - but it is the same "the scope you asked for is not the scope you were answered about" shape.

Minimal fix, in `_do_wait` beside the `since` check and in `_do_assert` beside the `send needs a live window` check:

```python
    if body.eol is not None and body.send is None:
        return _bad_request("eol applies to send; set send too")
```

Test: assert the 400 and its exact wording on both endpoints.

---

## S8 (LOW) The store writer task has no done-callback, so a death outside `stop()` strands every queued write

`host/mcuscope/store.py:527` (`_writer_task = asyncio.create_task(self._writer())`), `569` (`_fail_queued`), `564` (its only caller).

`_fail_queued`'s own docstring states the mechanism: a writer that stops draining leaves queued `_WriteReq` futures that nobody completes, and `SerialPort._store_rx_batch` awaits exactly those. It is wired only into `stop()`. `_writer` is well guarded against insert and commit failures, but any exception from outside those guards (`_broadcast`, `_resolve_drain`, `item.future.set_result`) ends the task with the queue still full of unresolved futures, and every awaiter hangs until the loop closes. `writer_alive` correctly goes false so `/status` reveals it and new `submit_line` calls fail fast; only the already-queued ones hang.

Minimal fix:

```python
        self._writer_task = asyncio.create_task(self._writer())
        # A writer that dies outside stop() leaves queued futures nobody resolves, and
        # _store_rx_batch awaits exactly those (see _fail_queued).
        self._writer_task.add_done_callback(
            lambda t: self._fail_queued("store writer exited") if not t.cancelled() else None
        )
```

Test: monkeypatch `_broadcast` to raise, submit two lines, and assert both futures resolve with `StoreError` rather than hanging (with a `wait_for` so the test fails by timeout without the fix).

---

## S9 (LOW) `POST /sessions/stop` is a check-then-act across an await

`host/mcuscope/server.py:1282-1296`.

The handler reads `store.active_session()`, refuses if it is `None` or `auto`, then `await store.stop_session()`. `_session_lock` is inside the store, not around the handler's check, so two concurrent stops both pass the guard; the loser's `stop_session` finds nothing active and returns `None`, and the handler answers `200 {"session": null}` - a success envelope for an operation that did not happen, on the endpoint whose whole purpose is to keep `session start`/`session stop` a matched pair.

Registry class 37, repeat instance (the read-modify-write spans the `await`).

Minimal fix: act on the result rather than the pre-check.

```python
        session = await store.stop_session()
        if session is None:
            return _bad_request("no session is running")
```

with the `auto` refusal kept as the pre-check (it is advisory, not a race). Test: two concurrent `POST /sessions/stop` against one named session; assert exactly one 200 and one 400, and that no response has `"session": null`.

---

## S10 (LOW) `/ws?port=<unknown alias>` upgrades and then delivers nothing forever

`host/mcuscope/server.py:1676`.

Driven: the handshake completes with `101 Switching Protocols` for `?port=typo`. `store.subscribe(port)` takes the filter verbatim, and no row ever matches. The client sees a healthy socket carrying only keepalives.

`/wait` and `/assert` resolve the alias and answer 400 for exactly this reason, and the `SendMode`/`Chan` comment at `server.py:215` states the principle ("a plausible negative answer to a typo is worse than an error"). The read endpoints (`/lines?port=`, `/can/frames?port=`, `/plot/series?port=`) are legitimately exempt - a detached port's lines are still in the capture, so an unattached alias is a valid historical scope - but `/ws` is live-only, so no such alias can ever be right.

New class candidate: **a live-only surface must validate a scope that only a live object can satisfy, even where its retrospective siblings cannot.**

Minimal fix, before `websocket.accept()`:

```python
        if port is not None and websocket.app.state.ports.get(port) is None:
            await websocket.close(code=1008)
            return
```

Test: assert the socket closes with 1008 for an unattached alias and opens for an attached one.

---

## S11 (LOW) `/plot/export` cannot be scoped to a port

`host/mcuscope/server.py:1581`; `store._export_where:1817` has no port term. SPEC 1485 lists `names, last_ms, id_to, format` and no `port`; SPEC 1483 says "Pass `port=` on `/plot/channels` and `/plot/series` to scope to one board"; SPEC 1481 states the collision.

So the code matches the contract, and this is a SPEC gap rather than a code defect - filed because the one endpoint that writes a file the user keeps is the only one with no way to resolve the collision SPEC 1484 acknowledges. On a two-board bench both declaring `temp`, `mcu plot export --name temp` silently interleaves both boards' samples into one CSV column, and `format=wide` then fails with `wide export requires all channels to share one stream`, which names the wrong cause.

Minimal fix: add `port: str | None = None` to the handler and a `port` term to `_export_where` (via `_window_terms(port_col="l.port")`, which already supports it), and amend SPEC 1483/1485. Test: two ports each with a `temp` channel; assert `?names=temp&port=a` returns only port a's rows.

---

## S12 (LOW) Filesystem syscalls on the event loop in handlers whose neighbours are offloaded

`host/mcuscope/server.py:1036` (`path.exists()` in `GET /config`, one line after `load_config` was sent to a thread), `1340-1345` (`tempfile.mkstemp` and `os.close` in `GET /sessions/{ref}/export`), `2041` (`Path(db_path).parent.is_dir()` in `_export_tmp_dir`, called from that handler).

Class 1's invariant names filesystem work explicitly. Each of these is one syscall on local disk and costs nothing there; the case for fixing them is that `db_path` and the config path may both be on a network mount, where a stat can block for the mount's timeout and freeze every WS feed and serial callback with it. The same handler already offloads `load_config` and `export_session_db` on that reasoning.

Minimal fix: fold `path.exists()` into the `to_thread(load_config, path)` hop (return a `(config, exists)` pair or call `to_thread(path.exists)`), and move `mkstemp`/`os.close`/`_export_tmp_dir` into the existing `asyncio.to_thread` that already runs `export_session_db`.

No test proposed: a behavioural test needs a slow filesystem. Verdict is a code-shape rule, and the sweep is the grep in the class 1 list below.

---

# Sweep verdict lists

## Class 1 - blocking work on the event loop or default executor

`grep -rn "run_in_executor(None" host/mcuscope` -> 1 line, a comment in `serial_link.py:38`. No executable line. **Complies.**

Site count: 35 route decorators (34 in `_register_routes` plus the `/` redirect in `_mount_webui`), plus the `/ws` pump. Per route, the store/os/serial calls it makes:

- `/` redirect - no I/O. complies.
- `GET /status` - `db_size_bytes()` (2 `os.path.getsize`), `content_bytes()` (3 pragmas), `active_session()` (indexed, `SCAN sessions USING INDEX idx_sessions_active` + LIMIT 1), `max_db_bytes()`, counters, `pt.status()` per port. exempt-because: constant-cost pragmas and one indexed row; measured plans below.
- `POST /shutdown` - no I/O. complies.
- `GET /ports` - in-memory. complies.
- `POST /ports` - `ports.attach` -> `validate_device` (regex), `prime_plot_defs` (offloaded match query). complies.
- `DELETE /ports/{alias}` - `port.stop()` joins via the private `_join_pool`. complies.
- `POST /ports/{alias}/reconnect`, `POST /ports/{alias}/disconnect` - as above. complies.
- `GET /devices` - `asyncio.to_thread(_enumerate_devices)`. complies.
- `GET /config` - `to_thread(load_config)`; **`path.exists()` on the loop -> violates (S12)**; `resolve_db_path` is string work.
- `PUT /config/server|storage|update|plotjuggler|ports` - every save via `to_thread`; `store.set_*` are in-memory; `load_config` in `/config/ports` via `to_thread`. complies.
- `GET /plotjuggler` - in-memory. complies.
- `PUT /plotjuggler` - `to_thread(pj.configure)`. complies.
- `GET /sessions` - `list_sessions_safe` offloaded; the `name=` branch uses `resolve_session` (indexed) + `count_lines_safe` (offloaded). complies.
- `POST /sessions` - `start_session` runs its INSERT on the loop connection under `_session_lock`. exempt-because: single writer on the loop by design (ARCHITECTURE), one row.
- `POST /sessions/stop` - as above. exempt.
- `DELETE /sessions/{id}` - `get_session` (PK seek), `max_id()`, `delete_range` (chunked, `_RETENTION_CHUNK` = 5000 per chunk with `await asyncio.sleep(0)` between), `delete_session` (PK delete). exempt-because: the chunk bound is the class-1 fix already in force for retention.
- `GET /sessions/{ref}/export` - `to_thread(export_session_db)`; **`mkstemp`/`os.close`/`_export_tmp_dir` on the loop -> violates (S12)**.
- `POST /purge` - `resolve_session`/`session_span`/`last_id_before_ts`/`max_id` all indexed seeks (plans below); `count_lines_safe` offloaded; `delete_range` chunked. complies.
- `POST /send`, `POST /break`, `POST /cmd` - `to_thread(_write_bytes)` / `to_thread(_break_locked)`; `add_line` queues. complies.
- `GET /lines` - `regex.compile` of the user pattern **on the loop** before the query. exempt-because: compilation is bounded by `MAX_MATCH_LEN` = 200 and is what makes a bad pattern a 400 rather than a 500 (the comment at `server.py:1476` records this); matching itself is offloaded. `query_lines_safe` offloads only when `match` is present; the match-free plans are all bounded seeks (below).
- `GET /can/frames` - `query_can_frames_safe` offloaded. complies.
- `GET /plot/channels` - `query_plot_channels_safe` offloaded; `plot_channel_meta()` is a dict merge. complies.
- `GET /plot/series`, `GET /plot/export` - all three store calls offloaded; `open_plot_export` streams on a private connection except for `:memory:`. complies.
- `POST /wait`, `POST /assert` - `regex.compile` on the loop (same exemption); every scan through `match_executor`; the retrospective branch uses `query_lines_safe`/`count_lines_safe`. complies.
- `POST /marker` - `add_line` queues. complies.
- `WS /ws` - `json.dumps` of at most `WS_BATCH_MAX` = 500 rows on the loop. exempt-because: the cap is what bounds it, and it is the serialization the frame is made of.

## Class 16 - one bad item ends the loop

Loops over external input in scope: 6.

- `store._writer` batch loop (`store.py:635`) - complies. Batched insert failure falls back to row-by-row, a failing fallback fails that batch's callers and continues, a commit failure rolls back and continues, and the `finally` releases the drain barrier on every exit.
- `store._fail_queued` drain loop (`:580`) - complies; per-item, no raising body.
- `store.iter_plot_export` fetchmany loop (`:1921`) - exempt-because: rows come from SQLite, not external input; a failure is connection-level, not per item.
- `server._repeat_send` (`:1919`) - **violates (S2)**: `except PortError` charges only that class to the tick; a `StoreError` from the logged write ends the loop.
- `server.pump` / `watch` (`:1692`, `:1734`) - complies; a send failure is a dead peer and correctly ends the connection.
- `server._do_assert` / `_do_wait` batch loops (`:2249`, `:1998`) - complies; `_scan_batch`/`_search_batch` raise only `MatchBudgetExceeded`, which is a whole-call verdict by design and mapped to a 400.
- Mirror question (a guard that must still recognise the non-per-item errors): `_repeat_send` after the S2 fix must not swallow `asyncio.CancelledError` - the proposed `except Exception` does not, since `CancelledError` is a `BaseException` on 3.8+. Stated so the fix does not reintroduce it.

## Class 17 - reported value is the request, not the result

Reported fields on the scope's health/result surfaces: 42.

`GET /status` (21): `version`, `pid` (`os.getpid()`), `uptime_s`, `db_path`, `db_size_bytes` (read back), `db_content_bytes` (read back), `db_max_bytes` (from `store.max_db_bytes()`, the cap in force), `lines_trimmed`, `write_errors`, `writer_alive` (task state), `ws_dropped`, `capture` (from the store), `session` (from the sessions table), `update` (from the checker's cache, and its docstring states it is the previous answer), `plotjuggler.enabled`/`.dest` (from the streamer, not config), `ports[]` - all **comply**.

Port `status()` (14): `alias`, `device`, `baud`, `eol`, `connected`, `held`, `resolved_device`, `description`, `lines_rx`, `lines_tx`, `write_failures`, `last_write_error`, `last_write_error_ts`, `write_failing_since` - all read back from state the reader/writer set. **Comply.** `disconnect_reason` - **violates (S3)** for the never-attempted state.

Result envelopes (7): `POST /send` `{"ok": true}` complies (a failed write raises). `POST /break` `{"ok": true}` - **violates (S1)**: the transport answered `True` without sending. `POST /cmd` returns `send_command`'s own result. `POST /purge` `deleted` comes from `delete_range`'s row count. `POST /wait` `sends`/`send_failures` count actual writes (the stale-read on the match path is documented and bounded by one tick). `PUT /plotjuggler` reads back from `pj`. `PUT /config/*` `restart_required` compares saved against running. **Comply.**

`/plot/export`'s refusal count and `/assert`'s `checked_lines` describe a window other than the one answered - filed as S4/S5 under class 44, which is class 17's clock-relative face.

## Class 20 - non-sargable bound on a hot query

Every statement reachable from a handler, explained against a real `Store` on a temp file, `sqlite_stat1` absent (confirmed 0 rows), two ports. 40 distinct statements; the ones with a plan worth stating:

- `/lines` plain: `SCAN lines` - exempt-because: `ORDER BY id DESC LIMIT n` on the rowid is a backwards PK walk that stops at the limit, not a table read.
- `/lines?port=`: `SEARCH lines USING INDEX idx_lines_port_id (port=?)`. complies.
- `/lines?chan=`: `SEARCH ... idx_lines_chan_id (chan=?)`. complies.
- `/lines?port=&chan=`: `SEARCH ... idx_lines_chan_id (chan=?)` - the `+port` de-optimisation holding. complies.
- **`/lines?chan=a&chan=b`** (the multi-value branch, which no prior sweep covered): `SEARCH ... idx_lines_chan_id (chan=?) | USE TEMP B-TREE FOR ORDER BY`, and this query runs **inline on the event loop**. Measured at 1M rows, two populated channels, no `sqlite_stat1`: **0.69 ms** (against 0.41 ms for the single-chan form, 0.03 ms for an empty pair). SQLite drives each `IN` value's index range in descending id order and feeds the sorter incrementally, so the LIMIT terminates it early and no full materialization happens. **Complies (measured).** Recorded because the plan text alone reads as the class-20 shape and is not.
- `/lines?last_ms=`: floor query `SEARCH ... COVERING INDEX idx_lines_ts (ts>?)`, main query `SEARCH lines USING INTEGER PRIMARY KEY (rowid>?)`. complies (the derived id floor is what makes it a seek).
- `/lines?port=&last_ms=`: `SEARCH ... idx_lines_port_id (port=? AND id>?)`. complies.
- `/lines?since_ts=`, `?since_id=`, `?id_from=&id_to=`: PK range seeks. complies.
- `/lines?match=`: `SCAN lines` - exempt-because: a regex filter has no index and the query is offloaded to `match_executor` with a per-call and per-query budget.
- `/lines?match=&port=`: `SEARCH ... idx_lines_port_id (port=?)`. complies.
- `count_lines` plain: `SCAN lines USING COVERING INDEX idx_lines_ts` - exempt-because: a whole-table count, and offloaded.
- `count_lines` with a range / `port`+`chan` / `last_ms`: PK range, `idx_lines_chan_id`, covering `idx_lines_ts`. complies.
- `/can/frames` plain and `?port=`: `SCAN cf | SEARCH l USING INTEGER PRIMARY KEY (rowid=?)` - the `CROSS JOIN` pinning the drive order, which is the class-20 fix. complies.
- `/can/frames?last_ms=`, `?since_id=`: `SEARCH cf USING INTEGER PRIMARY KEY (rowid>?)`. complies.
- `/can/frames?bus=&id=`: `SEARCH cf USING INDEX idx_can_id_line (can_id=?)`. complies.
- `/plot/channels` and `?port=`: co-routine over `idx_plot_name_line` plus a temp b-tree for ORDER BY - exempt-because: the registry's own carve-out, an aggregate over every point of every channel cannot be a bounded seek, and it is offloaded. `?port=` adds `SEARCH li USING INTEGER PRIMARY KEY` and does not make it worse.
- `/plot/series` and `?port=`, `?last_ms=`: `SEARCH pp USING INDEX idx_plot_name_line (name=?)` (`name=? AND line_id>?` with `last_ms`). complies.
- `/plot/series?decimate=`: three temp b-trees - exempt-because: `ROW_NUMBER() OVER (PARTITION BY ...)` needs them by construction, and it is offloaded.
- `export_sids`: `SEARCH pp USING INDEX idx_plot_name_line (name=?) | USE TEMP B-TREE FOR DISTINCT` - exempt-because: DISTINCT over the selection, offloaded, and the row cap bounds the selection.
- `count_plot_export`: `SEARCH pp USING COVERING INDEX idx_plot_name_line (name=?)`. complies.
- `list_sessions`: `SCAN s | CORRELATED SCALAR SUBQUERY | SEARCH l USING INTEGER PRIMARY KEY (rowid>? AND rowid<?)` - the `COALESCE` fix holding. complies, and offloaded.
- `active_session`: `SCAN sessions USING INDEX idx_sessions_active` + LIMIT 1. complies.
- `resolve_session` by name: `SEARCH sessions USING INDEX idx_sessions_name (name=?)`. complies.
- `get_session`: PK seek. complies.
- `max_id`: `SEARCH lines` (the MAX optimisation). complies.
- `last_id_before_ts`: `SEARCH lines USING COVERING INDEX idx_lines_ts (ts<?)`. complies.
- `retention_floor_id`: `SCAN sessions ... LIMIT 1 OFFSET ?` - exempt-because: `sessions` is bounded by how many runs a person names.
- `_estimated_rows`: `SCAN lines USING COVERING INDEX idx_lines_ts` - exempt-because: documented, runs only while over cap.
- retention/size/purge delete chunks: PK seek, covering `idx_lines_ts (ts<?)`, and PK range respectively. complies.

No finding.

## Class 31 - a field the model accepts and the path never reads

16 request models plus the query parameters of 10 GET routes.

Bodies: `PortAttach` (alias, device, serial_number, baud, eol - all read); `SendBody` (port, line, eol); `CmdBody` (port, cmd, timeout_ms, eol); `BreakBody` (port, ms); `PurgeBody` (session, before_ts, id_from, id_to, all, dry_run); `MarkerBody` (port, text); `SessionBody` (name, note); `ConfigServerBody` (host, port); `ConfigStorageBody` (db_path, retention_days, max_db_bytes, min_sessions, auto_session); `ConfigUpdateBody` (check); `PlotJugglerBody` (enabled, dest); `ConfigPlotJugglerBody` (enabled, dest); `ConfigPortEntry` (alias, device, serial_number, baud, autoconnect, identify, eol); `ConfigPortsBody` (ports) - every field read on every branch that accepts it. **Comply.**

- `WaitBody`: port, match, timeout_ms, send, send_mode, chan, since, repeat_ms all read; **`eol` violates (S7)** - read only inside `if body.send is not None`, accepted otherwise. `send_mode` is the same shape but is a closed domain with a meaningful default and no observable effect when `send` is null; folded into S7's fix.
- `AssertBody`: port, expect, forbid, timeout_ms, min_window_ms, send, send_mode, chan all read; `session` and `last_ms` are refused on the live branch (the class-31 fix in force); **`eol` violates (S7)** on both counts - ignored when `send` is null, and ignored outright on the retrospective branch where `send` is already refused.

Query parameters: `/lines` (port, chan, match, since_id, since_ts, last_ms, session, id_to, limit, order - all read); `/can/frames` (port, bus, id, last_ms, since_id, session, id_to, limit); `/plot/channels` (port); `/plot/series` (name, port, last_ms, since_id, session, id_to, limit, decimate); `/plot/export` (names, last_ms, session, id_to, format); `/sessions` (limit, name); `/sessions/{id}` DELETE (data); `/ws` (port); `/devices`, `/config`, `/plotjuggler`, `/status` (none). **All read; comply.** `/plot/export`'s missing `port` is the inverse (a scope the sibling offers and this one has no way to express) and is S11.

## Class 36 - a periodic catch-up loop without a burst cap

`grep -n "while "` over both files, filtered to loops whose condition or body compares a schedule variable against now: 2.

- `store._retention_loop` (`:2189`) - **exempt-because**: a fixed `await asyncio.sleep(_SIZE_CHECK_S)` per pass with no schedule variable, so a suspend produces exactly one tick, never a backlog.
- `server._repeat_send` (`:1919`) - **complies**: `next_at = max(next_at + period_s, loop.time())` re-anchors past a stall instead of backfilling, and the comment cites this class. Verified the re-anchor is the `max` form, not `next_at += period_s`.

The remaining `while` hits are drain loops (`_writer`, `_fail_queued`, `next_batch`, the WS coalescer, the delete chunkers) whose backlog is capture data that must not be dropped - the registry's stated exemption.

## Class 37 - an async read-modify-write spanning an await without a lock

Every `async def` in `store.py` (28) plus the port manager's mutating methods (5), plus the handlers that read store state and then act on it.

`store.py`: `start`, `_initial_sweep`, `stop`, `_writer`, `submit_line`, `add_line`, `drain_writes`, `list_sessions_safe`, `_offload`, `count_lines_safe`, `query_lines_safe`, `query_can_frames_safe`, `query_plot_channels_safe`, `query_plot_series_safe`, `export_sids_safe`, `count_plot_export_safe`, `open_plot_export`, `_trim_oldest`, `sweep_tick`, `_retention_loop` - **comply**: read-only, or (for `submit_line`/`_writer`) the queue is the serialization.

Bulk deleters, the registry's named sub-sweep: `delete_range` (`:2031`), `_sweep_size_async` (`:2101`), `_sweep_retention_async` (`:2174`) - all three take `_sweep_lock`. `_sweep_size_locked`, `_sweep_retention_locked`, `_trim_oldest` run under a caller's lock. `_delete_oldest_chunk`, `_delete_expired_chunk`, `_delete_range_chunk` are sync and only reachable from those. **3 of 3 comply.**

Sessions-table open/close mutations, the registry's other named sub-sweep: `start_session` (`_session_lock` + `drain_writes` barrier before sampling `_next_id`), `stop_session`, `_stop_session_locked` (lock held by caller) - **comply**. `delete_session` (`store.py:1145`) is sync, so no await spans it, but it is called from `DELETE /sessions/{id}` **after** an `await store.delete_range(...)`, outside `_session_lock`. **exempt-because**: `delete_session` is a single committed `DELETE` and the interleavings reachable through `_session_lock` (a concurrent `start_session` closing the same row first) leave the label deleted and a valid session open, not an overlapping or stranded pair. Recorded rather than filed.

Handlers: `POST /sessions/stop` - **violates (S9)**. `POST /ports/{alias}/disconnect` (`server.py:989-993`) - complies: `ports.get(alias)` after `await ports.hold(alias)` runs with no intervening yield, and `detach` contends for the same manager lock. `DELETE /sessions/{id}` sampling `store.max_id()` before `await delete_range` - complies: rows captured during the delete take ids above the sampled bound and are correctly outside the session. `PUT /config/storage` and `PUT /config/update` applying live state *after* releasing `config_write_lock` - **complies**: `asyncio.Lock.__aexit__` does not yield, so the apply is in the same non-suspending stretch as the release and cannot interleave with a waiter's save. (Checked explicitly; this reads like a violation and is not.)

## Class 39 - a raced task orphaned by the exceptional exit

`grep -n "create_task\|ensure_future"` over the scope: 6 sites.

- `store.py:527` `_writer_task` - not raced; consumed in `stop()` via `asyncio.wait` + `await` under suppress. **exempt** from the race clause, but see S8 for its queue.
- `store.py:530` `_initial_sweep_task`, `:531` `_retention_task` - not raced; both cancelled and awaited in `stop()`. **comply.**
- `server.py:1741` `pump_task`, `:1742` `watch_task` - raced with `asyncio.wait(FIRST_COMPLETED)`. Exits: the race returning (either half), a `CancelledError` out of `asyncio.wait`, any exception. All three land in the `finally`, which cancels both and awaits each under `suppress(asyncio.CancelledError, Exception)`. **Comply.**
- `server.py:1979` `repeater` - raced against the wait loop implicitly. Exits: the `match` return, the `timeout` return, the `_bad_request` return from a failed non-repeat send (unreachable in this branch), a `MatchBudgetExceeded` raise, and handler cancellation. All land in the `finally`, which cancels and awaits it - so the task is always **consumed** and no traceback is orphaned. **Complies on this class**; the defect is that the retrieved exception is then re-raised into the caller's result (S2, class 35).

## Class 40 - multi-attribute state shared between the loop and a worker thread, torn on read

`grep -n "to_thread"` over the scope plus the daemon's threads: 12 `to_thread` sites and 2 owned threads.

- `server.py:380`, `:1182` `pj.configure` - writes one immutable `(socket, sockaddr)` tuple plus `dest`/`enabled` under the config write lock; the class-40 fix in force. **complies.**
- `server.py:1002` `_enumerate_devices` - writes no instance state; `cached_comports()` owns its own cache. **complies.**
- `server.py:1035`, `1084`, `1104`, `1135`, `1155`, `1197`, `1227` `load_config`/`save_*` - module functions writing files, no shared attributes. **comply.**
- `server.py:1354` `store.export_session_db` - opens its own connection, writes no `Store` attribute. **complies.**
- `store.py:1514`, `1603` `run_in_executor(match_executor(), ...)` -> `_read_on_private_conn` / `_query_lines_threadsafe`: both open and close a private connection and write no instance state. **comply.**
- `serial_link._write_bytes` (worker thread) - writes `self._write_health` as one immutable value, one store, read once in `status()`. **complies** (this is the class's original fix site).
- `serial_link._break_locked` (worker thread) - writes nothing; re-reads `self._link` inside `_write_lock`. **complies.**
- The reader thread - passes every state change through `_post` onto the loop, so `connected`, `disconnect_reason`, `resolved_device`, `description` are all loop-side writes. **Complies** (S3 is a value question, not a tearing one).

## Class 44 - a relative bound re-evaluated on every page of a paged walk

The client-side sweep (`id_to`, `since_id`, `LINES_PAGE` in `cli.py` and `api.js`) is out of this leg's scope. The server-side twin - one logical answer built from more than one store call carrying the same clock-relative bound - has 4 sites:

- `GET /plot/export` with `last_ms` and no `id_to`: `export_sids_safe` + `count_plot_export_safe` + `open_plot_export`, three independent `time.time()` anchors. **violates (S4).**
- `POST /assert` retrospective with `last_ms`: up to 16 `query_lines_safe` calls plus one `count_lines_safe`, each its own anchor. **violates (S5).**
- `GET /lines`, `/can/frames`, `/plot/series` with `last_ms`: one store call per request. **comply** (the paging is the client's, and `_window_floor`'s `id_to` anchor is what a paging client must send).
- `POST /purge` with `before_ts`: an absolute bound, and `last_id_before_ts` resolves it to an id before the delete. **complies.**

---

# The two questions

**1. What am I least confident about, and what did I do to recheck it?**

The claim that `/lines?chan=a&chan=b` is *not* a class-20 finding. The plan text (`SEARCH ... (chan=?) | USE TEMP B-TREE FOR ORDER BY`) is exactly the shape the registry documents for `/can/frames?port=`, the query runs inline on the event loop, and the multi-value branch of `_window_terms` has never been plan-checked - so my first read filed it as a HIGH. I re-drove it rather than re-reading it: a 1M-row table, no `sqlite_stat1`, two channels each holding half the rows, three runs, best-of. 0.69 ms against 0.41 ms for the single-chan form. SQLite seeks each `IN` value's index range in descending id order and feeds the sorter incrementally, so the LIMIT terminates it and nothing is materialized. Recorded as complies-with-measurement in the class 20 list, because the next reader will reach the same wrong conclusion from the plan text alone.

Second candidate, also rechecked and also dropped: `PUT /config/storage` applying live state after releasing `config_write_lock`. Two concurrent PUTs looked able to leave the file and the running config disagreeing permanently. They cannot: `asyncio.Lock.__aexit__` awaits a coroutine that never suspends, so the release and the apply are one non-yielding stretch and the waiter cannot interleave between them. Stated in the class 37 list so it is not re-filed next round.

Not verified, and I am saying so rather than counting it done: S1's fix. I confirmed the *defect* against real pyserial (`socket://` `send_break` returns in 9.5e-07 s having sent nothing) but I have no hardware here, so the proposed scheme allowlist is untested against `rfc2217://` and against a native `/dev/ttyACM0`, where `send_break` must still work and still hold for the full duration. That needs the bench leg.

**2. What should have been checked that nobody asked for?**

The `--sim` transport as a test double, not as a demo. `SourceLink.send_break` returns `True` "so the sim exercises the success path end to end" - and that decision is precisely why S1 could ship: the whole e2e suite drives `/break` through a link that reports success unconditionally, so no test could distinguish a break that landed from one that evaporated. Class 27 names this shape and the sweep for it was not on my list. Worth a round of its own: enumerate every `Link` method where `SourceLink` answers more optimistically than `SerialLink` would, and rule each one on whether a test could tell.

Related and also unasked: `/break` writes a sys row (`port <alias>: break <ms> ms`) into the capture on the success path only. Because success is currently unconditional over `socket://`, the capture records breaks that never happened - a log line an operator will later read as evidence. Any fix to S1 must move that row behind the real result, not just behind the return.
