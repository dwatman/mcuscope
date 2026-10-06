"""config.toml and the update-check cache are created owner-only on POSIX (SPEC 3.2), under
a permissive umask, and a replace keeps the mode of the file it replaces."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from mcuscope.config import save_server
from mcuscope.update_check import UpdateChecker

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def umask022():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_a_new_config_and_its_new_directory_are_owner_only(tmp_path, umask022) -> None:
    tmp_path.chmod(0o755)
    cfg = tmp_path / "a" / "b" / "config.toml"
    save_server(cfg, "127.0.0.1", 8558)
    assert _mode(cfg) == 0o600
    assert _mode(cfg.parent) == 0o700 and _mode(cfg.parent.parent) == 0o700
    assert _mode(tmp_path) == 0o755   # an existing directory keeps its mode


def test_a_rewritten_config_keeps_the_mode_the_user_gave_it(tmp_path, umask022) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[server]\nport = 1\n")
    cfg.chmod(0o640)
    save_server(cfg, "127.0.0.1", 8558)
    assert _mode(cfg) == 0o640


def test_a_new_update_cache_is_owner_only(tmp_path, umask022) -> None:
    path = tmp_path / "x" / "update.json"
    UpdateChecker(path=path)._save_cache()
    assert path.exists(), "the cache write failed"
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700


def test_a_replaced_update_cache_keeps_its_mode(tmp_path, umask022) -> None:
    path = tmp_path / "update.json"
    path.write_text("{}")
    path.chmod(0o644)
    UpdateChecker(path=path)._save_cache()
    assert _mode(path) == 0o644


@pytest.mark.parametrize(("umask", "existing"), [(0o022, 0o664), (0o077, 0o644), (0o022, 0o666)])
def test_a_replace_keeps_the_exact_mode_whatever_the_umask(tmp_path, umask, existing) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[server]\nport = 1\n")
    cfg.chmod(existing)
    old = os.umask(umask)
    try:
        save_server(cfg, "127.0.0.1", 8558)
    finally:
        os.umask(old)
    assert _mode(cfg) == existing


def test_a_new_file_is_still_owner_only_under_a_zero_umask(tmp_path) -> None:
    old = os.umask(0)
    try:
        save_server(tmp_path / "config.toml", "127.0.0.1", 8558)
    finally:
        os.umask(old)
    assert _mode(tmp_path / "config.toml") == 0o600
