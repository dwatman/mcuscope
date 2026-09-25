"""`/status` `ppid` and the port rows' `serial_number`, and the CLI paths that read them.

`mcu daemon start` and `stop` corroborate a Windows venv launcher shim through `ppid`; `mcu
attach` tells a serial binding from a device through `serial_number`.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap

import httpx
import pytest

from mcuscope import cli, cli_daemonctl
from tests.support import Stack, child_env, free_port
from tests.test_cli import run_mcu


def test_status_reports_the_parent_and_port_rows_the_serial_binding(stack: Stack) -> None:
    with httpx.Client(base_url=stack.base_url, timeout=20.0) as c:
        status = c.get("/status").json()
        assert status["pid"] == os.getpid() and status["ppid"] == os.getppid()
        assert c.get("/ports").json()["ports"][0]["serial_number"] is None   # a device attach
        c.post("/ports", json={"alias": "b2", "serial_number": "ZZ99"})
        row = next(p for p in c.get("/ports").json()["ports"] if p["alias"] == "b2")
        assert row["serial_number"] == "ZZ99" and row["device"] == "ZZ99"


def test_reattaching_the_same_serial_prints_no_retarget_note(stack: Stack) -> None:
    r = run_mcu(stack, "attach", "--serial", "ZZ99", "--alias", "b2")
    assert r.returncode == 0, r.stderr
    r = run_mcu(stack, "attach", "--serial", "ZZ99", "--alias", "b2")
    assert r.returncode == 0, r.stderr
    assert "note:" not in r.stderr, r.stderr
    # Positive control: a real retarget is still named, the old binding as a serial.
    r = run_mcu(stack, "attach", "socket://127.0.0.1:1", "--alias", "b2")
    assert r.returncode == 0, r.stderr
    assert "note: b2 was attached to serial ZZ99; it now names socket://127.0.0.1:1" in r.stderr


class _Sys:
    """`sys` with some attributes replaced, for one module's view of it."""

    def __init__(self, **over: object) -> None:
        self.__dict__.update(over)

    def __getattr__(self, name: str) -> object:
        return getattr(sys, name)


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang shim stands in for the venv one")
def test_a_daemon_behind_a_launcher_shim_starts_and_stops(tmp_path, monkeypatch, capsys) -> None:
    """A Windows venv launcher runs the interpreter as its child, so the pid `daemon start`
    spawned and recorded is the daemon's parent. Driven here with a real shim and a real
    daemon; only the platform check in cli_daemonctl reads win32."""
    shim = tmp_path / "python-shim"
    shim.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import subprocess, sys
        sys.exit(subprocess.call([{sys.executable!r}, *sys.argv[1:]]))
        """), encoding="utf-8", newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    cfg = tmp_path / "shim.toml"
    cfg.write_text(f'[storage]\ndb_path = "{(tmp_path / "cap.db").as_posix()}"\n',
                   encoding="utf-8", newline="\n")
    for key, value in child_env(str(tmp_path)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "sys", _Sys(executable=str(shim)))
    monkeypatch.setattr(cli_daemonctl, "sys", _Sys(platform="win32"))
    monkeypatch.setattr(cli, "_open_append", lambda path: open(path, "ab"))  # noqa: SIM115
    url = f"http://127.0.0.1:{free_port()}"
    try:
        rc = cli.main(["--url", url, "daemon", "start", "--config", str(cfg), "--timeout", "30"])
        out, err = capsys.readouterr()
        assert rc == 0, err
        shim_pid = int(out.split("; launcher ")[1].split(")")[0])
        status = httpx.get(f"{url}/status", timeout=5).json()
        assert status["ppid"] == shim_pid and status["pid"] != shim_pid
        assert f"(pid {status['pid']}; launcher {shim_pid})" in out
        rc = cli.main(["--url", url, "daemon", "stop"])
        out, err = capsys.readouterr()
        assert rc == 0, err
        assert f"stopped mcuscoped (pid {shim_pid})" in out
    finally:
        try:
            httpx.post(f"{url}/shutdown", timeout=2)
        except httpx.HTTPError:
            pass
