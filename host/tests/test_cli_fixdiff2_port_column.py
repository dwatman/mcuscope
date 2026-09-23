"""A stream's `[port]` column counts the boards attached and stored together: one board
detached with history and another attached are two boards."""

from __future__ import annotations

from tests.test_port_column_stored import _ports_body


def test_a_detached_boards_history_and_another_attached_board_carry_the_column(
    monkeypatch,
) -> None:
    assert _ports_body(monkeypatch, {"ports": [{"alias": "b"}], "stored": ["a"]})


def test_one_board_attached_and_stored_has_no_column(monkeypatch) -> None:
    assert not _ports_body(monkeypatch, {"ports": [{"alias": "a"}], "stored": ["a"]})
    assert not _ports_body(monkeypatch, {"ports": [{"alias": "a"}], "stored": ["a", ""]})


def test_a_malformed_name_is_no_board_and_no_crash(monkeypatch) -> None:
    assert not _ports_body(monkeypatch, {"ports": [{"alias": ["a"]}], "stored": [{"b": 1}, "c"]})
