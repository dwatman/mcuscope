"""The files and streams `mcu` exports write: `-o` targets, newline translation, and what
a refused or dying export leaves behind (SPEC 4)."""

from __future__ import annotations

import builtins
import io
import json
import os
import sys

import httpx
import pytest

from mcuscope import cli
from mcuscope.cli_output import remove_partial
from tests.support import UNREACHABLE, canned, paths, recorder

# -- C4 / C9: the session export output path -------------------------------------------


@pytest.mark.parametrize("name", ["run.db", "run.DB", "run.Db"])
def test_bundle_refuses_a_db_name_whatever_its_case(capsys, tmp_path, name) -> None:
    """On Windows run.DB and run.db are one file, so a case-sensitive guard is none."""
    rc = cli.main(["session", "export", "1", "--bundle", "-o", str(tmp_path / name),
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "not a .db" in err, err
    assert not (tmp_path / name).exists()


EXPORTS = [
    ["session", "export", "1", "--bundle"],
    ["session", "export", "1"],
    ["log", "export"],
    ["plot", "export", "--names", "vbat"],
    ["can", "dump"],
]


def test_the_export_list_is_every_command_taking_the_option() -> None:
    """The --from list's twin is test_cli_export.py's WINDOWED."""
    from tests.test_cli_contract import command_of, commands_with

    derived = commands_with("-o")
    assert len(derived) >= 4, derived
    assert {command_of(argv) for argv in EXPORTS} == derived


@pytest.mark.parametrize("argv", EXPORTS)
def test_every_export_refuses_the_stdout_token(capsys, tmp_path, monkeypatch, argv) -> None:
    """`-o -` opened a file called "-" (and "-.zip" from the bundle branch), on all of them.

    One message, so the answer does not depend on which export was asked for, and it says
    where stdout actually is.
    """
    monkeypatch.chdir(tmp_path)
    rc = cli.main([*argv, "-o", "-", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "error: -o - is not stdout; omit -o for stdout, or give a file path" in err, err
    assert list(tmp_path.iterdir()) == [], "and no file named - is left behind"


# -- C6 / C10: line endings, on the file and on stdout ----------------------------------


def open_spy(monkeypatch) -> list[tuple[str, object]]:
    """Record (path, newline=) for every open, so the kwarg is asserted on Linux too."""
    calls: list[tuple[str, object]] = []
    real = builtins.open

    def spy(file, mode="r", *args, **kw):
        calls.append((str(file), kw.get("newline", "MISSING")))
        return real(file, mode, *args, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return calls


def test_the_streamed_export_file_is_opened_untranslated(monkeypatch, tmp_path,
                                                         capsys) -> None:
    """newline="" or the body stops reaching the file byte for byte on Windows."""
    recorder(monkeypatch, lines_export="a,b\nc,d\n")
    out = tmp_path / "run.csv"
    calls = open_spy(monkeypatch)
    rc = cli.main(["log", "export", "--csv", "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert (str(out), "") in calls, calls


def test_the_paged_export_file_is_opened_lf_only(monkeypatch, tmp_path, capsys) -> None:
    """The sibling path writes its own rows, so it pins LF rather than no translation."""
    recorder(monkeypatch, lines={"lines": [{"id": 1, "ts": 0.0, "chan": "debug",
                                            "raw": "hi"}], "truncated": False})
    out = tmp_path / "run.txt"
    calls = open_spy(monkeypatch)
    rc = cli.main(["log", "export", "--limit", "5", "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert (str(out), "\n") in calls, calls


@pytest.mark.parametrize("argv", [
    ["log", "export", "--csv"],
    ["can", "dump", "--csv"],
    ["plot", "export", "--names", "vbat"],
])
def test_a_streamed_export_to_stdout_is_not_newline_translated(monkeypatch, argv) -> None:
    """`> run.csv` and `-o run.csv` must write the same bytes (CRLF on Windows, else LF).

    Driven through a TextIOWrapper that translates to CRLF: on Linux the platform default
    hides the defect, and this makes the arm that never pinned `newline=` fail anywhere.
    """
    recorder(monkeypatch, lines_export="a,b\nc,d\n", can_frames="ts,id\n1,100\n",
             plot_export="ts,name,value\n1,vbat,2\n")
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", wrapper)
    rc = cli.main([*argv, *UNREACHABLE])
    wrapper.flush()
    assert rc == 0
    assert b"\r\n" not in buf.getvalue(), buf.getvalue()
    assert b"\n" in buf.getvalue()


# -- C7: a refusal from /lines/export reaches the user ---------------------------------


def test_a_400_from_the_export_endpoint_is_an_error_with_exit_1(monkeypatch,
                                                                capsys) -> None:
    """An unknown session is refused daemon-side; the CLI must not swallow the body."""
    recorder(monkeypatch, lines_export=(400, {"error": "no such session: nope"}))
    rc = cli.main(["log", "export", "--csv", "--session", "nope", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "error: no such session: nope" in err, err


def dying_body(*chunks: bytes):
    """A response body that sends `chunks` and then loses the connection."""
    def gen():
        yield from chunks
        raise httpx.ReadError("peer died")
    return gen()


# -- B-4: a refused export leaves -o alone -------------------------------------------------


REFUSAL = (400, {"error": "bad regex"})


@pytest.mark.parametrize("argv", [
    ["log", "export", "--match", "("],
    ["log", "export", "--match", "(", "--limit", "5"],
    ["log", "export", "--match", "(", "--decode"],
    ["plot", "export", "--names", "nosuch"],
    ["can", "dump", "--csv"],
    ["session", "export", "run"],
])
def test_a_refused_export_keeps_the_file_and_the_link(monkeypatch, capsys, tmp_path,
                                                      argv) -> None:
    recorder(monkeypatch, lines_export=REFUSAL, lines=REFUSAL, plot_export=REFUSAL,
             can_frames=REFUSAL, sessions={"sessions": [{"id": 1, "name": "run"}]},
             sessions_1_export=REFUSAL)
    target = tmp_path / "target.txt"
    target.write_text("data\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    plain = tmp_path / "plain.txt"
    plain.write_text("keep\n", encoding="utf-8")
    for out in (link, plain):
        rc = cli.main([*argv, "-o", str(out), *UNREACHABLE])
        assert rc == 1, capsys.readouterr().err
    assert link.is_symlink(), "the refusal removed the link"
    assert target.read_text(encoding="utf-8") == "data\n", "the refusal truncated the target"
    assert plain.read_text(encoding="utf-8") == "keep\n"


def test_a_stream_dying_mid_export_removes_a_file_but_not_a_link(monkeypatch, capsys,
                                                                 tmp_path) -> None:
    canned(monkeypatch, lambda request: httpx.Response(
        200, content=dying_body(b"a line\n")))
    target = tmp_path / "target.txt"
    target.write_text("data\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    plain = tmp_path / "plain.txt"
    for out in (link, plain):
        rc = cli.main(["log", "export", "-o", str(out), *UNREACHABLE])
        assert rc == 3, capsys.readouterr().err
    assert link.is_symlink(), "a link is not the partial file"
    assert not plain.exists(), "a short export reads exactly like a whole one"


def test_a_session_download_dying_mid_stream_keeps_a_link(monkeypatch, capsys,
                                                          tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]})
        return httpx.Response(200, content=dying_body(b"SQLite format 3"))

    canned(monkeypatch, handler)
    target = tmp_path / "target.db"
    target.write_bytes(b"old")
    link = tmp_path / "link.db"
    link.symlink_to(target)
    plain = tmp_path / "plain.db"
    for out in (link, plain):
        rc = cli.main(["session", "export", "run", "-o", str(out), *UNREACHABLE])
        assert rc == 3, capsys.readouterr().err
    assert link.is_symlink()
    assert not plain.exists(), "a truncated .db reads like a whole one"


def test_an_empty_accepted_export_still_writes_an_empty_file(monkeypatch, capsys,
                                                            tmp_path) -> None:
    """Opening at the first chunk must not lose the file of a window with nothing in it."""
    recorder(monkeypatch, lines_export="", lines={"lines": [], "truncated": False})
    for extra in ([], ["--limit", "5"]):
        out = tmp_path / f"empty{len(extra)}.txt"
        rc = cli.main(["log", "export", *extra, "-o", str(out), *UNREACHABLE])
        assert rc == 0, capsys.readouterr().err
        assert out.read_bytes() == b""


# -- B-5: the in-band error is its own JSONL line -------------------------------------------


def test_a_mid_row_failure_leaves_every_stdout_line_parseable(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(
        200, content=dying_body(b'{"id": 1}\n{"id": 2, "se')))
    rc = cli.main(["--json", "log", "export", *UNREACHABLE])
    out = capsys.readouterr().out
    assert rc == 3
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[0] == {"id": 1}
    assert rows[-1]["exit_code"] == 3 and len(rows) == 2, out


def test_a_body_without_a_final_newline_is_still_written_whole(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="one\ntwo"))
    assert cli.main(["log", "export", *UNREACHABLE]) == 0
    assert capsys.readouterr().out == "one\ntwo"


# -- B-7: the paged export does not translate stdout ---------------------------------------


@pytest.mark.parametrize("extra", [["--limit", "1"], ["--decode"]])
def test_the_paged_export_to_stdout_is_not_newline_translated(monkeypatch, extra) -> None:
    row = {"id": 5, "ts": 0.0, "port": "a", "chan": "debug", "raw": "hi"}
    recorder(monkeypatch, lines={"lines": [row], "truncated": False})
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", wrapper)
    rc = cli.main(["log", "export", *extra, *UNREACHABLE])
    wrapper.flush()
    assert rc == 0
    assert b"hi" in buf.getvalue() and b"\r\n" not in buf.getvalue(), buf.getvalue()


# -- FP-5: -o NAME beside a NAME/ directory writes NAME.zip -----------------------------


def test_bundle_beside_a_directory_of_the_same_name_writes_the_zip(monkeypatch, capsys,
                                                                    tmp_path) -> None:
    (tmp_path / "run-3").mkdir()
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 1, "name": "run-3"}]},
                    sessions_1_bundle="PK")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["session", "export", "run-3", "--bundle", "-o", "run-3", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/1/bundle"], paths(seen)
    assert (tmp_path / "run-3.zip").read_bytes() == b"PK"
    assert (tmp_path / "run-3").is_dir() and not list((tmp_path / "run-3").iterdir())


def test_a_bundle_target_ending_in_a_separator_is_refused_before_the_suffix(
    monkeypatch, capsys, tmp_path
) -> None:
    seen = recorder(monkeypatch)
    rc = cli.main(["session", "export", "1", "--bundle", "-o", str(tmp_path / "x") + os.sep,
                   *UNREACHABLE])
    assert rc == 1
    assert "is a directory" in capsys.readouterr().err
    assert seen == [] and list(tmp_path.iterdir()) == []


# -- FP-7: a failed export through a symlink leaves no partial bytes behind it -----------


def _dying(*chunks: bytes):
    def gen():
        yield from chunks
        raise httpx.ReadError("peer died")
    return gen()


@pytest.mark.parametrize("argv", [["log", "export", "--csv"], ["session", "export", "run"]])
def test_a_dead_stream_through_a_symlink_removes_the_file_it_resolves_to(
    monkeypatch, capsys, tmp_path, argv
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]})
        return httpx.Response(200, content=_dying(b"id,ts\n1,2\n"))
    canned(monkeypatch, handler)
    target = tmp_path / "target.csv"
    target.write_text("yesterday's complete export\n", encoding="utf-8")
    hop = tmp_path / "hop.csv"
    hop.symlink_to(target)
    link = tmp_path / "latest.csv"
    link.symlink_to(hop)                       # a chain resolves to the same regular file
    rc = cli.main([*argv, "-o", str(link), *UNREACHABLE])
    assert rc == 3, capsys.readouterr().err
    assert link.is_symlink() and hop.is_symlink(), "a link is not the partial file"
    assert not target.exists(), "the partial bytes read like a whole export"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
def test_a_symlink_to_a_fifo_keeps_both(tmp_path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    link = tmp_path / "link"
    link.symlink_to(fifo)
    remove_partial(str(link))
    remove_partial(str(fifo))
    assert link.is_symlink() and fifo.exists()
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "gone")
    remove_partial(str(dangling))              # nothing to remove, and no error
    assert dangling.is_symlink()
