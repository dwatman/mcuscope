"""The token guard's failure table: evicting live lockouts at its bound is logged once per
episode (SPEC 3.1), so an address spray that clears lockouts is visible."""

from __future__ import annotations

import logging

from mcuscope.server import (
    TOKEN_FAIL_TABLE_MAX,
    TOKEN_FAIL_WINDOW_S,
    TOKEN_LOCKOUT_S,
    _TokenGuard,
)

MSG = "evicting the oldest, whose lockouts end early"


def _spray(guard: _TokenGuard, now: float, n: int, prefix: str) -> None:
    for i in range(n):
        guard._register_failure(f"{prefix}.{i // 256}.{i % 256}", now)


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if MSG in r.getMessage()]


def test_evictions_are_logged_once_per_episode(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="mcuscope.server")
    guard = _TokenGuard(app=None, token="sesame-open-123")
    _spray(guard, 0.0, TOKEN_FAIL_TABLE_MAX, "10.0")
    assert _warnings(caplog) == [], "logged before anything was evicted"

    _spray(guard, 0.0, TOKEN_FAIL_TABLE_MAX, "10.1")   # a thousand evictions, one episode
    assert len(guard._fails) <= TOKEN_FAIL_TABLE_MAX
    assert _warnings(caplog) == [
        f"token guard: {TOKEN_FAIL_TABLE_MAX} addresses with failed tokens; {MSG}"
    ]

    # Every record expires: the next prune evicts nothing live, which ends the episode.
    later = TOKEN_FAIL_WINDOW_S + TOKEN_LOCKOUT_S + 1.0
    guard._register_failure("10.9.0.1", later)
    assert len(guard._fails) == 1
    _spray(guard, later, TOKEN_FAIL_TABLE_MAX * 2, "10.2")   # a second spray, a second line
    assert len(_warnings(caplog)) == 2
