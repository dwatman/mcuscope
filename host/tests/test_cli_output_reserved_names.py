"""A Windows device name is written in place, like /dev/null, with no partial-export warning."""

from __future__ import annotations

import os

import httpx
import pytest

from mcuscope import cli, cli_output
from mcuscope.cli_output import AtomicOut, is_reserved_name
from tests.support import UNREACHABLE, canned


@pytest.mark.parametrize("p", ["NUL", "nul", "C:\\x\\CON", "out/aux.txt", "COM1", "lpt9", "NUL ",
                               "CONIN$", "conout$.txt", "COM\u00b9", "LPT\u00b3"])
def test_reserved_names_are_recognised(p) -> None:
    assert is_reserved_name(p)


@pytest.mark.parametrize("p", ["null", "COM0", "COM10", "console.txt", "x/NULL.log", "LPT"])
def test_ordinary_names_are_not(p) -> None:
    assert not is_reserved_name(p)


def test_a_reserved_target_is_written_in_place_without_a_temp(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli_output.os, "name", "nt")
    monkeypatch.chdir(tmp_path)
    out = AtomicOut("NUL.txt", "w")   # stands in for a device: opens a plain file here
    assert out.tmp is None
    out.fh.write("x")
    out.commit()
    assert "no temporary file" not in capsys.readouterr().err


def test_a_reserved_name_that_stats_as_a_regular_file_is_replaced_whole(
    tmp_path, monkeypatch
) -> None:
    """Stat outranks the spelling: where Windows made the name an ordinary file, an
    interrupted export must not leave it partial."""
    monkeypatch.setattr(cli_output.os, "name", "nt")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "con.csv").write_text("old", encoding="utf-8")
    out = AtomicOut("con.csv", "w")
    assert out.tmp is not None
    out.fh.write("new")
    assert (tmp_path / "con.csv").read_text(encoding="utf-8") == "old"   # untouched until commit
    out.commit()
    assert (tmp_path / "con.csv").read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(os.name != "nt", reason="the real NUL device")
def test_an_export_to_nul_writes_through_it_on_windows(tmp_path, monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="a\nb\n"))
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["log", "export", "-o", "NUL", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 0 and "warning" not in err, err
    assert list(tmp_path.iterdir()) == [], "no temp file and no file named NUL"
