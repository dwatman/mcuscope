"""CLI usability round (2026-09-07): start hints, daemon stderr, restart, --open, ports,
lines --order, the ambiguous-port hint, `config path` and shell completion."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest

from mcuscope import cli, cli_client
from mcuscope.config import default_config_path
from tests.support import CHILD_TEXT, Stack
from tests.test_cli import MCU, _answers, _daemon_config, _spawn_env, run_mcu

DEAD = "http://127.0.0.1:1"
OTHER_DEAD = "http://127.0.0.1:2"


@pytest.fixture
def default_is_dead(monkeypatch):
    """Make the default daemon url one nothing answers at, without touching port 8558."""
    monkeypatch.setattr(cli_client, "DEFAULT_URL", DEAD)
    monkeypatch.setattr(cli, "DEFAULT_URL", DEAD)
    monkeypatch.delenv("MCUSCOPE_URL", raising=False)


# -- 1: the unreachable hint ---------------------------------------------------------------


def test_unreachable_at_the_default_url_says_how_to_start_one(default_is_dead, capsys) -> None:
    assert cli.main(["status"]) == 3
    err = capsys.readouterr().err
    assert "daemon unreachable" in err
    assert "mcu daemon start" in err and "mcuscoped" in err


def test_unreachable_at_a_custom_url_gets_no_start_hint(default_is_dead, capsys) -> None:
    assert cli.main(["status", "--url", OTHER_DEAD]) == 3
    err = capsys.readouterr().err
    assert "daemon unreachable" in err
    assert "daemon start" not in err, "a custom --url names a daemon that is not ours to start"


def test_unreachable_via_the_env_url_gets_no_start_hint(default_is_dead, monkeypatch,
                                                        capsys) -> None:
    monkeypatch.setenv("MCUSCOPE_URL", OTHER_DEAD)
    assert cli.main(["status"]) == 3
    assert "daemon start" not in capsys.readouterr().err


def test_follow_unreachable_at_the_default_url_hints_too(default_is_dead, capsys) -> None:
    """The websocket follow has its own unreachable arm (cli.py), not cli_client's."""
    assert cli.main(["tail", "-n", "0", "-f"]) == 3
    assert "mcu daemon start" in capsys.readouterr().err


# -- 2 / 4: daemon start's stderr file, the UI url and --open --------------------------------


class _FakeDaemon:
    """A Popen stand-in: writes `stderr_lines` to the handle it was given, then `exit`s."""

    stderr_lines: list[str] = []
    exit: int | None = 2
    spawned: list[_FakeDaemon] = []

    def __init__(self, args, **kwargs) -> None:
        self.pid = 4242
        self.args = args
        self.kwargs = kwargs
        fh = kwargs["stderr"]
        assert fh is not subprocess.DEVNULL, "the daemon's stderr must land somewhere readable"
        fh.write("".join(f"{line}\n" for line in self.stderr_lines).encode())
        fh.flush()
        _FakeDaemon.spawned.append(self)

    def poll(self):
        return self.exit

    def terminate(self) -> None:
        self.exit = -15

    def kill(self) -> None:
        self.exit = -9

    def wait(self, timeout=None):
        return self.exit


@pytest.fixture
def fake_spawn(monkeypatch, tmp_path):
    pid_path = str(tmp_path / "mcuscoped-127.0.0.1-1.pid")
    monkeypatch.setattr(cli, "_pid_file", lambda s: pid_path)
    monkeypatch.setattr(cli.subprocess, "Popen", _FakeDaemon)
    monkeypatch.setattr(_FakeDaemon, "spawned", [])
    monkeypatch.setattr(_FakeDaemon, "exit", 2)
    monkeypatch.setattr(_FakeDaemon, "stderr_lines", [f"line-{i:02d}" for i in range(12)])
    return tmp_path


def test_a_failed_start_shows_the_tail_of_the_daemons_stderr(fake_spawn, capsys) -> None:
    err_path = fake_spawn / "mcuscoped-127.0.0.1-1.err"
    err_path.write_text("OLD JUNK from a previous start\n", encoding="utf-8")
    rc = cli.main(["daemon", "start", "--url", DEAD, "--timeout", "0.2"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "exited with status 2" in err
    assert f"last 10 lines of {err_path}" in err
    assert "line-02" in err and "line-11" in err
    assert "line-01\n" not in err, "more than the last 10 lines"
    assert "OLD JUNK" not in err, "the file is truncated on every start"
    assert "OLD JUNK" not in err_path.read_text(encoding="utf-8")
    assert not (fake_spawn / "mcuscoped-127.0.0.1-1.pid").exists()


def test_a_failed_start_with_an_empty_stderr_file_shows_no_tail(fake_spawn, monkeypatch,
                                                                capsys) -> None:
    monkeypatch.setattr(_FakeDaemon, "stderr_lines", [])
    assert cli.main(["daemon", "start", "--url", DEAD, "--timeout", "0.2"]) == 1
    err = capsys.readouterr().err
    assert "exited with status 2" in err
    assert "last" not in err and ".err" not in err


def _answering(monkeypatch, pid: int, absent_first: int = 0) -> list[int]:
    """/status answers for `pid` after `absent_first` probes; returns the probe log."""
    probes: list[int] = []

    def status_body(s, timeout=2.0):
        probes.append(1)
        if len(probes) <= absent_first:
            return None
        return {"version": "0", "uptime_s": 0.0, "ports": [], "pid": pid}

    monkeypatch.setattr(cli, "_status_body", status_body)
    return probes


def test_start_prints_the_web_ui_url_and_opens_it_only_on_request(fake_spawn, monkeypatch,
                                                                  capsys) -> None:
    monkeypatch.setattr(_FakeDaemon, "exit", None)
    _answering(monkeypatch, 4242, absent_first=1)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert cli.main(["daemon", "start", "--url", DEAD]) == 0
    assert f"web UI: {DEAD}/ui/" in capsys.readouterr().out
    assert opened == [], "--open was not given"

    _answering(monkeypatch, 4242, absent_first=1)
    assert cli.main(["--json", "daemon", "start", "--url", DEAD]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": True, "pid": 4242, "ui_url": f"{DEAD}/ui/"}
    assert opened == []

    _answering(monkeypatch, 4242, absent_first=1)
    assert cli.main(["daemon", "start", "--url", DEAD, "--open"]) == 0
    capsys.readouterr()
    assert opened == [f"{DEAD}/ui/"]


def test_open_with_json_is_refused_before_anything_is_spawned(fake_spawn, monkeypatch,
                                                              capsys) -> None:
    """The browser command inherits stdout (BROWSER=/bin/echo printed the url after the
    JSON object), so the pair is refused rather than left to corrupt --json output."""
    monkeypatch.setattr(_FakeDaemon, "exit", None)
    probes = _answering(monkeypatch, 4242)
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail("must not open"))
    for cmd in ("start", "restart"):
        rc = cli.main(["--json", "daemon", cmd, "--url", DEAD, "--open"])
        out, err = capsys.readouterr()
        assert rc == 1
        assert "--open cannot be combined with --json" in err
        assert json.loads(out)["exit_code"] == 1, "the refusal is a JSON error object"
    assert _FakeDaemon.spawned == [], "refused before the spawn"
    assert probes == [] or cmd == "restart", "start must not even probe"


def test_restart_carries_the_running_daemons_config_and_sim(fake_spawn, monkeypatch,
                                                            capsys) -> None:
    """`start -c x --sim` then a bare `restart` came back on the default config with no sim
    port; the running daemon's config_path (/status) and sim port (/ports) are carried."""
    monkeypatch.setattr(_FakeDaemon, "exit", None)
    probes: list[int] = []

    def status_body(s, timeout=2.0):
        probes.append(1)
        body = {"version": "0", "uptime_s": 0.0, "ports": [], "pid": 4242}
        if len(probes) == 1:
            body["config_path"] = "/etc/running.toml"
        if len(probes) == 2:
            return None   # start's own "already running" check, after the stop
        return body

    monkeypatch.setattr(cli, "_status_body", status_body)
    monkeypatch.setattr(cli.Client, "probe", lambda self, m, path: {"ports": [
        {"alias": "sim", "device": "sim://demo"}]})
    monkeypatch.setattr(cli, "_stop_daemon", lambda s, quiet=False: None)
    assert cli.main(["daemon", "restart", "--url", DEAD]) == 0
    args = _FakeDaemon.spawned[0].args
    assert "--sim" in args and args[args.index("--config") + 1] == "/etc/running.toml"
    # An explicit -c wins over the running one; no sim port means no --sim.
    monkeypatch.setattr(cli.Client, "probe", lambda self, m, path: {"ports": []})
    probes.clear()
    _FakeDaemon.spawned.clear()
    assert cli.main(["daemon", "restart", "--url", DEAD, "-c", "mine.toml"]) == 0
    args = _FakeDaemon.spawned[0].args
    assert "--sim" not in args and args[args.index("--config") + 1] == "mine.toml"


def test_an_unwritable_stderr_log_falls_back_to_devnull_with_a_warning(fake_spawn, monkeypatch,
                                                                       capsys) -> None:
    monkeypatch.setattr(_FakeDaemon, "exit", None)
    _answering(monkeypatch, 4242, absent_first=1)
    monkeypatch.setattr(cli, "_stderr_log_path",
                        lambda pid_path: os.path.join(pid_path + ".nodir", "x.err"))
    real_init = _FakeDaemon.__init__

    def init_accepting_devnull(self, args, **kwargs):
        assert kwargs["stderr"] is subprocess.DEVNULL
        self.pid, self.args, self.kwargs = 4242, args, kwargs
        _FakeDaemon.spawned.append(self)

    monkeypatch.setattr(_FakeDaemon, "__init__", init_accepting_devnull)
    try:
        assert cli.main(["daemon", "start", "--url", DEAD]) == 0
    finally:
        monkeypatch.setattr(_FakeDaemon, "__init__", real_init)
    out, err = capsys.readouterr()
    assert "cannot write the daemon log" in err or "daemon log" in err, err
    assert "started mcuscoped" in out


def test_the_alias_hint_is_left_off_when_ports_cannot_be_listed(monkeypatch, capsys) -> None:
    class _Resp:
        status_code = 400
        text = '{"error": "port is ambiguous; specify one"}'
        def json(self):
            return json.loads(self.text)

    c = cli_client.Client(cli_client.Settings(url=DEAD, json_out=False, port=None, token=None))
    for probe in (lambda m, p: None, lambda m, p: {"ports": [{"device": "x"}]}):
        monkeypatch.setattr(c, "probe", probe)
        with pytest.raises(cli.typer.Exit) as ex:
            c.fail(_Resp())
        assert ex.value.exit_code == 1
        err = capsys.readouterr().err
        assert "port is ambiguous" in err and "one of:" not in err


def test_daemon_status_hints_how_to_start_at_the_default_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    assert cli.main(["daemon", "status"]) == 3
    assert "not running; start it with 'mcu daemon start'" in capsys.readouterr().out
    assert cli.main(["daemon", "status", "--url", DEAD]) == 3
    assert capsys.readouterr().out.strip() == "not running"


def test_status_reports_the_config_path(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "status")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["config_path"].endswith(".toml")


def test_open_is_not_honoured_when_the_start_fails(fake_spawn, monkeypatch, capsys) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert cli.main(["daemon", "start", "--url", DEAD, "--timeout", "0.2", "--open"]) == 1
    assert opened == []


# -- 3: daemon restart ------------------------------------------------------------------------


def test_restart_with_no_daemon_running_just_starts_one(fake_spawn, monkeypatch,
                                                        capsys) -> None:
    monkeypatch.setattr(_FakeDaemon, "exit", None)
    _answering(monkeypatch, 4242, absent_first=2)   # restart's check, then start's own
    stops: list[object] = []
    monkeypatch.setattr(cli, "_stop_daemon", lambda s, quiet=False: stops.append(s))
    rc = cli.main(["--json", "daemon", "restart", "--url", DEAD, "--sim", "-c", "x.toml"])
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert stops == [], "nothing to stop must not turn into an exit 1 from `stop`"
    assert "no daemon running" in err
    assert json.loads(out)["pid"] == 4242, "exactly one JSON object, the start's"
    args = _FakeDaemon.spawned[0].args
    assert "--sim" in args and args[args.index("--config") + 1] == "x.toml"


_PIDDIR_ENV_SKIP = pytest.mark.skipif(
    os.name == "nt",
    reason="platformdirs resolves the Windows data dir via the shell API, not env vars",
)


@_PIDDIR_ENV_SKIP
def test_restart_of_a_running_daemon_swaps_the_pid(tmp_path) -> None:
    from tests.support import free_port

    data_home = str(tmp_path / "data")
    url = f"http://127.0.0.1:{free_port()}"
    env = _spawn_env(data_home, url)
    cfg = _daemon_config(tmp_path, "restart")

    def mcu(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*MCU, *args], capture_output=True, **CHILD_TEXT, timeout=90,
                              env=env)

    started = mcu("--json", "daemon", "start", "-c", cfg)
    try:
        assert started.returncode == 0, started.stderr
        first = json.loads(started.stdout)["pid"]
        restarted = mcu("--json", "daemon", "restart", "-c", cfg)
        assert restarted.returncode == 0, restarted.stderr
        body = json.loads(restarted.stdout)      # one object, or this raises
        assert body["ok"] is True and body["pid"] != first
        assert body["ui_url"] == f"{url}/ui/"
        assert _answers(url)
        port = url.rsplit(":", 1)[1]
        err_log = os.path.join(data_home, "mcuscope", f"mcuscoped-127.0.0.1-{port}.err")
        assert os.path.exists(err_log)
    finally:
        stopped = mcu("daemon", "stop")
    assert stopped.returncode == 0, stopped.stderr
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _answers(url):
        time.sleep(0.2)
    assert not _answers(url)


# -- 7: ports with none attached -------------------------------------------------------------


def test_ports_with_none_attached_says_so_in_text_only(stack: Stack) -> None:
    assert run_mcu(stack, "detach", stack.alias).returncode == 0
    r = run_mcu(stack, "ports")
    assert r.returncode == 0
    assert "no ports attached" in r.stdout and "mcu attach" in r.stdout
    j = run_mcu(stack, "--json", "ports")
    assert json.loads(j.stdout)["ports"] == []
    assert "no ports" not in j.stdout


# -- 8: lines --order ---------------------------------------------------------------------------


def _mark_three(stack: Stack) -> None:
    for text in ("first", "second", "third"):
        assert run_mcu(stack, "mark", text).returncode == 0


def test_lines_order_desc_reverses_the_text_output(stack: Stack) -> None:
    _mark_three(stack)
    default = run_mcu(stack, "lines", "--chan", "marker", "--limit", "3").stdout.splitlines()
    desc = run_mcu(stack, "lines", "--chan", "marker", "--limit", "3", "--order", "desc")
    assert desc.returncode == 0, desc.stderr
    assert desc.stdout.splitlines() == default[::-1]
    assert default[0].endswith("first") and desc.stdout.splitlines()[0].endswith("third")
    asc = run_mcu(stack, "lines", "--chan", "marker", "--limit", "3", "--order", "asc")
    assert asc.stdout.splitlines() == default


def test_lines_order_asc_reverses_the_json_output(stack: Stack) -> None:
    _mark_three(stack)
    base = ["--json", "lines", "--chan", "marker", "--limit", "3"]
    default = json.loads(run_mcu(stack, *base).stdout)["lines"]
    asc = json.loads(run_mcu(stack, *base, "--order", "asc").stdout)["lines"]
    desc = json.loads(run_mcu(stack, *base, "--order", "desc").stdout)["lines"]
    assert [r["id"] for r in default] == sorted((r["id"] for r in default), reverse=True)
    assert asc == default[::-1]
    assert desc == default


def test_lines_order_rejects_anything_else(capsys) -> None:
    assert cli.main(["lines", "--order", "newest", "--url", DEAD]) == 1
    assert "--order" in capsys.readouterr().err


# -- 9: an ambiguous port names the choices ---------------------------------------------------


def test_an_ambiguous_port_lists_the_aliases(stack: Stack) -> None:
    import socket

    # A second port whose device answers: both are connected, so the choice is ambiguous.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    device = f"socket://127.0.0.1:{srv.getsockname()[1]}"
    try:
        assert run_mcu(stack, "attach", device, "--alias", "spare").returncode == 0
        r = run_mcu(stack, "send", "hello")
        assert r.returncode == 1
        assert "port is ambiguous" in r.stderr
        assert "-p" in r.stderr and "board" in r.stderr and "spare" in r.stderr
        ok = run_mcu(stack, "-p", stack.alias, "send", "hello")
        assert ok.returncode == 0, ok.stderr
    finally:
        run_mcu(stack, "detach", "spare")
        srv.close()


def test_a_sole_connected_port_is_not_ambiguous(stack: Stack) -> None:
    from tests.support import free_port

    # A second attached port with nothing listening stays disconnected (retrying), so an
    # unnamed send lands on the one that is up rather than being refused.
    device = f"socket://127.0.0.1:{free_port()}"
    assert run_mcu(stack, "attach", device, "--alias", "spare").returncode == 0
    try:
        r = run_mcu(stack, "send", "hello")
        assert r.returncode == 0, r.stderr
        assert "ambiguous" not in r.stderr
    finally:
        run_mcu(stack, "detach", "spare")


def test_port_help_names_the_rule() -> None:
    # Colourless (CI threads ANSI codes through the phrase) and wide (typer reads
    # TERMINAL_WIDTH); the box is stripped and line breaks collapsed as a second guard
    r = run_mcu(None, "--help", url=DEAD,
                env_extra={"TERMINAL_WIDTH": "200", "TERM": "dumb", "NO_COLOR": "1"})
    assert r.returncode == 0
    out = " ".join(re.sub(r"[^ -~]", " ", r.stdout).split())
    assert "required when several are connected" in out
    assert "--show-completion" in out and "--install-completion" in out


# -- 10: config path ------------------------------------------------------------------------------


def test_config_path_prints_the_default_location(capsys) -> None:
    assert cli.main(["config", "path", "--url", DEAD]) == 0
    assert capsys.readouterr().out.strip() == str(default_config_path())
    assert cli.main(["--json", "config", "path", "--url", DEAD]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": str(default_config_path())}


def test_config_path_does_not_need_a_daemon(capsys) -> None:
    assert cli.main(["config", "path", "--url", "http://127.0.0.1:1"]) == 0
    assert capsys.readouterr().out.strip().endswith("config.toml")


# -- 5 / 12: startup weight and the guide ---------------------------------------------------


def test_httpx_is_not_imported_for_help_version_or_the_guide() -> None:
    code = ("import sys, mcuscope.cli as c\n"
            "for argv in (['--help'], ['--version'], ['ai-guide']):\n"
            "    c.main(argv)\n"
            "print('HTTPX' if 'httpx' in sys.modules else 'CLEAN')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, **CHILD_TEXT,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip().endswith("CLEAN"), r.stdout[-200:]


def test_the_guide_gives_the_powershell_control_character_form() -> None:
    assert "([char]3)" in cli.AI_GUIDE
    assert "--order" in cli.AI_GUIDE and "mcu config path" in cli.AI_GUIDE
