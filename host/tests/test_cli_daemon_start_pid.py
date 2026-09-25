"""Which pid `mcu daemon start` reports: the serving process, and the launcher beside it."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from mcuscope import cli
from tests.test_cli_daemon_stop_scope import _Proc

STATUS = {"version": "9.9.9", "uptime_s": 0, "ports": []}
LAUNCHER = 999997   # the pid Popen hands back


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))


def _start(monkeypatch, capsys, body: dict, *glob: str) -> tuple[int, str, str]:
    """`daemon start` whose Popen pid is LAUNCHER and whose readiness probe answers `body`."""
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_status_or_refusal", lambda s, timeout=2.0: (body, None))
    monkeypatch.setattr(cli, "_open_append", lambda path: open(path, "ab"))  # noqa: SIM115
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: _Proc(LAUNCHER, exited=None))
    rc = cli.main([*glob, "--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "5"])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_a_windows_venv_start_names_the_serving_pid_and_the_launcher(monkeypatch,
                                                                    capsys) -> None:
    """`taskkill` on the printed pid hit the launcher shim, not the daemon."""
    monkeypatch.setattr(sys, "platform", "win32")
    rc, out, err = _start(monkeypatch, capsys, {**STATUS, "pid": 5678, "ppid": LAUNCHER})
    assert rc == 0, err
    assert f"started mcuscoped (pid 5678; launcher {LAUNCHER})" in out, out


def test_the_json_answer_carries_the_serving_pid_and_the_launcher(monkeypatch,
                                                                  capsys) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    rc, out, err = _start(monkeypatch, capsys, {**STATUS, "pid": 5678, "ppid": LAUNCHER},
                          "--json")
    assert rc == 0, err
    body = json.loads(out)
    assert body["pid"] == 5678 and body["launcher_pid"] == LAUNCHER, body


@pytest.mark.parametrize("glob", [(), ("--json",)])
def test_a_start_served_by_the_spawned_process_names_one_pid(monkeypatch, capsys,
                                                             glob) -> None:
    """Positive control: no launcher, so no launcher clause and no `launcher_pid`."""
    rc, out, err = _start(monkeypatch, capsys, {**STATUS, "pid": LAUNCHER}, *glob)
    assert rc == 0, err
    if glob:
        body = json.loads(out)
        assert body["pid"] == LAUNCHER and "launcher_pid" not in body, body
    else:
        assert f"started mcuscoped (pid {LAUNCHER}); web UI:" in out, out
