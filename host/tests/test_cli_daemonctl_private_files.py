"""Files `mcu daemon start` creates are owner-only on POSIX at creation (0600); an existing
file keeps its mode."""

from __future__ import annotations

import os
import stat

import pytest

from mcuscope import cli_daemonctl

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@pytest.fixture
def loose_umask():
    old = os.umask(0o022)   # what would leave a default-mode file world-readable
    yield
    os.umask(old)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_the_daemon_stderr_log_is_created_owner_only(tmp_path, loose_umask) -> None:
    path = tmp_path / "mcuscoped-127.0.0.1-8558.err"
    with cli_daemonctl._open_append(str(path)) as fh:
        fh.write(b"x")
    assert _mode(path) == 0o600


def test_an_existing_stderr_log_keeps_its_mode(tmp_path, loose_umask) -> None:
    path = tmp_path / "mcuscoped-127.0.0.1-8558.err"
    path.write_bytes(b"old\n")
    os.chmod(path, 0o640)
    with cli_daemonctl._open_append(str(path)) as fh:
        fh.write(b"new\n")
    assert _mode(path) == 0o640
    assert path.read_bytes() == b"old\nnew\n"   # appended, not truncated


def test_a_replaced_pid_record_is_owner_only(tmp_path, loose_umask) -> None:
    path = tmp_path / "mcuscoped-127.0.0.1-8558.pid"
    path.write_text("1")
    os.chmod(path, 0o644)   # a record from before records were private
    cli_daemonctl._replace_pid_record(str(path), 4242)
    assert path.read_text() == "4242"
    assert _mode(path) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]   # no .tmp left
