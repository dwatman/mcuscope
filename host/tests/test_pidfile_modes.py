"""Every pid record is created owner-only on POSIX, whichever path wrote it (SPEC 3.1)."""

from __future__ import annotations

import errno
import os
import stat

import pytest

from mcuscope import pidfile

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@pytest.fixture(autouse=True)
def open_umask(tmp_path, monkeypatch):
    # 022, so a default-mode create would read 0644 rather than pass by luck.
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(tmp_path / "data"))
    old = os.umask(0o022)
    yield
    os.umask(old)


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_claimed_record_is_0600() -> None:
    path = pidfile.claim("127.0.0.1", 19041)
    try:
        assert path is not None and _mode(path) == 0o600
    finally:
        pidfile.release(path)


def test_a_created_record_is_0600() -> None:
    path = pidfile.pid_file_path("127.0.0.1", 19042)
    assert pidfile.create_record(path, os.getpid())
    assert _mode(path) == 0o600


def test_a_record_written_without_hard_links_is_0600(monkeypatch) -> None:
    def no_links(src, dst):
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr(os, "link", no_links)
    path = pidfile.pid_file_path("127.0.0.1", 19043)
    assert pidfile.create_record(path, os.getpid())
    assert _mode(path) == 0o600 and pidfile.read_pid_record(path) == os.getpid()
