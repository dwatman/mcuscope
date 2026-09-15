"""Owner ruling 2026-09-15: the subscriber cap exits 1 everywhere. `mcu tail -f` refused with
a WebSocket close 1013 says so like `mcu wait` does for the cap's 503; a daemon that is really
going away (close 1001 at shutdown) stays exit 3. Driven against the in-process stack."""

from __future__ import annotations

import json

import pytest

from mcuscope import cli, store
from tests.support import Stack


@pytest.fixture
def capped(stack: Stack, monkeypatch) -> Stack:
    monkeypatch.setattr(store, "MAX_SUBSCRIBERS", 0)   # read at subscribe time
    return stack


def test_tail_follow_at_the_subscriber_cap_is_exit_1_naming_the_cap(capped, capsys) -> None:
    rc = cli.main(["tail", "-n", "0", "-f", "--url", capped.base_url])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "too many subscribers" in err and "subscriber cap" in err, err
    assert "stream closed by daemon" not in err


def test_tail_follow_cap_in_json_mode_is_one_error_object(capped, capsys) -> None:
    rc = cli.main(["--json", "tail", "-n", "0", "-f", "--url", capped.base_url])
    out = capsys.readouterr().out
    assert rc == 1
    obj = json.loads(out.strip().splitlines()[-1])
    assert obj["exit_code"] == 1 and "too many subscribers" in obj["error"]


def test_wait_at_the_same_cap_answers_the_same_code_and_words(capped, capsys) -> None:
    """The sibling surface the ruling aligns with: both name "too many subscribers"."""
    rc = cli.main(["wait", "--match", "never", "--timeout", "500", "--url", capped.base_url])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "too many subscribers" in err


def test_tail_follow_refused_at_shutdown_stays_exit_3(stack: Stack, capsys) -> None:
    """Close 1001: the daemon is going away, which is "unreachable", not the cap."""
    stack.app.state.store._subscribers_closed = True
    rc = cli.main(["tail", "-n", "0", "-f", "--url", stack.base_url])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "stream closed by daemon" in err
    assert "too many subscribers" not in err
