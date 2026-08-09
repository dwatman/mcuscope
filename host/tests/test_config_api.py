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
        encoding="utf-8", newline="\n",
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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        r = c.put("/config/server", json={"host": "0.0.0.0", "port": 9000})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restart_required": True}
        # matching the running values clears the flag
        r = c.put("/config/server", json={"host": "127.0.0.1", "port": 8765})
        assert r.json() == {"ok": True, "restart_required": False}
        assert load_config(tmp_path / "config.toml").server.port == 8765


def test_put_config_storage_applies_retention_live(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
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
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
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
    with TestClient(app, base_url="http://127.0.0.1") as c:
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
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.put(
            "/config/server", json={"host": "0.0.0.0", "port": 9000}, headers=headers
        )
        assert r.status_code == 200
        body = c.get("/config", headers=headers).json()
        assert body["token_set"] is True
        assert body["server"]["host"] == "0.0.0.0"


def test_put_config_rejects_non_table_section(tmp_path: Path) -> None:
    # A hand-edited `server = 3` must produce a clean 500 envelope, not a TypeError.
    (tmp_path / "config.toml").write_text("server = 3\n", encoding="utf-8", newline="\n")
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        r = c.put("/config/server", json={"host": "127.0.0.1", "port": 8765})
        assert r.status_code == 500
        assert "not a table" in r.json()["error"]
    # the broken file is left untouched
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "server = 3\n"


def test_get_config_reports_invalid_file(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not [ valid toml", encoding="utf-8", newline="\n")
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        r = c.get("/config")
        assert r.status_code == 500
        assert "invalid TOML" in r.json()["error"]


# -- update check (SPEC 3.6) -------------------------------------------------------------


def test_config_update_defaults_on_and_saves_off(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        assert c.get("/config").json()["update"] == {"check": True}

        r = c.put("/config/update", json={"check": False})
        assert r.json() == {"ok": True, "restart_required": False}
        # Written to the file, applied to the running config, and applied to the checker
        # itself: an opt-out that only takes effect on restart is not an opt-out.
        assert load_config(tmp_path / "config.toml").update.check is False
        assert c.get("/config").json()["update"] == {"check": False}
        assert app.state.config.update.check is False
        assert app.state.update_checker.enabled is False

        c.put("/config/update", json={"check": True})
        assert load_config(tmp_path / "config.toml").update.check is True


def test_config_update_is_write_protected_like_the_rest(tmp_path: Path) -> None:
    # A remote client with no token set may not touch the config file (SPEC 3.3.1).
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://192.168.1.10:8765", client=("192.168.1.10", 1)) as c:
        r = c.put("/config/update", json={"check": False},
                  headers={"host": "192.168.1.10:8765"})
    assert r.status_code == 403
    assert not (tmp_path / "config.toml").exists()


def test_status_reports_no_update_result_before_a_check(tmp_path: Path) -> None:
    # The suite runs with the environment veto set, so the checker is disabled and reports
    # nothing - not even a cached result from a real run on this machine. The field is
    # present and null rather than missing, which is what the UI keys off.
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        body = c.get("/status").json()
    assert "update" in body
    assert body["update"] is None


def test_status_reports_an_available_update(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        checker = app.state.update_checker
        checker.enabled = True     # the suite's environment veto would report nothing
        checker.latest = "99.0.0"
        checker.checked_at = 1_700_000_000.0
        body = c.get("/status").json()
    assert body["update"]["latest"] == "99.0.0"
    assert body["update"]["available"] is True
    assert body["update"]["url"].startswith("https://")


def test_put_config_storage_applies_min_sessions_live(tmp_path: Path) -> None:
    """The one storage setting whose live apply was never driven through the endpoint.

    retention_days, max_db_bytes and auto_session each have a test above; this one was saved
    to the file and never handed to the running store, so the retention floor kept whatever
    it started with.
    """
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        base = {"db_path": str(tmp_path / "cap.db"), "retention_days": 7}
        r = c.put("/config/storage", json={**base, "min_sessions": 9})
        assert r.json() == {"ok": True, "restart_required": False}
        assert app.state.store._min_sessions == 9
        assert c.get("/config").json()["storage"]["min_sessions"] == 9
        assert load_config(tmp_path / "config.toml").storage.min_sessions == 9


def test_put_config_server_refuses_a_malformed_host(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        r = c.put("/config/server", json={"host": "0.0.0.0 evil", "port": 8765})
        assert r.status_code == 400
        assert "host" in r.json()["error"]
        # Nothing was written: a refused save must not half-apply.
        assert load_config(tmp_path / "config.toml").server.host != "0.0.0.0 evil"


def test_put_config_storage_refuses_a_malformed_db_path(tmp_path: Path) -> None:
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        r = c.put("/config/storage", json={"db_path": "cap\ndb", "retention_days": 7})
        assert r.status_code == 400
        assert "db_path" in r.json()["error"]


def test_put_config_ports_carries_every_field_of_the_body(tmp_path: Path) -> None:
    """autoconnect and serial_number reached the file only when save_ports was called direct.

    Driving the endpoint is the point: the passthrough is where a field gets dropped, and
    hard-coding `autoconnect=True` there was invisible to the whole suite.
    """
    app = _mk_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        entry = {
            "alias": "rig", "device": "/dev/ttyACM0", "baud": 57600,
            "autoconnect": False, "serial_number": "SN-12345",
        }
        assert c.put("/config/ports", json={"ports": [entry]}).json()["ok"] is True
        saved = load_config(tmp_path / "config.toml").ports
        assert len(saved) == 1
        assert saved[0].autoconnect is False, "autoconnect did not survive the endpoint"
        assert saved[0].serial_number == "SN-12345", "serial_number did not survive"
        assert saved[0].baud == 57600
        echoed = c.get("/config").json()["ports"][0]
        assert echoed["autoconnect"] is False and echoed["serial_number"] == "SN-12345"


# -- class 22: a hand-edited file is the path that never sees the model validation ----------


def test_a_string_key_of_the_wrong_type_fails_the_load_and_names_the_key(tmp_path: Path) -> None:
    """_as_int and _as_bool guarded every number and flag and left every string bare.

    `db_path = 5` loaded fine and then died inside resolve_db_path with an AttributeError
    naming neither the file nor the key, which is the exact failure _as_int exists to prevent.
    """
    import pytest

    from mcuscope.config import ConfigError

    cfg = tmp_path / "config.toml"
    cfg.write_text('[storage]\ndb_path = 5\n', encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg)
    assert "db_path" in str(excinfo.value)
    assert str(cfg) in str(excinfo.value)

    cfg.write_text('[server]\nhost = 3\n', encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError, match="host"):
        load_config(cfg)


def test_a_port_entry_with_a_non_string_alias_is_skipped_not_stored(tmp_path: Path) -> None:
    """The grammar check ran on str(alias) while the raw value was stored.

    `alias = 123` therefore attached a port under an integer key that no string lookup -
    /ports/123, ?port=123, PortManager.resolve - can ever match.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = 123\ndevice = "/dev/ttyACM0"\n\n'
        '[[ports]]\nalias = "good"\ndevice = "/dev/ttyACM1"\n',
        encoding="utf-8", newline="\n",
    )
    ports = load_config(cfg).ports
    assert [p.alias for p in ports] == ["good"], "a non-string alias was stored as one"
    assert all(isinstance(p.alias, str) for p in ports)


def test_a_bad_per_port_string_falls_back_without_losing_the_other_ports(tmp_path: Path) -> None:
    # class 16: one bad entry is charged to that entry, never to the whole file.
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "a"\ndevice = 7\nserial_number = "SN1"\n\n'
        '[[ports]]\nalias = "b"\ndevice = "/dev/ttyACM1"\n',
        encoding="utf-8", newline="\n",
    )
    ports = load_config(cfg).ports
    assert [p.alias for p in ports] == ["a", "b"]
    assert ports[0].device is None, "an integer device was kept as one"
    assert ports[0].serial_number == "SN1", "the rest of the entry was discarded too"


def test_a_hand_edited_size_cap_below_the_floor_falls_back(tmp_path: Path) -> None:
    """The 1 MiB floor is a property of the setting, not of the endpoint that also checks it.

    The trim targets 90% of the cap, so `max_db_bytes = 1000` empties the capture on the
    first sweep - and a hand-edited file is precisely the path that never sees the API's
    validation. Falling back beats clamping wherever the value governs deletion.
    """
    from mcuscope.config import MIN_DB_CAP_BYTES, StorageConfig

    cfg = tmp_path / "config.toml"
    cfg.write_text("[storage]\nmax_db_bytes = 1000\n", encoding="utf-8", newline="\n")
    assert load_config(cfg).storage.max_db_bytes == StorageConfig.max_db_bytes

    # 0 still means "no cap", and a real cap is kept as written.
    cfg.write_text("[storage]\nmax_db_bytes = 0\n", encoding="utf-8", newline="\n")
    assert load_config(cfg).storage.max_db_bytes == 0
    cfg.write_text(f"[storage]\nmax_db_bytes = {MIN_DB_CAP_BYTES}\n",
                   encoding="utf-8", newline="\n")
    assert load_config(cfg).storage.max_db_bytes == MIN_DB_CAP_BYTES


def test_a_port_whose_only_selector_is_the_wrong_type_is_skipped(tmp_path: Path) -> None:
    """`device = 5` is not a device, so the entry is unusable and must be dropped.

    The type coercion has to run before the "device or serial_number required" guard: a
    non-string is truthy, so it satisfied the guard and was then nulled, leaving exactly the
    port that guard exists to reject - one the reader retries on nothing forever.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[[ports]]\nalias = "bad"\ndevice = 5\n\n'
        '[[ports]]\nalias = "good"\ndevice = "/dev/ttyACM0"\n',
        encoding="utf-8", newline="\n",
    )
    ports = load_config(cfg).ports
    assert [p.alias for p in ports] == ["good"]
    # A wrong-typed device alongside a usable serial_number keeps the port, on the serial.
    cfg.write_text(
        '[[ports]]\nalias = "bysn"\ndevice = 5\nserial_number = "SN9"\n',
        encoding="utf-8", newline="\n",
    )
    ports = load_config(cfg).ports
    assert len(ports) == 1 and ports[0].device is None and ports[0].serial_number == "SN9"
