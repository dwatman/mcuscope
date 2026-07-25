"""Config write-back tests (SPEC 3.3.1).

Covers the tomlkit round-trip (comments and unknown keys survive), the REST
endpoints, the network write-protection rule, and restart_required reporting.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mcuscope.config import (
    Config,
    PortConfig,
    ServerConfig,
    StorageConfig,
    load_config,
    save_ports,
    save_server,
    save_storage,
)
from mcuscope.server import create_app

# -- write-back unit tests -------------------------------------------------------------


def test_save_creates_file_and_parents(tmp_path: Path) -> None:
    cfg = tmp_path / "deep" / "dir" / "config.toml"
    save_server(cfg, "0.0.0.0", 9000)
    loaded = load_config(cfg)
    assert loaded.server.host == "0.0.0.0"
    assert loaded.server.port == 9000


def test_save_preserves_comments_and_unknown_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "# hand-written header comment\n"
        "[server]\n"
        'host = "127.0.0.1"  # keep me local\n'
        "port = 8765\n"
        "\n"
        "[storage]\n"
        "retention_days = 3\n"
        "\n"
        "[custom]\n"
        'note = "user section"\n',
        encoding="utf-8",
    )
    save_server(cfg, "0.0.0.0", 8765)
    text = cfg.read_text(encoding="utf-8")
    assert "# hand-written header comment" in text
    assert "# keep me local" in text
    assert 'note = "user section"' in text
    assert 'host = "0.0.0.0"' in text
    # the untouched section is intact
    assert load_config(cfg).storage.retention_days == 3


def test_save_storage_and_ports_roundtrip(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    save_storage(cfg, "", 14)
    save_ports(
        cfg,
        [
            PortConfig(alias="board", device="/dev/ttyACM0", baud=921600, autoconnect=False),
            PortConfig(alias="sim", serial_number="ABC123", baud=115200),
        ],
    )
    loaded = load_config(cfg)
    assert loaded.storage.retention_days == 14
    assert [pc.alias for pc in loaded.ports] == ["board", "sim"]
    assert loaded.ports[0].device == "/dev/ttyACM0"
    assert loaded.ports[0].autoconnect is False
    assert loaded.ports[1].serial_number == "ABC123"
    assert loaded.ports[1].device is None


def test_save_empty_ports_removes_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    save_ports(cfg, [PortConfig(alias="board", device="/dev/ttyACM0")])
    save_ports(cfg, [])
    assert load_config(cfg).ports == []
    assert "[[ports]]" not in cfg.read_text(encoding="utf-8")


# -- endpoint tests ----------------------------------------------------------------------


def _mk_app(tmp_path: Path, token: str | None = None):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=8765, token=token),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    return create_app(config, config_path=tmp_path / "config.toml")


def test_get_config_defaults(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        body = c.get("/config").json()
        assert body["exists"] is False
        assert body["server"] == {"host": "127.0.0.1", "port": 8765}
        assert body["ports"] == []
        assert body["token_set"] is False
        assert "token" not in body.get("server", {})
        # saved db_path "" differs from the running tmp db_path, so a restart
        # would change where capture goes
        assert body["restart_required"] is True


def test_put_config_server_and_restart_flag(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        r = c.put("/config/server", json={"host": "0.0.0.0", "port": 9000})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restart_required": True}
        # matching the running values clears the flag
        r = c.put("/config/server", json={"host": "127.0.0.1", "port": 8765})
        assert r.json() == {"ok": True, "restart_required": False}
        assert load_config(tmp_path / "config.toml").server.port == 8765


def test_put_config_storage_applies_retention_live(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        r = c.put(
            "/config/storage",
            json={"db_path": str(tmp_path / "cap.db"), "retention_days": 2},
        )
        assert r.json() == {"ok": True, "restart_required": False}
        assert app.state.store._retention_days == 2
        # a different db_path needs a restart
        r = c.put("/config/storage", json={"db_path": "elsewhere.db", "retention_days": 2})
        assert r.json()["restart_required"] is True


def test_put_config_storage_size_cap(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        base = {"db_path": str(tmp_path / "cap.db"), "retention_days": 7}
        # A non-zero cap below the floor is refused, so a mistyped value cannot trim a
        # capture to nothing the moment it is saved.
        r = c.put("/config/storage", json={**base, "max_db_bytes": 5000})
        assert r.status_code == 400
        assert "max_db_bytes" in r.json()["error"]
        assert app.state.store._max_db_bytes == 0

        # A real cap applies live and round-trips through the saved file and /status.
        r = c.put("/config/storage", json={**base, "max_db_bytes": 64 << 20})
        assert r.json() == {"ok": True, "restart_required": False}
        assert app.state.store._max_db_bytes == 64 << 20
        assert c.get("/config").json()["storage"]["max_db_bytes"] == 64 << 20
        assert c.get("/status").json()["db_max_bytes"] == 64 << 20

        # 0 always means "no cap" and turns it back off.
        r = c.put("/config/storage", json={**base, "max_db_bytes": 0})
        assert r.json()["ok"] is True
        assert app.state.store._max_db_bytes == 0


def test_put_config_storage_auto_session_applies_live(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        base = {"db_path": str(tmp_path / "cap.db"), "retention_days": 7}
        assert c.get("/status").json()["session"]["auto"] is True

        # Turning it off leaves the open run to close normally: ending it early would
        # fragment the capture for no benefit.
        r = c.put("/config/storage", json={**base, "auto_session": False})
        assert r.json()["ok"] is True
        assert c.get("/config").json()["storage"]["auto_session"] is False

        # With it off, ending a named run does not hand back to an automatic one.
        c.post("/sessions", json={"name": "manual"})
        c.post("/sessions/stop")
        assert c.get("/status").json()["session"] is None

        # Turning it back on starts covering the capture immediately.
        r = c.put("/config/storage", json={**base, "auto_session": True})
        assert r.json()["ok"] is True
        assert c.get("/status").json()["session"]["auto"] is True


def test_put_config_ports_validation(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        ok = {"alias": "board", "device": "/dev/ttyACM0", "baud": 115200}
        r = c.put("/config/ports", json={"ports": [ok]})
        assert r.json() == {"ok": True, "restart_required": False}
        # duplicate alias
        r = c.put("/config/ports", json={"ports": [ok, ok]})
        assert r.status_code == 400
        assert "duplicate" in r.json()["error"]
        # neither device nor serial_number
        r = c.put("/config/ports", json={"ports": [{"alias": "x"}]})
        assert r.status_code == 400
        # dangerous device scheme is rejected like the runtime attach path
        bad = {"alias": "x", "device": "spy:///dev/ttyACM0"}
        r = c.put("/config/ports", json={"ports": [bad]})
        assert r.status_code == 400
        # bad alias grammar -> 422 from the model
        r = c.put("/config/ports", json={"ports": [{"alias": "", "device": "/dev/x"}]})
        assert r.status_code == 422
        # the earlier good save is still what is on disk
        assert [pc.alias for pc in load_config(tmp_path / "config.toml").ports] == ["board"]


def test_config_write_denied_from_network_without_token(tmp_path: Path) -> None:
    # TestClient's default client host is "testclient", i.e. non-loopback.
    app = _mk_app(tmp_path, token=None)
    with TestClient(app) as c:
        # reads are allowed (same as the rest of the API in tokenless mode)
        assert c.get("/config").status_code == 200
        for path, body in (
            ("/config/server", {"host": "127.0.0.1", "port": 8765}),
            ("/config/storage", {"db_path": "", "retention_days": 7}),
            ("/config/ports", {"ports": []}),
        ):
            r = c.put(path, json=body)
            assert r.status_code == 403, path
            assert "token" in r.json()["error"]
    assert not (tmp_path / "config.toml").exists()


def test_config_write_allowed_from_network_with_token(tmp_path: Path) -> None:
    app = _mk_app(tmp_path, token="sesame-open-123")
    headers = {"Authorization": "Bearer sesame-open-123"}
    with TestClient(app) as c:
        r = c.put(
            "/config/server", json={"host": "0.0.0.0", "port": 9000}, headers=headers
        )
        assert r.status_code == 200
        body = c.get("/config", headers=headers).json()
        assert body["token_set"] is True
        assert body["server"]["host"] == "0.0.0.0"


def test_put_config_rejects_non_table_section(tmp_path: Path) -> None:
    # A hand-edited `server = 3` must produce a clean 500 envelope, not a TypeError.
    (tmp_path / "config.toml").write_text("server = 3\n", encoding="utf-8")
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        r = c.put("/config/server", json={"host": "127.0.0.1", "port": 8765})
        assert r.status_code == 500
        assert "not a table" in r.json()["error"]
    # the broken file is left untouched
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "server = 3\n"


def test_get_config_reports_invalid_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not [ valid toml", encoding="utf-8")
    app = _mk_app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        r = c.get("/config")
        assert r.status_code == 500
        assert "invalid TOML" in r.json()["error"]
