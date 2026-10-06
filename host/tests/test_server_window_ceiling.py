"""A `last_ms` window has a ceiling as well as a floor (SPEC 3.4): rows stamped ahead of the
anchor (a clock stepped back since) are not "the last N ms" on the frozen paths either."""

from __future__ import annotations

import time as _time

from tests.support import Stack, on_loop, stack_client


def _future_rows(stack: Stack, tag: str) -> None:
    store = stack.app.state.store
    ahead = _time.time() + 3600
    for i in range(5):
        on_loop(stack, store.add_line(
            ts=ahead + i, port=stack.alias, dir="rx", chan="debug", seq=None, raw=f"{tag} {i}",
        ))


def test_a_retrospective_assert_does_not_judge_rows_stamped_ahead_of_now(stack: Stack) -> None:
    _future_rows(stack, "ZZAHEADA")
    with stack_client(stack) as c:
        r = c.post("/assert", json={
            "expect": ["ZZAHEADA"], "timeout_ms": 0, "last_ms": 60000,
        }).json()
        control = c.post("/assert", json={"expect": ["ZZAHEADA"], "timeout_ms": 0}).json()
    assert [e["matched"] for e in control["expect"]] == [True], control   # the rows exist
    assert [e["matched"] for e in r["expect"]] == [False], r


def test_an_export_does_not_carry_rows_stamped_ahead_of_now(stack: Stack) -> None:
    _future_rows(stack, "ZZAHEADB")
    with stack_client(stack) as c:
        windowed = c.get("/lines/export", params={"last_ms": 60000, "match": "ZZAHEADB"})
        control = c.get("/lines/export", params={"match": "ZZAHEADB"})
    assert windowed.status_code == 200 and control.status_code == 200
    assert control.text.count("ZZAHEADB") == 5, control.text
    assert "ZZAHEADB" not in windowed.text, windowed.text
