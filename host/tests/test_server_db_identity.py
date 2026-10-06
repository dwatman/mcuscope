"""The capture is the file a db path names, not its spelling (SPEC 3.4): export temp copies
are keyed and placed by `realpath`, as the capture lock is, and `restart_required` treats a
symlink or another spelling of the running capture as the same file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcuscope import server
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege on this OS")


def _client(db_path: Path, cfg_dir: Path) -> TestClient:
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=8558),
        storage=StorageConfig(db_path=str(db_path)),
    )
    app = create_app(config, config_path=cfg_dir / "config.toml")
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def _copies(*dirs: Path) -> list[Path]:
    return sorted(p for d in dirs for p in d.iterdir() if p.name.startswith("mcuscope-"))


def _leave_a_copy(c: TestClient, monkeypatch) -> None:
    """Export a session with removal disabled, standing in for a build in flight."""
    with monkeypatch.context() as m:
        m.setattr(server, "_remove_export_file", lambda path, live: live.discard(path))
        sid = c.get("/status").json()["session"]["id"]
        assert c.get(f"/sessions/{sid}/export").status_code == 200


@pytest.fixture
def tree(tmp_path):
    a, b, links = tmp_path / "a", tmp_path / "b", tmp_path / "links"
    for d in (a, b, links):
        d.mkdir()
    return a, b, links


def test_a_retargeted_symlink_does_not_sweep_the_other_captures_copies(
    tree, monkeypatch
) -> None:
    a, b, links = tree
    link = links / "cap.db"
    _symlink(link, a / "cap.db")
    with _client(link, links) as c:
        _leave_a_copy(c, monkeypatch)
    first = _copies(a, b, links)
    assert len(first) == 1 and first[0].parent == a, "the copy is not beside the real file"

    link.unlink()
    _symlink(link, b / "cap.db")
    with _client(link, links):
        pass
    assert _copies(a, b, links) == first, "a capture on another file swept this one's copy"


def test_a_respelled_capture_sweeps_its_own_leftovers(tree, monkeypatch) -> None:
    a, _b, links = tree
    with _client(a / "cap.db", links) as c:
        _leave_a_copy(c, monkeypatch)
    assert len(_copies(a, links)) == 1
    link = links / "cap.db"   # a spelling whose own directory is not the capture's
    _symlink(link, a / "cap.db")
    with _client(link, links):
        pass
    assert _copies(a, links) == [], "the same capture by another spelling kept its orphan"


def test_restart_required_compares_files_not_spellings(tree) -> None:
    a, b, links = tree
    real = a / "cap.db"
    alias, other = links / "alias.db", links / "other.db"
    _symlink(alias, real)
    _symlink(other, b / "cap.db")
    with _client(real, links) as c:
        for spelling, restart in ((alias, False), (other, True), (real, False)):
            r = c.put("/config/storage", json={"db_path": str(spelling), "retention_days": 7})
            assert r.json()["restart_required"] is restart, spelling
            assert c.get("/config").json()["restart_required"] is restart, spelling
    # The running side is the file opened at start: a link retargeted since names another.
    with _client(alias, links) as c:
        alias.unlink()
        _symlink(alias, b / "cap.db")
        r = c.put("/config/storage", json={"db_path": str(alias), "retention_days": 7})
        assert r.json()["restart_required"] is True
        assert c.get("/config").json()["restart_required"] is True
        alias.unlink()
        _symlink(alias, real)
    # The running capture named through the link: the real spelling saved is no restart.
    with _client(alias, links) as c:
        r = c.put("/config/storage", json={"db_path": str(real), "retention_days": 7})
        assert r.json()["restart_required"] is False
        assert c.get("/config").json()["restart_required"] is False


def test_an_in_memory_capture_keeps_its_export_copies_out_of_the_working_dir(
    tmp_path, monkeypatch
) -> None:
    """`:memory:` is not a path: resolved like one it would name `<cwd>/:memory:`, and
    export temp copies would land in whatever directory the daemon was started from."""
    monkeypatch.chdir(tmp_path)
    with _client(Path(":memory:"), tmp_path) as c:
        assert c.app.state.db_realpath == ":memory:"
        assert server._export_dir(c.app.state.db_realpath) is None
    # Positive control: a relative file path is resolved against the working directory.
    assert server._db_realpath("cap.db") == os.path.realpath(tmp_path / "cap.db")
