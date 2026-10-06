"""A resolved `floor_ts` window takes its ceiling from the same anchor (REVIEW class 104):
the store refuses a floor without the ceiling it was measured from."""

from __future__ import annotations

import pytest

from mcuscope.store import WINDOW_TS_SLACK_S, Store


async def test_a_floor_without_its_ceiling_is_refused_and_one_with_it_is_used(
    tmp_path
) -> None:
    store = Store(str(tmp_path / "w.db"))
    await store.start()
    try:
        with pytest.raises(ValueError, match="floor_ts needs the ceil_ts"):
            store._window_terms(floor_ts=100.0, id_to=5)
        clauses, params = store._window_terms(floor_ts=100.0, ceil_ts=160.0, id_to=5)
        assert params[clauses.index("+ts <= ?")] == 160.0 + WINDOW_TS_SLACK_S
    finally:
        await store.stop()
