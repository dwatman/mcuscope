"""`PUT /config/*` revisions (SPEC 3.3.1, owner ruling E-8): every save carries the revision
it read, and a file changed since then is refused.

Every refusal is driven next to the success it guards."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcuscope import config as config_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app

CONFLICT = "config file changed since it was read; reload it and try again"

# One valid body per PUT /config/* endpoint.
BODIES = {
    "/config/server": {"host": "127.0.0.1", "port": 8558},
    "/config/storage": {"db_path": "", "retention_days": 5},
    "/config/update": {"check": False},
    "/config/plotjuggler": {"enabled": False, "dest": "127.0.0.1:9870"},
    "/config/ports": {"ports": [{"alias": "board", "device": "/dev/ttyACM0"}]},
}


def _app(tmp_path: Path, **kw):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=8558),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
    )
    return create_app(config, config_path=tmp_path / "config.toml", **kw)


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1))


def test_bodies_names_every_put_config_route(tmp_path: Path) -> None:
    derived = {r.path for r in _app(tmp_path).routes
               if "PUT" in getattr(r, "methods", ()) and r.path.startswith("/config/")}
    assert len(derived) >= 5, derived
    assert set(BODIES) == derived


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- E-8: revision ------------------------------------------------------------------------


def test_get_config_revision_is_the_file_hash_or_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    with _client(_app(tmp_path)) as c:
        body = c.get("/config").json()
        assert body["revision"] == "" and body["exists"] is False
        # An empty file exists: its revision is the hash of no bytes, not "".
        cfg.write_bytes(b"")
        body = c.get("/config").json()
        assert body["exists"] is True
        assert body["revision"] == hashlib.sha256(b"").hexdigest() != ""
        # Hashed on the bytes, BOM included, not on the decoded text.
        cfg.write_bytes(b"\xef\xbb\xbf[server]\nport = 9000\n")
        body = c.get("/config").json()
        assert body["server"]["port"] == 9000
        assert body["revision"] == _sha(cfg)


@pytest.mark.parametrize("route", sorted(BODIES))
def test_every_put_returns_the_new_revision_and_it_matches_get(tmp_path: Path, route) -> None:
    with _client(_app(tmp_path)) as c:
        rev = c.get("/config").json()["revision"]
        r = c.put(route, json={**BODIES[route], "revision": rev})
        assert r.status_code == 200, r.text
        new = r.json()["revision"]
        assert new == _sha(tmp_path / "config.toml") != rev
        assert c.get("/config").json()["revision"] == new
        # The returned revision is usable for the next save straight away.
        assert c.put(route, json={**BODIES[route], "revision": new}).status_code == 200


@pytest.mark.parametrize("route", sorted(BODIES))
def test_a_write_between_get_and_put_is_refused_and_writes_nothing(
    tmp_path: Path, route
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[server]\nport = 8558\n", encoding="utf-8", newline="\n")
    app = _app(tmp_path)
    with _client(app) as c:
        rev = c.get("/config").json()["revision"]
        # A hand edit lands while the dialog is open.
        edited = b'# hand edit\n[server]\nport = 8558\n[[ports]]\nalias = "tabB"\ndevice = "x"\n'
        cfg.write_bytes(edited)
        retention = app.state.config.storage.retention_days
        r = c.put(route, json={**BODIES[route], "revision": rev})
        assert r.status_code == 409, r.text
        assert r.json() == {"error": CONFLICT}
        assert cfg.read_bytes() == edited, "a refused save wrote the file"
        # Nothing of the refused body applied live either.
        assert app.state.config.storage.retention_days == retention
        assert app.state.config.update.check is True


def test_a_revision_for_an_absent_file_is_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    with _client(_app(tmp_path)) as c:
        r = c.put("/config/update", json={"check": False, "revision": ""})
        assert r.status_code == 200 and cfg.exists()
        # "" now names a file that is gone: refused, file kept.
        before = cfg.read_bytes()
        r = c.put("/config/update", json={"check": True, "revision": ""})
        assert (r.status_code, r.json()) == (409, {"error": CONFLICT})
        assert cfg.read_bytes() == before
        # And a real revision against a file deleted since: refused, nothing created.
        rev = c.get("/config").json()["revision"]
        cfg.unlink()
        r = c.put("/config/update", json={"check": True, "revision": rev})
        assert r.status_code == 409 and not cfg.exists()


def test_an_absent_revision_skips_the_check(tmp_path: Path) -> None:
    """Older clients and `mcu plotjuggler --save` send none: last writer wins, as before."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[update]\ncheck = true\n", encoding="utf-8", newline="\n")
    with _client(_app(tmp_path)) as c:
        c.get("/config")
        cfg.write_text("# edited\n[update]\ncheck = true\n", encoding="utf-8", newline="\n")
        r = c.put("/config/update", json={"check": False})
        assert r.status_code == 200 and r.json()["revision"] == _sha(cfg)
        r = c.put("/config/update", json={"check": True, "revision": None})
        assert r.status_code == 200


def test_a_garbage_revision_is_a_conflict_not_a_validation_error(tmp_path: Path) -> None:
    with _client(_app(tmp_path)) as c:
        for bad in ("nope", "0" * 64, " "):
            r = c.put("/config/update", json={"check": False, "revision": bad})
            assert (r.status_code, r.json()) == (409, {"error": CONFLICT}), bad
        assert not (tmp_path / "config.toml").exists()


def test_two_writers_holding_one_revision_one_wins(tmp_path: Path, monkeypatch) -> None:
    """The compare and the write are one step against other writers. The write is slowed
    so that, without the shared lock, both saves read the old file before either lands."""
    real_write = config_mod._write_doc

    def slow_write(path, doc):
        time.sleep(0.3)
        return real_write(path, doc)

    monkeypatch.setattr(config_mod, "_write_doc", slow_write)
    cfg = tmp_path / "config.toml"
    with _client(_app(tmp_path)) as c:
        rev = c.get("/config").json()["revision"]
        start = threading.Barrier(2)

        def put(route: str):
            start.wait()
            return c.put(route, json={**BODIES[route], "revision": rev})

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(put, ["/config/update", "/config/ports"]))
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409], [r.text for r in results]
        winner = next(r for r in results if r.status_code == 200)
        assert winner.json()["revision"] == _sha(cfg)
        text = cfg.read_text(encoding="utf-8")
        # Only the winner's section is in the file.
        assert ("[update]" in text) != ("[[ports]]" in text), text
