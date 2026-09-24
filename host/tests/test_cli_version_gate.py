"""`mcu` against a daemon too old for an option (SPEC 4): an option an older daemon would
drop is refused, not silently ignored, and one `/status` request judges them all.

Every test here drives `cli.main` in process against a canned transport: each case is
about one refusal, one message or the bytes one write produces, and the exit-code
contract is the same either way. The refusals that must not reach the network run against
an unreachable url, where exit 1 proves the CLI judged the request itself."""

from __future__ import annotations

import pytest

from mcuscope import cli
from tests.support import STATUS, UNREACHABLE, paths, recorder

# -- C3: --from/--to against a daemon that would silently drop them ---------------------

BOUNDED = [
    ["lines", "--to", "23:59"],
    ["lines", "--from", "00:01"],
    ["log", "export", "--to", "23:59"],
    ["plot", "export", "--names", "vbat", "--to", "23:59"],
    ["can", "dump", "--to", "23:59"],
]


@pytest.mark.parametrize("argv", BOUNDED)
def test_clock_bounds_are_refused_against_a_daemon_that_ignores_them(monkeypatch, capsys,
                                                                     argv) -> None:
    """A pre-0.4.0 daemon drops the undeclared parameter and answers the whole capture."""
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "0.3.0" in err and "0.4.0 or newer" in err, err
    assert "--from/--to" in err, err
    assert paths(seen) == ["/status"], "refused before the query it would have answered"


@pytest.mark.parametrize("argv", BOUNDED)
def test_clock_bounds_are_allowed_against_a_current_daemon(monkeypatch, capsys, argv) -> None:
    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False},
                    lines_export="", plot_export="ts,name,value\n",
                    can_frames={"frames": []})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen).count("/status") == 1, "one extra request, not one per page"


def test_an_unparsable_daemon_version_is_not_refused(monkeypatch, capsys) -> None:
    """A dev build must not be locked out on a string nobody can order."""
    recorder(monkeypatch, status={**STATUS, "version": "0.3.0.dev3+g1234"},
             lines={"lines": [], "truncated": False})
    rc = cli.main(["lines", "--to", "23:59", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


def test_a_query_without_clock_bounds_makes_no_extra_request(monkeypatch, capsys) -> None:
    """The version check is on that path only: every other command keeps its one call."""
    seen = recorder(monkeypatch, lines={"lines": [], "truncated": False})
    rc = cli.main(["lines", "--last-ms", "5000", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen) == ["/lines"], paths(seen)


def test_inverted_bounds_are_still_refused_before_the_version_check(monkeypatch,
                                                                    capsys) -> None:
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["lines", "--from", "19:00", "--to", "18:00", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "is after --to" in err, err
    assert seen == [], "a usage error costs no request"




































































# -- fix-diff F2: the rest of the 0.4.0 export surface is version-gated like --from/--to --


def test_plot_export_decode_is_refused_against_a_daemon_that_drops_it(monkeypatch, tmp_path,
                                                                      capsys) -> None:
    """A pre-0.4.0 `/plot/export` declares no `decode`, so FastAPI drops it and the export
    comes back raw at exit 0 (class 53)."""
    monkeypatch.chdir(tmp_path)   # a regressed gate writes x.csv here, not into host/
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["plot", "export", "--names", "ramp", "--decode", "-o", "x.csv",
                   *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "ignores --decode (" in err and "0.3.0" in err, err
    assert paths(seen) == ["/status"], paths(seen)
    assert list(tmp_path.iterdir()) == []


def test_can_dump_csv_is_refused_against_a_daemon_that_drops_format(monkeypatch, tmp_path,
                                                                    capsys) -> None:
    """Without `format` the old daemon answers JSON, which landed inside the .csv file."""
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, status={**STATUS, "version": "0.3.0"})
    rc = cli.main(["can", "dump", "--csv", "-o", "x.csv", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "--csv" in err and "0.3.0" in err, err
    assert paths(seen) == ["/status"], paths(seen)
    assert list(tmp_path.iterdir()) == []


OLD = {**STATUS, "version": "0.3.0"}


# -- B-2 / B-3: the version gate covers -p on plot export, --eol and --repeat-ms ---------


GATED = [
    (["-p", "nosuch", "plot", "export", "--names", "ramp"], "ignores -p ("),
    (["attach", "socket://127.0.0.1:1", "--alias", "e1", "--eol", "crlf"], "ignores --eol ("),
    (["send", "eoltest", "--eol", "none"], "ignores --eol ("),
    (["cmd", "ping", "--eol", "crlf"], "ignores --eol ("),
    (["wait", "--match", "x", "--send", "ping", "--eol", "none"], "ignores --eol ("),
    (["assert", "--expect", "x", "--send", "ping", "--eol", "none"], "ignores --eol ("),
    (["wait", "--match", "ZZZ", "--send", "", "--repeat-ms", "50", "--timeout", "300"],
     "ignores --repeat-ms ("),
]


@pytest.mark.parametrize(("argv", "named"), GATED, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_a_field_an_older_daemon_drops_is_refused(monkeypatch, capsys, tmp_path, argv,
                                                  named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, status=OLD)
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert named in err and "0.3.0" in err and "0.4.0 or newer" in err, err
    assert paths(seen) == ["/status"], "refused before the request it would have dropped"


@pytest.mark.parametrize(("argv", "named"), GATED, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_the_same_fields_reach_a_current_daemon(monkeypatch, capsys, tmp_path, argv,
                                               named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, plot_export="ts,name,value\n",
                    ports={"port": {"alias": "e1", "connected": False}},
                    send={"ok": True}, cmd={"status": "ok", "data": ""},
                    wait={"status": "timeout", "waited_ms": 1.0, "sends": 3,
                          "send_failures": 0},
                    **{"assert": {"status": "pass", "checked_lines": 0, "elapsed_ms": 1.0,
                                  "expect": [], "forbid": []}})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc in (0, 2), capsys.readouterr().err
    # attach's GET /ports (the retarget note's check) is not a gate request
    sent = [r.url.path for r in seen if (r.method, r.url.path) != ("GET", "/ports")]
    assert sent[0] == "/status" and len(sent) == 2, paths(seen)


@pytest.mark.parametrize("argv", [
    ["plot", "export", "--names", "ramp"],
    ["attach", "socket://127.0.0.1:1", "--alias", "e1"],
    ["attach", "socket://127.0.0.1:1", "--alias", "e1", "--eol", "lf"],
    ["send", "x"],
])
def test_no_gate_request_without_a_gated_option(monkeypatch, capsys, argv) -> None:
    """LF is what a pre-0.4.0 port appends anyway, so `--eol lf` on attach costs nothing."""
    seen = recorder(monkeypatch, plot_export="ts,name,value\n",
                    ports={"port": {"alias": "e1", "connected": False}}, send={"ok": True})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert "/status" not in paths(seen), paths(seen)


# -- B-18: a route an older daemon lacks names the version ----------------------------------


NOT_FOUND = (404, {"error": "Not Found"})


@pytest.mark.parametrize(("argv", "route"), [
    (["log", "export"], "/lines/export"),
    (["session", "export", "1", "--bundle", "-o", "b.zip"], "/sessions/1/bundle"),
    (["break"], "/break"),
])
def test_a_missing_route_names_the_daemon_version(monkeypatch, capsys, tmp_path, argv,
                                                  route) -> None:
    monkeypatch.chdir(tmp_path)
    recorder(monkeypatch, status=OLD, sessions={"sessions": [{"id": 1, "name": "r"}]},
             lines_export=NOT_FOUND, sessions_1_bundle=NOT_FOUND, **{"break": NOT_FOUND})
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert f"daemon 0.3.0 does not serve {route}; it needs daemon 0.4.0 or newer" in err, err


def test_a_404_from_a_current_daemon_keeps_its_own_message(monkeypatch, capsys) -> None:
    recorder(monkeypatch, lines_export=NOT_FOUND)
    rc = cli.main(["log", "export", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "error: Not Found" in err and "does not serve" not in err, err


# -- FP-6: one GET /status per command ---------------------------------------------------


GATES = [
    (["plot", "export", "--names", "v", "--from", "10:00", "--decode", "-o", "p.csv"],
     "ignores --from/--to/--decode ("),
    (["-p", "board", "plot", "export", "--names", "v", "--from", "10:00", "-o", "q.csv"],
     "ignores --from/--to/-p ("),
    (["can", "dump", "--csv", "--from", "10:00", "-o", "c.csv"], "ignores --from/--to/--csv ("),
    (["can", "dump", "--csv", "-o", "c.csv"], "ignores --csv ("),
    (["plot", "export", "--names", "v", "--decode", "-o", "p.csv"], "ignores --decode ("),
    (["plot", "export", "--names", "v", "--decode", "--changes", "--deadband", "v=1", "-o",
      "p.csv"], "ignores --decode/--changes/--deadband ("),
]


def test_the_gate_lists_name_every_option_the_cli_gates() -> None:
    """Every flag literal cli.py hands the version gate is named by some gate test's message."""
    import ast

    with open(cli.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    derived = set()
    for node in ast.walk(tree):
        call = getattr(getattr(node, "func", None), "attr", None) or getattr(
            getattr(node, "func", None), "id", None)
        on_gated = getattr(getattr(getattr(node, "func", None), "value", None), "id", None)
        if call in ("require_daemon", "_clock_bounds") or on_gated == "gated":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.startswith("-"):
                    derived.update(sub.value.split("/"))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "gated"
                                                for t in node.targets):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                        and sub.value.startswith("-"):
                    derived.add(sub.value)
    assert len(derived) >= 8, derived
    named = " ".join(msg for _, msg in [*GATES, *GATED]) + (" --from/--to" if BOUNDED else "")
    missing = sorted(flag for flag in derived if flag not in named)
    assert not missing, f"gated in cli.py, named by no gate test: {missing}"


@pytest.mark.parametrize(("argv", "named"), GATES, ids=lambda v: " ".join(v)
                         if isinstance(v, list) else None)
def test_every_gated_option_is_judged_by_one_status_request(monkeypatch, capsys, tmp_path,
                                                            argv, named) -> None:
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch, status=OLD)
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert named in err, err
    assert paths(seen) == ["/status"], paths(seen)
    seen = recorder(monkeypatch, plot_export="ts,name,value\n", can_frames="id\n",
                    plot_channels={"channels": []})
    rc = cli.main([*argv, *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert paths(seen).count("/status") == 1, paths(seen)
