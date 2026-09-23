"""`daemon start`'s stderr file on Windows is opened FILE_APPEND_DATA-only, so the spawned
daemon appends too. The real inheritance needs Windows; here kernel32 and msvcrt are fakes
and only the open the CLI asks for is checked."""

from __future__ import annotations

import ctypes
import os
import sys
import types
from ctypes import wintypes

import pytest

from mcuscope import cli, cli_daemonctl


@pytest.fixture
def fake_win(monkeypatch, tmp_path):
    calls: list[tuple] = []
    result = {"handle": 42}

    class CreateFileW:
        restype = argtypes = None

        def __call__(self, *args):
            calls.append(args)
            return result["handle"]

    class K32:
        def __init__(self) -> None:
            self.CreateFileW = CreateFileW()

    backing = tmp_path / "backing.err"
    msvcrt = types.SimpleNamespace(
        open_osfhandle=lambda handle, flags: os.open(backing, os.O_WRONLY | os.O_CREAT
                                                     | os.O_APPEND))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: K32(),
                        raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "access denied"),
                        raising=False)
    return calls, result


def test_the_windows_open_asks_for_append_data_only(fake_win, tmp_path) -> None:
    calls, _ = fake_win
    fh = cli_daemonctl._open_append(str(tmp_path / "d.err"))
    fh.close()
    (path, access, share, _sa, disposition, _attrs, _tmpl), = calls
    assert path == str(tmp_path / "d.err")
    # FILE_APPEND_DATA | FILE_READ_ATTRIBUTES (os.fstat) | SYNCHRONIZE, no FILE_WRITE_DATA
    assert access == 0x0004 | 0x0080 | 0x00100000
    assert disposition == 4                     # OPEN_ALWAYS: never truncates
    assert share & 0x2                          # a racing start can open it too


def test_a_failed_windows_open_falls_back_to_no_log(fake_win, monkeypatch, capsys,
                                                    tmp_path) -> None:
    _, result = fake_win
    result["handle"] = wintypes.HANDLE(-1).value
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_status_body", lambda s, timeout=2.0: None)
    monkeypatch.setattr(cli, "_status_or_refusal", lambda s, timeout=2.0: (None, None))
    spawned: list[object] = []

    class Proc:
        pid = 999996

        def poll(self):
            return 1

    def popen(args, **kw):
        spawned.append(kw["stderr"])
        return Proc()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    rc = cli.main(["--url", "http://127.0.0.1:1", "daemon", "start", "--timeout", "1"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "warning: cannot write the daemon log" in err and "access denied" in err
    assert spawned == [cli.subprocess.DEVNULL]
