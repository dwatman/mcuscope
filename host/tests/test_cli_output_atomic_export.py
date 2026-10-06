"""An export's `-o FILE` appears whole or not at all (SPEC 4, RES-5): a signal runs no
cleanup, so the target must never hold a partial body, not even while the stream runs."""

from __future__ import annotations

import os
import stat

import httpx
import pytest

from mcuscope import cli
from mcuscope.cli_output import AtomicOut
from tests.support import UNREACHABLE, canned


def _session(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/sessions":
        return httpx.Response(200, json={"sessions": [{"id": 1, "name": "run"}]})
    return None


@pytest.mark.parametrize("argv", [
    ["log", "export"],                       # the daemon-rendered stream
    ["log", "export", "--csv"],
    ["log", "export", "--limit", "5"],       # the paged path, rendered here
    ["plot", "export", "--names", "x"],
    ["can", "dump", "--csv"],
    ["session", "export", "run"],            # Client.download
])
def test_the_target_holds_its_old_bytes_while_the_export_streams(monkeypatch, capsys,
                                                                 tmp_path, argv) -> None:
    target = tmp_path / "out.txt"
    target.write_text("yesterday\n", encoding="utf-8")
    during: list[str] = []

    def body():
        yield b"first chunk\n"
        during.append(target.read_text(encoding="utf-8"))   # mid-stream: a kill lands here
        yield b"second chunk\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if (r := _session(request)) is not None:
            return r
        if request.url.path == "/lines":
            row = {"id": 1, "ts": 1.0, "port": "a", "dir": "rx", "chan": "debug",
                   "seq": None, "raw": "x"}
            during.append(target.read_text(encoding="utf-8"))
            return httpx.Response(200, json={"lines": [row], "truncated": False})
        return httpx.Response(200, content=body())

    canned(monkeypatch, handler)
    rc = cli.main([*argv, "-o", str(target), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert during and set(during) == {"yesterday\n"}, during
    assert target.read_text(encoding="utf-8") != "yesterday\n"   # and then it was replaced
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"], "no temp file left"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_a_replaced_file_keeps_its_mode_and_a_new_one_gets_the_umask(tmp_path) -> None:
    kept = tmp_path / "kept.csv"
    kept.write_text("old", encoding="utf-8")
    kept.chmod(0o640)
    out = AtomicOut(str(kept), "w", newline="")
    out.fh.write("new")
    out.commit()
    assert stat.S_IMODE(kept.stat().st_mode) == 0o640 and kept.read_text() == "new"

    mask = os.umask(0o022)
    try:
        fresh = AtomicOut(str(tmp_path / "fresh.csv"), "w", newline="")
        fresh.commit()
    finally:
        os.umask(mask)
    assert stat.S_IMODE((tmp_path / "fresh.csv").stat().st_mode) == 0o644   # not mkstemp's 0600


@pytest.mark.skipif(not os.path.exists("/dev/null") or os.name != "posix", reason="POSIX")
def test_an_export_to_dev_null_writes_through_it(monkeypatch, capsys) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="a\nb\n"))
    rc = cli.main(["log", "export", "-o", "/dev/null", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert stat.S_ISCHR(os.stat("/dev/null").st_mode)


def test_an_unwritable_directory_is_named_by_the_path_asked_for(monkeypatch, capsys,
                                                                tmp_path) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="a\n"))
    target = tmp_path / "missing" / "out.txt"
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and f"cannot write {target}" in err
    assert ".partial" not in err, "the temp file is not a name the user typed"


_POSIX_USER = os.name == "posix" and os.geteuid() != 0


@pytest.mark.skipif(not _POSIX_USER, reason="POSIX directory modes, not as root")
def test_a_read_only_directory_writes_the_file_directly_with_a_warning(monkeypatch, capsys,
                                                                       tmp_path) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="new\n"))
    target = tmp_path / "f.txt"
    target.write_text("old\n", encoding="utf-8")
    tmp_path.chmod(0o555)
    try:
        rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    finally:
        tmp_path.chmod(0o755)
    err = capsys.readouterr().err
    assert rc == 0, err
    assert target.read_text(encoding="utf-8") == "new\n"
    assert f"warning: no temporary file beside {target}" in err, err
    assert "an interrupted export leaves it partial" in err
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_a_name_near_the_limit_still_goes_through_a_temp_file(monkeypatch, capsys,
                                                              tmp_path) -> None:
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX") if hasattr(os, "pathconf") else 255
    target = tmp_path / ("n" * (name_max - 4) + ".txt")
    target.write_text("old\n", encoding="utf-8")
    during: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        def body():
            yield b"new\n"
            during.append(target.read_text(encoding="utf-8"))
        return httpx.Response(200, content=body())

    canned(monkeypatch, handler)
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 0 and "warning" not in err, err
    assert during == ["old\n"], "written beside it, not in place"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_a_failed_rename_names_the_target_and_removes_the_temp(monkeypatch, capsys,
                                                               tmp_path) -> None:
    canned(monkeypatch, lambda request: httpx.Response(200, text="new\n"))
    target = tmp_path / "held.txt"
    target.write_text("old\n", encoding="utf-8")

    def replace(src, dst):
        raise PermissionError(13, "Access is denied", src, None, dst)

    monkeypatch.setattr(os, "replace", replace)
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and f"cannot write {target}" in err, err
    assert ".partial" not in err, err
    assert target.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["held.txt"], "no temp file left"


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_a_target_another_handle_holds_open_is_refused_whole_on_windows(monkeypatch, capsys,
                                                                       tmp_path) -> None:
    """The real FD-CLI-4 case: a reader's handle shares no delete, so the rename fails."""
    canned(monkeypatch, lambda request: httpx.Response(200, text="new\n"))
    target = tmp_path / "held.txt"
    target.write_text("old\n", encoding="utf-8")
    with open(target, encoding="utf-8"):
        rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and f"cannot write {target}" in err, err
    assert target.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["held.txt"], "no temp file left"


@pytest.mark.skipif(not _POSIX_USER, reason="POSIX file modes, not as root")
def test_a_read_only_temp_is_still_removed(monkeypatch, capsys, tmp_path) -> None:
    """Windows refuses to remove a read-only file; the temp of a read-only target is one."""
    real_remove = os.remove

    def windows_remove(path):
        if not os.stat(path).st_mode & stat.S_IWRITE:
            raise PermissionError(13, "Access is denied", path)
        real_remove(path)

    monkeypatch.setattr(os, "remove", windows_remove)
    canned(monkeypatch, lambda request: httpx.Response(200, text="new\n"))
    target = tmp_path / "ro.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o444)
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and f"cannot write {target}" in err, err
    assert [p.name for p in tmp_path.iterdir()] == ["ro.txt"], "no temp file left"
