"""`mcuscoped` takes the id `mcu daemon start` hands it (MCUSCOPED_START_ID) only in the
grammar that start generates; anything else is ignored with a note, never echoed."""

from __future__ import annotations

import pytest

from mcuscope import daemon


def test_the_id_a_start_generates_is_taken(monkeypatch, capsys) -> None:
    import secrets

    start_id = secrets.token_hex(16)
    monkeypatch.setenv("MCUSCOPED_START_ID", start_id)
    assert daemon._start_id() == start_id
    assert capsys.readouterr().err == ""


def test_no_id_is_no_note(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MCUSCOPED_START_ID", raising=False)
    assert daemon._start_id() is None
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("raw", ["", "ab" * 7, "ab" * 33, "AB" * 8, "ab" * 8 + "\r\nX-Evil: 1",
                                 "ab" * 8 + "\n"])
def test_any_other_value_is_ignored_and_said(monkeypatch, capsys, raw) -> None:
    monkeypatch.setenv("MCUSCOPED_START_ID", raw)
    assert daemon._start_id() is None
    assert "ignoring MCUSCOPED_START_ID: not 16 to 64 lowercase hex digits" in \
        capsys.readouterr().err
