"""The size cap (`max_db_bytes`): what the trim spends first, that it converges, and that
pages go back to the filesystem (SPEC 3.2)."""

from __future__ import annotations

import asyncio
import logging
import time

from mcuscope.store import Store

# -- size-capped retention (SPEC 3.2) --------------------------------------------------


async def _fill(store: Store, n: int, payload: str, prefix: str = "") -> None:
    futs = [
        await store.submit_line(
            ts=time.time(), port="t", dir="rx", chan="debug", seq=None,
            raw=f"{prefix}{i} {payload}",
        )
        for i in range(n)
    ]
    for fut in futs:
        await fut


def test_size_cap_trims_oldest_and_converges(tmp_path) -> None:
    # The cap must remove the OLDEST lines, keep the newest, and settle: SQLite reuses
    # freed pages rather than shrinking, so a cap measured against the file size would
    # still read "too big" after a trim and keep deleting until the capture was empty.
    async def run() -> None:
        store = Store(str(tmp_path / "cap.db"))
        await store.start(retention_days=7, max_db_bytes=0)
        try:
            await _fill(store, 4000, "x" * 200)
            cap = store.content_bytes() // 2
            store.set_max_db_bytes(cap)

            assert await store._sweep_size_async() > 0
            assert store.content_bytes() <= cap
            assert store.lines_trimmed > 0

            rows, _ = store.query_lines(limit=1, order="desc")
            assert rows[0]["raw"].startswith("3999 "), "newest line must survive a trim"
            rows, _ = store.query_lines(limit=1, order="asc")
            assert not rows[0]["raw"].startswith("0 "), "oldest lines should have gone"

            # Converged: an immediate re-check must not keep eating the capture.
            remaining = store.max_id() and len(store.query_lines(limit=1000)[0])
            assert await store._sweep_size_async() == 0
            assert remaining > 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_trims_into_protected_sessions_rather_than_being_ignored(tmp_path, caplog) -> None:
    # The forced trim: when the protected sessions ALONE exceed the cap, the size sweep has
    # a floor it cannot delete below and would otherwise return having freed nothing, on
    # every sweep, forever - a cap that is not a bound, and silent about it. This is the
    # one branch of the sweep the suite never drove.
    async def run() -> None:
        store = Store(str(tmp_path / "forced.db"))
        await store.start(retention_days=7, max_db_bytes=0, min_sessions=5)
        try:
            # One session holding everything, and fewer sessions than the floor, so
            # retention_floor_id protects every line in the capture.
            await store.start_session("the-only-run")
            await _fill(store, 4000, "x" * 200)
            assert store.retention_floor_id() is not None
            cap = store.content_bytes() // 2
            store.set_max_db_bytes(cap)

            with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
                dropped = await store._sweep_size_async()
            assert dropped > 0, "the cap was silently unenforceable inside a protected session"
            # Specifically the forced branch, not an ordinary trim that happened to suffice:
            # deleting protected data is loud on purpose, and this is the evidence of it.
            assert any("protected session(s) alone exceed" in r.message for r in caplog.records)
            assert store.content_bytes() <= cap
            # The newest lines are still the ones kept, protected or not.
            rows, _ = store.query_lines(limit=1, order="desc")
            assert rows[0]["raw"].startswith("3999 ")
            # And it converges rather than eating the rest of the session on the next pass.
            assert await store._sweep_size_async() == 0
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_spends_unprotected_lines_first_and_forces_only_the_remainder(
    tmp_path, caplog
) -> None:
    # The ordering half of the floor, and the arithmetic of the forced pass. The sibling
    # above has nothing unprotected, so it cannot see either: here the cap needs more than
    # the ambient lines can pay, so the first pass spends all of them, stops at the floor,
    # and the forced pass takes only the SHORTFALL out of the protected run. Trimming
    # `want` again there would silently eat a second helping of protected data.
    #
    # (Replaces a test in test_sessions.py that drove this with `want` below the ambient
    # count, where the floor changed nothing and removing it left the test green.)
    async def run() -> None:
        store = Store(str(tmp_path / "ordering.db"))
        await store.start(retention_days=7, max_db_bytes=0, min_sessions=1)
        try:
            await _fill(store, 500, "x" * 200, prefix="ambient")
            await store.start_session("keep-me")
            protected_from = store.max_id()
            await _fill(store, 1500, "x" * 200, prefix="protected")
            assert store.retention_floor_id() is not None

            used = store.content_bytes()
            store.set_max_db_bytes(used // 2)   # more than the 500 ambient lines can pay
            with caplog.at_level(logging.WARNING, logger="mcuscope.store"):
                assert await store._sweep_size_async() > 0

            rows, _ = store.query_lines(limit=1000, order="asc")
            assert not any(r["raw"].startswith("ambient") for r in rows), \
                "unprotected lines must be spent before protected ones"
            # That the cap is a hard bound is the sibling's invariant; this one is about
            # which lines pay for it, so it only asks that the trim moved towards the cap
            # (one pass leaves partly-filled pages behind, which is why it is not `<=`).
            assert store.content_bytes() < used
            assert any("protected session(s) alone exceed" in r.message for r in caplog.records)
            # The shortfall only: over half the protected run survives a cap that asked for
            # roughly a quarter of the capture beyond what the ambient lines covered.
            surviving = store.count_lines(id_from=protected_from)
            assert surviving > 750, f"the forced pass overshot the target: {surviving} of 1500"
        finally:
            await store.stop()

    asyncio.run(run())


def test_a_purge_running_beside_the_size_sweep_is_not_paid_for_twice(tmp_path) -> None:
    # `delete_range` deleted in yielding chunks WITHOUT `_sweep_lock`, which is the same
    # hole the lock exists for on the sweeps: both compute how much to remove up front and
    # then remove it a chunk at a time, so the sweep spent a `want` measured before the
    # purge freed most of it, and the two together ate far more of the capture than either
    # was asking for. Every purge path is affected (POST /purge and
    # DELETE /sessions/{id}?data=true), and the 60 s tick makes the overlap routine.
    #
    # The interleave is deterministic, not lucky: both loops chunk at _RETENTION_CHUNK and
    # yield between chunks, so the work here is sized to take several chunks each. The purge
    # is first in the gather, so it is the one that holds the lock; the sweep then measures
    # a capture that has stopped moving.
    async def run() -> None:
        store = Store(str(tmp_path / "purgesweep.db"))
        await store.start(retention_days=7, max_db_bytes=0)
        try:
            await _fill(store, 30000, "x" * 200)
            used = store.content_bytes()
            store.set_max_db_bytes(used // 2)

            purged, trimmed = await asyncio.gather(
                store.delete_range(10001, 20000), store._sweep_size_async()
            )
            remaining = store._estimated_rows()
            assert purged and trimmed, "the scenario did not exercise both deleters"
            assert store.content_bytes() <= store.max_db_bytes(), "the cap is not a bound"
            # Measured: 13419 of the 30000 rows survive when the two serialise, 8449 when
            # the sweep spends its full pre-purge target on top of the purge.
            assert remaining > 11000, (
                f"{30000 - remaining} rows went for a purge of {purged} and a trim of "
                f"{trimmed}: the sweep spent a target the purge had already met"
            )
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_cap_off_by_default_never_trims(tmp_path) -> None:
    # The default must not drop anything: age retention is the only bound unless the
    # owner opts in to a size cap.
    async def run() -> None:
        store = Store(str(tmp_path / "nocap.db"))
        await store.start()
        try:
            await _fill(store, 500, "y" * 200)
            assert await store._sweep_size_async() == 0
            assert store.lines_trimmed == 0
            rows, _ = store.query_lines(limit=1, order="asc")
            assert rows[0]["raw"].startswith("0 ")
        finally:
            await store.stop()

    asyncio.run(run())


def test_db_size_counts_the_wal(tmp_path) -> None:
    # Under WAL a large share of a fast capture sits in the -wal sidecar; reporting only
    # the main file would under-report what the capture is using.
    async def run() -> None:
        path = tmp_path / "wal.db"
        store = Store(str(path))
        await store.start()
        try:
            await _fill(store, 2000, "z" * 200)
            wal = (path.parent / (path.name + "-wal"))
            assert wal.exists() and wal.stat().st_size > 0
            assert store.db_size_bytes() >= path.stat().st_size + wal.stat().st_size
        finally:
            await store.stop()

    asyncio.run(run())


def test_size_trim_actually_returns_pages_to_the_filesystem(tmp_path) -> None:
    # Class 17. `conn.execute("PRAGMA incremental_vacuum")` reclaims exactly one page: the
    # pragma yields a row per freed page and sqlite3 steps it only as rows are consumed, so
    # an unconsumed execute() advances it once. The cap trimmed rows correctly and handed
    # back ~0.02% of the space, with nothing reporting it - the same request-versus-result
    # shape as the auto_vacuum defect this mechanism exists to fix.
    #
    # Asserted on the freelist, which is what "gave the space back" means, rather than on
    # the pragma being issued: the broken version issued it too.
    async def run() -> None:
        db = tmp_path / "vac.db"
        store = Store(str(db))
        await store.start()
        try:
            assert store._conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2, \
                "the capture must be INCREMENTAL, or this reclaims nothing either way"
            row = "x" * 400
            for i in range(4000):
                fut = await store.submit_line(
                    ts=time.time(), port="p", dir="rx", chan="debug", seq=None,
                    raw=f"{i} {row}",
                )
            await fut
            max_id = store.max_id()
            await store.delete_range(1, max_id)
            free = store._conn.execute("PRAGMA freelist_count").fetchone()[0]
            # Bounded per call, so a large backlog drains over several sweeps; what must not
            # happen is the one-page-per-call behaviour, which leaves nearly all of it.
            assert free < 100, f"the trim left {free} free pages; the vacuum did not step"
        finally:
            await store.stop()

    asyncio.run(run())
