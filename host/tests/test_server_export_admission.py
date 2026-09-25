"""Export admission (SPEC 3.4): a bundle takes the sweep lock only once it holds a build slot,
and `?check=1` answers the refusal a session `.db` export would get, without building."""

from __future__ import annotations

import concurrent.futures

import httpx
import pytest

from mcuscope.server import EXPORT_QUEUE_MAX, EXPORT_WAITERS_MAX, EXPORT_WORKERS
from mcuscope.store import Store
from tests.support import Stack, on_loop
from tests.test_e2e import poll
from tests.test_server_export_pool import _Build, _fill, _get, _sid


@pytest.fixture
def build(monkeypatch):
    fake = _Build()
    monkeypatch.setattr(Store, "export_session_db",
                        lambda self, dest_path, **kw: fake(self, dest_path, **kw))
    yield fake
    fake.release.set()


BUSY = "too many session exports in progress; try again shortly"
WAITERS = "too many session exports waiting for a slot; try again shortly"


def test_retention_runs_while_a_bundle_waits_for_a_slot(stack: Stack, build: _Build) -> None:
    sid = _sid(stack)
    threads, results = _fill(stack, build, sid)
    store = stack.app.state.store
    bundled: list = []
    bundle = _get(stack, f"/sessions/{sid}/bundle", bundled, wait=1)
    assert poll(lambda: stack.app.state.export_waiters == 1, 5), "the bundle never parked"
    assert not store._sweep_lock.locked(), "the waiting bundle holds the sweep lock"
    try:
        on_loop(stack, store._sweep_retention_async(), timeout=5)
    except concurrent.futures.TimeoutError:
        raise AssertionError("retention waited on a bundle queued for a slot") from None
    build.release.set()
    for t in [*threads, bundle]:
        t.join(20)
    # Positive control: the bundle, once admitted, still ran (and held the lock to do so).
    assert [r.status_code for r in bundled] == [200]


def test_check_answers_the_refusal_without_building(stack: Stack, build: _Build) -> None:
    sid = _sid(stack)
    url = f"{stack.base_url}/sessions/{sid}/export"

    def check(**params) -> tuple[int, dict]:
        r = httpx.get(url, params={"check": 1, **params}, timeout=5)
        return r.status_code, r.json()

    assert check() == (200, {"ok": True})
    r = httpx.get(f"{stack.base_url}/sessions/no-such/export", params={"check": 1}, timeout=5)
    assert (r.status_code, r.json()) == (400, {"error": "no such session: no-such"})
    threads, results = _fill(stack, build, sid)
    assert check() == (503, {"error": BUSY})
    assert check(wait=1) == (200, {"ok": True})   # a navigation with wait=1 would queue
    waited: list = []
    threads += [_get(stack, f"/sessions/{sid}/export", waited, wait=1)
                for _ in range(EXPORT_WAITERS_MAX)]
    assert poll(lambda: stack.app.state.export_waiters == EXPORT_WAITERS_MAX, 5)
    assert check(wait=1) == (503, {"error": WAITERS})
    # No check took a slot or started a build.
    assert stack.app.state.export_builds == EXPORT_WORKERS + EXPORT_QUEUE_MAX
    assert len(build.paths) == EXPORT_WORKERS
    build.release.set()
    for t in threads:
        t.join(30)


def test_a_bundle_refused_after_its_slot_gives_the_slot_back(tmp_path, monkeypatch) -> None:
    from tests.test_server_exports import _app, _client, _session_id

    with _client(_app(tmp_path)) as c:
        store, state = c.app.state.store, c.app.state
        sid = _session_id(c)
        # The session deleted while the bundle waited for the lock: a refusal from prepare.
        monkeypatch.setattr(store, "get_session", lambda _id: None)
        r = c.get(f"/sessions/{sid}/bundle")
        assert (r.status_code, r.json()) == (400, {"error": f"no such session: {sid}"})
        assert state.export_builds == 0
        monkeypatch.undo()

        async def boom(**_kw):
            raise RuntimeError("members failed")

        monkeypatch.setattr(store, "plot_streams_safe", boom)
        r = c.get(f"/sessions/{sid}/bundle")
        assert r.status_code == 400 and "members failed" in r.json()["error"]
        assert state.export_builds == 0
        assert not store._sweep_lock.locked()
        monkeypatch.undo()
        assert c.get(f"/sessions/{sid}/bundle").status_code == 200   # control: the slot is free
