"""`mcu session delete` and `mcu session export`: which session a name or id picks, and
what a miss downloads (SPEC 4)."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from mcuscope import cli
from tests.support import UNREACHABLE, Stack, canned, make_sessions, paths, recorder, stack_client

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
    """A daemon honouring name= answers no row; the page an older one answers is in
    test_a_page_without_the_name_falls_back_to_the_quoted_path (FP-8)."""
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


# -- FP-8: a daemon ignoring ?name= still exports a session past its page ----------------


@pytest.mark.parametrize("bundle", [[], ["--bundle"]])
def test_a_page_without_the_name_falls_back_to_the_quoted_path(monkeypatch, capsys, tmp_path,
                                                                bundle) -> None:
    name = "run 1?#x"
    page = {"sessions": [{"id": i, "name": f"s{i}"} for i in range(60, 10, -1)]}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json=page)
        return httpx.Response(200, content=b"SQLite")
    canned(monkeypatch, handler)
    out = tmp_path / ("s.zip" if bundle else "s.db")
    rc = cli.main(["session", "export", name, *bundle, "-o", str(out), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    kind = "bundle" if bundle else "export"
    # The lookup's query is compared decoded: httpx encodes its space as `+` or `%20` by
    # version. The export path is the CLI's own quoting, so it is compared raw.
    assert [r.url.path for r in seen] == ["/sessions", f"/sessions/{name}/{kind}"]
    assert parse_qs(seen[0].url.query.decode(), strict_parsing=True) == {"name": [name]}
    assert seen[1].url.raw_path == f"/sessions/run%201%3F%23x/{kind}".encode()
    assert out.read_bytes() == b"SQLite"


def test_a_page_holding_the_name_exports_that_row_by_id(monkeypatch, capsys, tmp_path) -> None:
    """The newest row is never taken for the name: only an exact name or id matches."""
    page = {"sessions": [{"id": 9, "name": "old-run-2"}, {"id": 3, "name": "old-run"}]}
    seen = recorder(monkeypatch, sessions=page, sessions_3_export="SQLite")
    rc = cli.main(["session", "export", "old-run", "-o", str(tmp_path / "s.db"), *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/3/export"], paths(seen)


def test_a_fallback_to_a_session_the_daemon_lacks_downloads_nothing(monkeypatch, capsys,
                                                                    tmp_path) -> None:
    seen = recorder(monkeypatch, sessions={"sessions": [{"id": 1, "name": "other"}]},
                    sessions_nope_export=(400, {"error": "no such session: nope"}))
    out = tmp_path / "s.db"
    rc = cli.main(["session", "export", "nope", "-o", str(out), *UNREACHABLE])
    assert rc == 1
    assert "no such session: nope" in capsys.readouterr().err
    assert paths(seen) == ["/sessions", "/sessions/nope/export"] and not out.exists()


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
