"""A handler cancelled in the tick its raced work failed retrieves that failure (REVIEW class 39):
otherwise asyncio logs "exception was never retrieved" for an error nobody could act on."""

from __future__ import annotations

import asyncio
import gc
import threading
from types import SimpleNamespace

import pytest

from mcuscope import server as server_mod
from mcuscope.config import Config, StorageConfig


def _run_recording(coro_fn) -> list[dict]:
    """Run `coro_fn()` on a fresh loop; the exception-handler calls made by gc afterwards."""
    seen: list[dict] = []

    async def main() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: seen.append(ctx))
        await coro_fn()
        gc.collect()

    asyncio.run(main())
    return [ctx for ctx in seen if "never retrieved" in ctx.get("message", "")]


def _until_stopped_case(cancel: bool):
    async def case() -> None:
        store = SimpleNamespace(_subscribers_stopped=asyncio.Event())
        work = asyncio.get_running_loop().create_future()
        task = asyncio.ensure_future(server_mod._until_stopped(store, work, "stopped"))
        for _ in range(3):
            await asyncio.sleep(0)   # parked in asyncio.wait
        work.set_exception(RuntimeError("boom"))
        if cancel:
            task.cancel()            # same tick: the task wakes cancelled, work failed
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="boom"):
                await task
        del work, task
    return case


def test_until_stopped_retrieves_work_that_failed_as_it_was_cancelled() -> None:
    assert _run_recording(_until_stopped_case(cancel=True)) == []


def test_until_stopped_positive_control_raises_the_failure_uncancelled() -> None:
    assert _run_recording(_until_stopped_case(cancel=False)) == []


def test_a_detector_sees_an_unretrieved_failure() -> None:
    # Positive control for the observation point: an abandoned failed future is reported.
    async def case() -> None:
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(RuntimeError("lost"))
        del fut

    assert len(_run_recording(case)) == 1


def test_an_export_build_that_failed_as_its_handler_was_cancelled_is_retrieved(
    tmp_path, monkeypatch
) -> None:
    entered, release, posted = threading.Event(), threading.Event(), threading.Event()
    submitted: list = []
    real_pool = server_mod._pool

    def pool(name: str, workers: int):
        real = real_pool(name, workers)
        if name != "export":
            return real

        def submit(fn, *args):
            cf = real.submit(fn, *args)
            submitted.append(cf)
            return cf
        return SimpleNamespace(submit=submit)

    monkeypatch.setattr(server_mod, "_pool", pool)

    def build(_job) -> str:
        entered.set()
        release.wait(10)
        raise RuntimeError("build failed")

    async def case() -> None:
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(
            export_builds=1, export_waiters=0, export_freed=asyncio.Event(),   # claimed
            export_files=set(), export_key="k",
            config=Config(storage=StorageConfig(db_path=str(tmp_path / "cap.db"))),
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        gone = loop.create_future()
        task = asyncio.ensure_future(server_mod._build_admitted(request, build, gone))
        await asyncio.to_thread(entered.wait, 10)
        # The handler is parked, so wrap_future's callback is registered; this one runs after
        # it, so once it has fired the failure is already posted to the loop.
        [cf] = submitted
        cf.add_done_callback(lambda _f: posted.set())
        release.set()
        assert posted.wait(10)   # blocks the loop: the result cannot land before the cancel
        assert isinstance(cf.exception(), RuntimeError)
        task.cancel()            # so both land in one tick
        with pytest.raises(asyncio.CancelledError):
            await task
        gone.cancel()
        # cf keeps its done callbacks, which reach the handler's future: drop every holder,
        # or that future outlives the recording and its failure is never reported.
        submitted.clear()
        del task, cf

    assert _run_recording(case) == []
    assert posted.is_set()
