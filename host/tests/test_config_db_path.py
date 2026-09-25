"""A relative `storage.db_path` resolves against the config file's directory and is reported
absolute (SPEC 3.3), so a daemon restarted from another directory opens the same capture."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from mcuscope.config import Config, StorageConfig, load_config, resolve_db_path
from mcuscope.server import create_app


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def test_a_relative_db_path_is_taken_from_the_config_files_directory(
    tmp_path, monkeypatch
) -> None:
    home, elsewhere = tmp_path / "cfg", tmp_path / "cwd"
    home.mkdir()
    elsewhere.mkdir()
    _write(home / "config.toml", '[storage]\ndb_path = "rel.db"\n')
    monkeypatch.chdir(elsewhere)
    cfg = load_config(home / "config.toml")
    assert resolve_db_path(cfg) == str(home / "rel.db")
    # Positive control: the same text in a Config read from no file follows the CWD.
    assert resolve_db_path(Config(storage=StorageConfig(db_path="rel.db"))) == str(
        elsewhere / "rel.db"
    )


def test_a_relative_config_path_resolves_once_against_the_cwd_it_was_read_from(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "a").mkdir()
    _write(tmp_path / "a" / "config.toml", '[storage]\ndb_path = "sub/cap.db"\n')
    monkeypatch.chdir(tmp_path)
    cfg = load_config("a/config.toml")
    monkeypatch.chdir(tmp_path / "a")
    assert resolve_db_path(cfg) == str(tmp_path / "a" / "sub" / "cap.db")


def test_status_reports_the_absolute_path_and_an_unchanged_save_needs_no_restart(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "cfg"
    home.mkdir()
    cfg_file = home / "config.toml"
    _write(cfg_file, '[storage]\ndb_path = "rel.db"\n')
    monkeypatch.chdir(tmp_path)
    app = create_app(load_config(cfg_file), config_path=cfg_file)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as c:
        status = c.get("/status").json()
        assert status["db_path"] == str(home / "rel.db") and os.path.isabs(status["db_path"])
        assert os.path.exists(home / "rel.db") and not os.path.exists(tmp_path / "rel.db")
        assert c.get("/config").json()["restart_required"] is False
        storage = c.get("/config").json()["storage"]
        assert storage["db_path"] == "rel.db"   # the file keeps what was typed
        r = c.put("/config/storage", json=storage)
        assert r.status_code == 200 and r.json()["restart_required"] is False
        # Positive control: a different relative path does need one.
        r = c.put("/config/storage", json={**storage, "db_path": "other.db"})
        assert r.status_code == 200 and r.json()["restart_required"] is True


def test_an_in_memory_db_path_is_not_taken_as_a_file_name(tmp_path, monkeypatch) -> None:
    home = tmp_path / "cfg"
    home.mkdir()
    _write(home / "config.toml", '[storage]\ndb_path = ":memory:"\n')
    monkeypatch.chdir(tmp_path)
    assert resolve_db_path(load_config(home / "config.toml")) == ":memory:"
    # Positive control: a name that only resembles it is still a file beside the config.
    _write(home / "config.toml", '[storage]\ndb_path = "memory:"\n')
    assert resolve_db_path(load_config(home / "config.toml")) == str(home / "memory:")
