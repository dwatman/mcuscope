"""`mcu session delete` and `mcu session export`: which session a name or id picks, and
what a miss downloads (SPEC 4)."""

from __future__ import annotations

import pytest

from mcuscope import cli
from tests.support import UNREACHABLE, Stack, make_sessions, paths, recorder, stack_client

# -- B-9 / B-10: the session export target and path ----------------------------------------


def test_session_export_goes_by_id_whatever_the_name(monkeypatch, capsys, tmp_path) -> None:
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 7, "name": "run?x=1#3/b"}]},
                    sessions_7_export="SQLite")
    rc = cli.main(["session", "export", "run?x=1#3/b", "-o", str(tmp_path / "s.db"),
                   *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/7/export"], paths(seen)
    assert seen[0].url.params["name"] == "run?x=1#3/b"


def test_session_export_of_no_such_session_downloads_nothing(monkeypatch, capsys,
                                                             tmp_path) -> None:
    seen = recorder(monkeypatch, sessions={"sessions": []})
    out = tmp_path / "s.db"
    rc = cli.main(["session", "export", "nope", "-o", str(out), *UNREACHABLE])
    assert rc == 1
    assert "no such session: nope" in capsys.readouterr().err
    assert paths(seen) == ["/sessions"] and not out.exists()


@pytest.mark.parametrize("bundle", [[], ["--bundle"]])
@pytest.mark.parametrize("form", ["slash", "existing"])
def test_session_export_refuses_a_directory_target(capsys, tmp_path, bundle, form) -> None:
    """`existing` is the final path: with --bundle, `-o adir` means `adir.zip`."""
    target = tmp_path / "adir"
    if form == "existing":
        (tmp_path / ("adir.zip" if bundle else "adir")).mkdir()
        arg = str(target)
    else:
        arg = str(target) + "/"
    rc = cli.main(["session", "export", "1", *bundle, "-o", arg, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "is a directory" in err, err
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_cli_session_delete_resolves_a_name_past_the_default_page(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    make_sessions(stack, 55)
    r = run_mcu(stack, "session", "delete", "s0")
    assert r.returncode == 0, r.stderr
    assert "s0" in r.stdout
    with stack_client(stack) as c:
        assert c.get("/sessions", params={"name": "s0"}).json()["sessions"] == []


def test_cli_session_delete_keeps_its_error_text_for_a_missing_name(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    r = run_mcu(stack, "session", "delete", "no-such-session")
    assert r.returncode == 1
    assert "no such session: no-such-session" in r.stderr
