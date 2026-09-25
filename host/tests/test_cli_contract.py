"""CLI contract tests from the 2026-09-04 adversarial review (C1..C11, D6, D10).

Every path out of `mcu` maps to SPEC 4's 0/1/2/3, with no traceback: these drive the
paths that did not - an unwritable stdout, an interrupt inside a command, an export the
daemon dies under, and the bounds the client was leaving to the daemon.
"""

from __future__ import annotations

import errno
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
import typer

from mcuscope import cli
from mcuscope import cli as cli_module
from mcuscope import protocol as p
from tests.support import Stack, canned, recorder
from tests.test_cli import run_mcu_canned

UNREACHABLE = ["--url", "http://127.0.0.1:1"]


class _FullStdout:
    """A stdout whose every write fails the way a full disk or a quota does.

    No fileno(): _silence_stdout must not repoint the test runner's own descriptor.
    """

    def __init__(self) -> None:
        self.encoding = "utf-8"

    def write(self, text: str) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _canned(monkeypatch, body):
    """Point every request at a current daemon answering `body` as JSON."""
    canned(monkeypatch, lambda request: httpx.Response(200, json=body))


# -- C1 / C10: a stdout that cannot be written -----------------------------------------


def test_json_output_to_a_full_stdout_exits_1(monkeypatch, capsys) -> None:
    _canned(monkeypatch, {"version": "0", "uptime_s": 1, "db_path": "x", "ports": []})
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "status", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, "a write that failed is not a success, and never exit 120"
    assert "cannot write output" in err
    assert "Traceback" not in err


def test_json_rows_to_a_full_stdout_exit_1(monkeypatch, capsys) -> None:
    """The per-row emitter half of the same guard (`mcu --json tail -n 1`)."""
    _canned(monkeypatch, {"lines": [{"id": 1, "ts": 0.0, "chan": "debug", "raw": "hi"}],
                          "truncated": False})
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "tail", "-n", "1", *UNREACHABLE])
    assert rc == 1
    assert "cannot write output" in capsys.readouterr().err


def test_follow_output_error_is_not_reported_as_unreachable(stack: Stack, monkeypatch,
                                                            capsys) -> None:
    """C10: emit_stream's failure is ours, not the daemon's.

    The follow's `except OSError` arm wraps the emit as well as the socket, so a local
    write error was attributed to the daemon and exited 3.
    """
    monkeypatch.setattr(sys, "stdout", _FullStdout())
    rc = cli.main(["--json", "tail", "-n", "0", "-f", "--url", stack.base_url])
    err = capsys.readouterr().err
    assert rc == 1
    assert "unreachable" not in err
    assert "cannot write output" in err


# -- C2: Ctrl-C inside a command --------------------------------------------------------


def test_ctrl_c_inside_a_command_exits_1(monkeypatch, capsys) -> None:
    def boom(self, path: str, **kw: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.Client, "get", boom)
    rc = cli.main(["--json", "status", *UNREACHABLE])
    out, err = capsys.readouterr()
    assert rc == 1, "typer converts it to Exit(130); SPEC 4 says an interrupt is 1"
    assert json.loads(out) == {"error": "interrupted", "exit_code": 1}
    assert "interrupted" in err


# -- C4: a negative count is bad usage, not an empty answer ------------------------------


@pytest.mark.parametrize("args, opt", [
    (["lines", "--limit", "-1"], "--limit"),
    (["tail", "-n", "-1"], "-n"),
    (["can", "dump", "-n", "-1"], "-n"),
    (["session", "list", "--limit", "-1"], "--limit"),
    (["log", "export", "--limit", "-1"], "--limit"),
])
def test_negative_limit_is_bad_usage(monkeypatch, capsys, args, opt) -> None:
    _canned(monkeypatch, {"lines": [], "truncated": False, "sessions": [], "frames": []})
    rc = cli.main([*args, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, "the daemon clamps a negative limit to 0, which reads as 'nothing found'"
    assert opt in err


# -- C5 / C11: timeout and min-window bounds --------------------------------------------


@pytest.mark.parametrize("args", [
    ["cmd", "x", "--timeout", "0"],
    ["wait", "--match", "x", "--timeout", "0"],
])
def test_a_zero_timeout_is_bad_usage_where_the_daemon_needs_one(capsys, args) -> None:
    rc = cli.main([*args, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1
    assert f"expected 1 to {cli.MAX_TIMEOUT_MS} ms" in err, \
        "the exit code alone is satisfied by the daemon's 422"


def test_assert_still_takes_a_zero_timeout(monkeypatch, capsys) -> None:
    """0 is `assert`'s retrospective mode, and stays legal."""
    _canned(monkeypatch, {"status": "pass", "checked_lines": 3, "elapsed_ms": 1.0,
                          "expect": [], "forbid": []})
    rc = cli.main(["assert", "--expect", "x", "--timeout", "0", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


@pytest.mark.parametrize("args, msg", [
    (["--timeout", "1000", "--min-window", "5000"], "--min-window cannot exceed --timeout"),
    (["--min-window", "5000"], "needs a live window"),
    (["--timeout", "1000", "--min-window", "999999999"], "--min-window"),
])
def test_min_window_is_bounded_by_the_client(capsys, args, msg) -> None:
    rc = cli.main(["assert", "--expect", "x", *args, *UNREACHABLE])
    assert rc == 1
    assert msg in capsys.readouterr().err


# -- C8 / C9: the guide and the wire vocabularies ---------------------------------------

# Long spellings of short flags the guide gives (-f, -o); the guide names the short one.
GUIDE_EXEMPT = {"--follow", "--out"}


def _option_strings():
    """Every non-hidden option of every non-hidden subcommand, as (path, flag)."""
    root = typer.main.get_command(cli.app)
    found: list[tuple[str, str]] = []

    def walk(command, path: list[str]) -> None:
        # A group's own params are collected too: the root group carries the global
        # options, which the guide has to name like any other flag.
        for param in command.params:
            for opt in [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]:
                if opt.startswith("-"):
                    found.append((" ".join(path) or "<root>", opt))
        # Duck-typed, not isinstance(click.Group): typer's group does not derive from it
        # under click 8.4.
        for name, sub in (getattr(command, "commands", None) or {}).items():
            if not getattr(sub, "hidden", False):
                walk(sub, [*path, name])

    walk(root, [])
    return found


def commands_with(flag: str) -> set[str]:
    """Every command path taking `flag`: the derived side of a test claiming "every command"."""
    return {path for path, opt in _option_strings() if opt == flag}


def command_of(argv: list[str]) -> str:
    """The command path an argv names: its longest leading run of known command words."""
    paths = {path for path, _ in _option_strings()}
    words: list[str] = []
    for arg in argv:
        prefix = " ".join([*words, arg])
        if not any(p == prefix or p.startswith(prefix + " ") for p in paths):
            break
        words.append(arg)
    return " ".join(words)


def test_the_guide_exemptions_are_real_flags() -> None:
    assert GUIDE_EXEMPT <= {opt for _, opt in _option_strings()}, "a stale exemption"


def test_ai_guide_names_every_flag() -> None:
    # As a whole token: `-c` inside `--color` or `mcu-crash` is not the guide naming it.
    missing = [(cmd, opt) for cmd, opt in _option_strings()
               if opt not in GUIDE_EXEMPT
               and not re.search(rf"(?<![\w-]){re.escape(opt)}(?![\w-])", cli.AI_GUIDE)]
    assert not missing, f"AI_GUIDE (the agent's only view of the CLI) omits: {missing}"


def test_cli_eol_choices_match_the_protocol() -> None:
    assert set(cli.EOL_CHOICES) == set(p.EOL_BYTES)
    assert (cli.BUS_OPTION.min, cli.BUS_OPTION.max) == (p.CAN_BUS_MIN, p.CAN_BUS_MAX)


# -- D10: sysrq takes one printable character --------------------------------------------


@pytest.mark.parametrize("char", ["\n", "\r", "\x00"])
def test_sysrq_refuses_a_non_printable_character(capsys, char) -> None:
    rc = cli.main(["sysrq", char, *UNREACHABLE])
    assert rc == 1, ("a non-printable SysRq character is bad usage, not a write the daemon "
                     "has to refuse")
    assert "printable" in capsys.readouterr().err


def test_sysrq_still_takes_one_printable_character(monkeypatch, capsys) -> None:
    _canned(monkeypatch, {"ok": True})
    assert cli.main(["sysrq", "b", *UNREACHABLE]) == 0, capsys.readouterr().err


# -- R1: an export that could not open must not delete the file ---------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 444 does not deny write on Windows")
def test_log_export_keeps_a_file_it_could_not_open(monkeypatch, tmp_path, capsys) -> None:
    """The removal guard is armed only after the open succeeds.

    The daemon answers, because the open follows its first answer.
    """
    canned(monkeypatch, lambda request: httpx.Response(200, text="a line\n"))
    target = tmp_path / "keep.txt"
    target.write_text("PRECIOUS DATA\n", encoding="utf-8")
    target.chmod(0o444)
    rc = cli.main(["log", "export", "-o", str(target), *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "cannot write" in err
    assert target.exists(), "a file this command never opened must survive the failure"
    assert target.read_text(encoding="utf-8") == "PRECIOUS DATA\n"


# -- R3: --eol without --send is bad usage, in the daemon's words -------------------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x", "--eol", "crlf", "--timeout", "1000"],
    ["assert", "--expect", "x", "--eol", "crlf", "--timeout", "1000"],
])
def test_eol_without_send_is_refused_client_side(capsys, argv) -> None:
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--eol applies to --send; give --send too" in err


# -- improvement 7 (CLI half): a daemon shutting down under a long poll -----------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x"],
    ["assert", "--expect", "x", "--timeout", "1000"],
])
def test_a_503_from_the_daemon_is_unreachable_not_an_error(monkeypatch, capsys,
                                                           argv) -> None:
    """SPEC 4 codes "the daemon is not there" 3, and a shutdown mid-wait is that."""
    msg = "daemon is shutting down; the wait was cut short"
    canned(monkeypatch, lambda request: httpx.Response(503, json={"error": msg}))
    rc = cli.main([*argv, *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert msg in err, err


def test_an_ordinary_400_is_still_exit_1(monkeypatch, capsys) -> None:
    """Only 503 moves; a bad request is still the user's error."""
    canned(monkeypatch, lambda request: httpx.Response(400, json={"error": "bad regex"}))
    rc = cli.main(["wait", "--match", "(", *UNREACHABLE])
    assert rc == 1, capsys.readouterr().err


# -- FC-6: the guide says what SPEC 4 says about a wait that is never answered ------------

def test_the_guide_gives_mcu_wait_the_exit_code_spec_4_gives_it() -> None:
    block = cli.AI_GUIDE.split("THE CORE LOOP")[1].split("mcu lines")[0]
    assert "never answers is exit 1, not 2" in block, block


def test_the_guide_still_names_the_wait_timeout_verdict() -> None:
    """Positive control: the clause is added to the wait block, not instead of it."""
    assert "exit 2 on" in cli.AI_GUIDE and "too many subscribers" in cli.AI_GUIDE


def test_the_guide_names_the_post_spawn_refusal() -> None:
    """FC-3's behaviour, in the one place an agent reads (SPEC 4 carries the same sentence)."""
    block = cli.AI_GUIDE.split("DAEMON CONTROL")[1]
    assert "exit 0 with a note that it wants a token" in block, block


# -- G-1: only the shutdown 503 is "unreachable" -----------------------------------------


@pytest.mark.parametrize("argv", [
    ["wait", "--match", "x"],
    ["assert", "--expect", "x", "--timeout", "1000"],
])
def test_the_subscriber_cap_503_is_exit_1_not_unreachable(monkeypatch, capsys, argv) -> None:
    """A daemon at its subscriber cap is running; exit 3 sends an agent to restart it."""
    msg = "too many subscribers (max 256)"
    canned(monkeypatch, lambda request: httpx.Response(503, json={"error": msg}))
    rc = cli.main(["--json", *argv, *UNREACHABLE])
    out = capsys.readouterr()
    assert rc == 1, out.err
    assert json.loads(out.out) == {"error": f"error: {msg}", "exit_code": 1}


def test_a_503_that_only_mentions_shutdown_is_not_the_shutdown_answer(monkeypatch,
                                                                     capsys) -> None:
    """The prefix is the daemon's own sentence, not a substring anywhere in a proxy page."""
    canned(monkeypatch, lambda request: httpx.Response(
        503, text="upstream says: daemon is shutting down"))
    rc = cli.main(["wait", "--match", "x", *UNREACHABLE])
    assert rc == 1, capsys.readouterr().err


# -- CLI --json contract (SPEC 4) -----------------------------------------------------


def test_only_the_documented_commands_emit_jsonl() -> None:
    """SPEC 4 exempts named commands from "exactly one JSON object", and missed one.

    `mcu can dump` prints one object per frame, exactly like `mcu tail`, and for the same
    reason: its `-f` form is an unbounded live stream. The exemption sentence was corrected
    for `tail` in the previous round while `can dump` went unlisted, so the sweep read as
    passing with a live instance in it.

    Enumeration is what failed, so the enumeration is pinned here rather than re-read: a
    per-row emitter is `out_json` inside a loop, and a new one fails this test until SPEC
    names it deliberately.
    """
    import ast
    import pathlib

    src = pathlib.Path(cli_module.__file__).read_text(encoding="utf-8")
    emitters = set()
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for loop in (n for n in ast.walk(fn) if isinstance(n, ast.For | ast.While)):
            for call in ast.walk(loop):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                    continue
                arg = call.args[0] if call.args else None
                if isinstance(arg, ast.IfExp):     # json.dumps(row) if s.json_out else fmt(row)
                    arg = arg.body
                dumps = (
                    call.func.id == "emit_stream"
                    and isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "dumps"
                )
                if call.func.id == "out_json" or dumps:
                    emitters.add(fn.name)

    # `log_export` writes its JSONL through a shared text branch rather than a loop over
    # out_json, so it is documented in SPEC but not detectable by this shape.
    # `_tail_snapshot` is where `mcu tail` prints its recent-lines snapshot: the follow
    # path opens /ws before fetching it, so the loop lives in a helper the command calls
    # rather than in the command body. Same emitter, same SPEC exemption.
    assert emitters == {"_tail_snapshot", "can_dump"}

    spec = (pathlib.Path(__file__).parents[2] / "docs" / "SPEC.md").read_text(encoding="utf-8")
    for documented in ("`mcu log export`", "`mcu tail`", "`mcu can dump`"):
        assert documented in spec


# -- F2: every dispatcher arm emits exactly one JSON object under --json ----------------


def test_json_mode_gets_one_object_from_the_key_error_arm(monkeypatch, capsys) -> None:
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json={"ok": True}),
        "--json", "purge", "--all", "-y",
    )
    assert rc == 1
    assert json.loads(out) == {
        "error": "unexpected response from daemon: 'deleted'", "exit_code": 1
    }


def _run_json_status_raising(monkeypatch, capsys, exc: BaseException):
    """`mcu --json status` where the request raises `exc` from inside the command."""
    from mcuscope import cli

    def boom(self, path: str, **kw: object) -> None:
        raise exc

    monkeypatch.setattr(cli.Client, "get", boom)
    rc = cli.main(["--json", "status", "--url", "http://127.0.0.1:1"])
    out, err = capsys.readouterr()
    return rc, out, err


def test_json_mode_gets_one_object_from_the_abort_arm(monkeypatch, capsys) -> None:
    """Declining a confirmation prompt: click's Abort, on the --json contract."""
    rc, out, err = _run_json_status_raising(monkeypatch, capsys, typer.Abort())
    assert rc == 1
    assert json.loads(out) == {"error": "aborted", "exit_code": 1}
    assert "aborted" in err


def test_json_mode_gets_one_object_from_the_keyboard_interrupt_arm(monkeypatch, capsys) -> None:
    """A Ctrl-C the app call did not convert.

    typer turns an interrupt raised *inside* a command into Exit(130) (typer/core.py), so
    this arm covers one that escapes the call itself; it is driven here at that boundary
    rather than through a command, which cannot reach it.
    """
    from mcuscope import cli

    def boom(**kw: object) -> int:
        raise KeyboardInterrupt

    # The hoist result this argv really produces; stubbed because the app it walks is the
    # object being replaced below.
    monkeypatch.setattr(cli, "_split_global_opts", lambda argv: (["--json"], ["status"]))
    monkeypatch.setattr(cli, "app", boom)
    rc = cli.main(["--json", "status"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert json.loads(out) == {"error": "interrupted", "exit_code": 1}
    assert "interrupted" in err


def test_a_value_error_from_the_transport_is_mapped_not_raised(monkeypatch, capsys) -> None:
    """_daemon_errors covers ValueError, which httpx raises while encoding a request."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise UnicodeEncodeError("ascii", "x", 0, 1, "not ascii")

    rc, out, err = run_mcu_canned(monkeypatch, capsys, handler, "status")
    assert rc == 1
    assert "cannot send request to" in err


# -- RG-F9: a millisecond timeout is bounded client-side --------------------------------


@pytest.mark.parametrize("command", ["cmd", "wait", "assert"])
def test_ms_timeout_out_of_range_is_a_usage_refusal(monkeypatch, capsys, command: str) -> None:
    """`--timeout 99999999999999999999` raised OverflowError out of httpx."""
    args = {
        "cmd": ["cmd", "x"], "wait": ["wait", "--match", "x"],
        "assert": ["assert", "--expect", "x"],
    }[command]
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json={}),
        *args, "--timeout", "99999999999999999999",
    )
    assert rc == 1
    assert "300000 ms" in err
    assert "OverflowError" not in err


# -- RG-F10: daemon fields are vouched for at the point of use ---------------------------


@pytest.mark.parametrize(
    ("args", "body", "key"),
    [
        (["status"], {"version": "1", "uptime_s": 1.0, "db_path": "x", "ports": [],
                      "session": "notadict"}, "'session'"),
        (["attach", "/dev/null"], {"port": "notadict"}, "'port'"),
        (["assert", "--expect", "x"], {"status": "fail", "checked_lines": 0,
                                       "elapsed_ms": 1, "expect": None, "forbid": []},
         "'expect'"),
        (["assert", "--forbid", "x"], {"status": "fail", "checked_lines": 0,
                                       "elapsed_ms": 1, "expect": [], "forbid": None},
         "'forbid'"),
        (["session", "start", "run1"], {"session": "notadict"}, "'session'"),
        (["session", "stop"], {"session": "notadict"}, "'session'"),
    ],
)
def test_a_wrongly_typed_daemon_field_is_reported_not_a_traceback(
    monkeypatch, capsys, args: list[str], body: dict, key: str
) -> None:
    rc, out, err = run_mcu_canned(
        monkeypatch, capsys, lambda request: httpx.Response(200, json=body), *args
    )
    assert rc == 1
    assert "unexpected response from daemon" in err and key in err
    assert "Traceback" not in err


# -- R35 (class 35): a closed stdout must not turn an error exit into 0 -------------------


def test_json_error_exit_survives_a_closed_stdout() -> None:
    """`mcu --json status | head -0` still exits 3 when the daemon is unreachable.

    die() writes its JSON object to stdout, and that write raising BrokenPipeError landed
    in the dispatcher's broken-pipe arm, which answers 0: every --json error exit did.
    """
    from tests.test_cli import run_mcu_closed_pipe

    rc, _ = run_mcu_closed_pipe(None, "--json", "status", url="http://127.0.0.1:1")
    assert rc == 3


# -- FC-8: the child suites move the config and cache dirs too ----------------------------

@pytest.mark.parametrize("name", ["test_cli_closed_pipe", "test_cli_closed_output"])
def test_the_child_suites_build_their_env_from_child_env(name) -> None:
    """A spawned child's config and cache dirs are not the user's (class 33): child_env
    moves all three, dict(os.environ, ...) moves none."""
    src = (Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8")
    assert "child_env(" in src, name
    assert "dict(os.environ" not in src, name


# -- B-2 / B-3: each typed field reaches the daemon ---------------------------------------


FIELDS = [
    (["-p", "nosuch", "plot", "export", "--names", "ramp"], "/plot/export", "port", "nosuch"),
    (["attach", "socket://127.0.0.1:1", "--alias", "e1", "--eol", "crlf"], "/ports", "eol",
     "crlf"),
    (["send", "eoltest", "--eol", "none"], "/send", "eol", "none"),
    (["cmd", "ping", "--eol", "crlf"], "/cmd", "eol", "crlf"),
    (["wait", "--match", "x", "--send", "ping", "--eol", "none"], "/wait", "eol", "none"),
    (["assert", "--expect", "x", "--send", "ping", "--eol", "none"], "/assert", "eol", "none"),
    (["wait", "--match", "ZZZ", "--send", "", "--repeat-ms", "50", "--timeout", "300"],
     "/wait", "repeat_ms", 50),
]


@pytest.mark.parametrize(("argv", "path", "key", "value"), FIELDS,
                         ids=lambda v: " ".join(v) if isinstance(v, list) else None)
def test_a_typed_field_reaches_the_daemon(monkeypatch, capsys, tmp_path, argv, path, key,
                                          value) -> None:
    """Judged on the request, not the exit code: the recorder answers 200 where a real
    daemon would refuse some of these (no port `nosuch`)."""
    monkeypatch.chdir(tmp_path)
    seen = recorder(monkeypatch)
    cli.main([*argv, *UNREACHABLE])
    capsys.readouterr()
    sent = [r for r in seen if r.url.path == path and r.method != "GET"] or \
        [r for r in seen if r.url.path == path]
    assert len(sent) == 1, [(r.method, r.url.path) for r in seen]
    fields = json.loads(sent[0].content) if sent[0].method == "POST" else \
        dict(sent[0].url.params)
    assert fields.get(key) == value, fields


def test_a_404_keeps_the_daemons_own_message(monkeypatch, capsys) -> None:
    recorder(monkeypatch, lines_export=(404, {"error": "Not Found"}))
    rc = cli.main(["log", "export", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1 and "error: Not Found" in err, err
