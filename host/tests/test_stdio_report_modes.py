"""A startup or crash report is written owner-only on POSIX, into a data dir created 0700
when missing (SPEC 3.1)."""

from __future__ import annotations

import os
import stat

import pytest

from mcuscope import _stdio

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


def test_a_startup_log_into_a_missing_data_dir_creates_it_0700_and_the_log_0600(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "a" / "data"
    monkeypatch.setenv("MCUSCOPE_DATA_DIR", str(data))
    monkeypatch.setattr(_stdio, "_report_key", "")
    old = os.umask(0o022)   # a default-mode create would read 0755 / 0644
    try:
        path = _stdio.write_startup_log("mcuscoped", "started\n")
    finally:
        os.umask(old)
    assert path is not None and os.path.dirname(path) == str(data)
    for d in (tmp_path / "a", data):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
