"""Owner rulings 2026-09-15, config side: E-8 revision, A-5 warnings, a named config must exist.

SPEC 3.3 and 3.3.1. Every refusal is driven next to the success it guards.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcuscope import config as config_mod
from mcuscope import daemon as daemon_mod
from mcuscope import pidfile
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from tests.support import free_port

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


# -- A-5: warnings once, and on /status ---------------------------------------------------

TYPO = '[server]\nprot = 18605\n[storge]\ndb_path = "x"\n[storage]\nretention_days = 0\n'


def _unknown(records) -> list[str]:
    return [r.getMessage() for r in records if "unknown key" in r.getMessage()]


def test_warnings_are_logged_once_at_startup_and_served_on_status(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    cfg = tmp_path / "typo.toml"
    cfg.write_text(TYPO, encoding="utf-8", newline="\n")
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    seen: dict = {}

    def fake_serve(app, **kw):
        with _client(app) as c:
            seen["status"] = c.get("/status").json()["config_warnings"]
            for _ in range(2):
                assert c.get("/config").status_code == 200
            ports = {"ports": [{"alias": "b", "device": "/dev/ttyACM0"}]}
            assert c.put("/config/ports", json=ports).status_code == 200
            # The file changes after startup: /status keeps the config it started with.
            cfg.write_text(TYPO + "[updat]\n", encoding="utf-8", newline="\n")
            c.get("/config")
            seen["status_after"] = c.get("/status").json()["config_warnings"]

    monkeypatch.setattr(daemon_mod, "_serve", fake_serve)
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        assert daemon_mod.main(["-c", str(cfg), "--port", str(free_port())]) in (0, None)

    logged = _unknown(caplog.records)
    assert len(logged) == 2, logged   # prot and storge, once each, across 3 reads and a save
    assert any("'prot'" in m and "did you mean 'port'" in m for m in logged), logged
    # Not only unknown keys: every loader warning for that file.
    assert any("retention_days" in m for m in seen["status"]), seen
    assert sorted(seen["status"]) == sorted(
        r.getMessage() for r in caplog.records if r.name == "mcuscope.config"
    )
    assert seen["status_after"] == seen["status"]
    assert not any("updat" in m for m in seen["status_after"])


def test_status_config_warnings_empty_for_a_clean_config(tmp_path: Path) -> None:
    with _client(_app(tmp_path)) as c:
        assert c.get("/status").json()["config_warnings"] == []
    with _client(_app(tmp_path, config_warnings=["config: x"])) as c:
        assert c.get("/status").json()["config_warnings"] == ["config: x"]


def test_load_config_still_logs_without_a_sink(tmp_path: Path, caplog) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[server]\nprot = 1\n", encoding="utf-8", newline="\n")
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        config_mod.load_config(cfg)
    assert len(_unknown(caplog.records)) == 1
    caplog.clear()
    sink: list[str] = []
    with caplog.at_level(logging.WARNING, logger="mcuscope.config"):
        config_mod.load_config(cfg, warnings=sink)
        # The sink is per call: a later plain load logs again.
        config_mod.load_config(cfg)
    assert len(sink) == 1 and "prot" in sink[0]
    assert len(_unknown(caplog.records)) == 1


# -- a named config must exist -------------------------------------------------------------


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """Runs `daemon.main` without serving, reporting what each run reached.

    `served` and `claims` are cleared per run: a list that only ever grows makes the
    second run of a test a constant, which is what `bool(served)` used to be. The claim
    spy is the observation point for "refused before the pid claim": the record itself
    is released in main()'s finally, so the data dir is empty on every path.
    """
    monkeypatch.setattr("platformdirs.user_data_dir", lambda app: str(tmp_path / "data"))
    monkeypatch.setattr("platformdirs.user_config_dir", lambda app: str(tmp_path / "cfgdir"))
    monkeypatch.delenv("MCUSCOPED_CONFIG", raising=False)
    served: list = []
    claims: list[str | None] = []
    monkeypatch.setattr(daemon_mod, "_serve", lambda app, **kw: served.append(app))
    real_claim = pidfile.claim

    def spy_claim(host: str, port: int) -> str | None:
        path = real_claim(host, port)
        claims.append(path)
        return path

    monkeypatch.setattr(pidfile, "claim", spy_claim)

    def run(*argv: str) -> tuple[int | None, bool]:
        served.clear()
        claims.clear()
        return daemon_mod.main([*argv, "--port", str(free_port())]), bool(served)

    run.claims = claims
    return run


def test_a_relative_named_config_is_reported_absolute(tmp_path, run_main, monkeypatch) -> None:
    """`mcu daemon restart` from another directory checks the file this daemon runs on."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "rel.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    given: list = []
    real_create_app = daemon_mod.create_app

    def spy_create_app(*args, **kwargs):
        given.append(kwargs.get("config_path"))   # what /status reports (server.py)
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(daemon_mod, "create_app", spy_create_app)
    assert run_main("-c", "etc/rel.toml") == (0, True)
    assert given == [str(tmp_path / "etc" / "rel.toml")], given


def test_a_named_config_that_does_not_exist_is_refused(tmp_path, run_main, capsys) -> None:
    missing = tmp_path / "typo.toml"
    assert run_main("-c", str(missing)) == (1, False)
    cap = capsys.readouterr()
    assert cap.err == f"mcuscoped: no such config file: {missing}\n"
    assert cap.out == "", "refused before the files notice"
    assert not missing.exists(), "the refusal must not create the file"
    assert run_main.claims == [], "refused before the pid claim"


def test_a_start_that_gets_past_the_config_does_claim(tmp_path, run_main) -> None:
    """Positive control for the claim spy above: the served path reaches the claim."""
    present = tmp_path / "present.toml"
    present.write_bytes(b"")
    assert run_main("-c", str(present)) == (0, True)
    assert len(run_main.claims) == 1 and run_main.claims[0], run_main.claims
    assert run_main.claims[0].endswith(".pid")
    # Claimed, then released by main()'s finally: the data dir cannot tell the two paths
    # apart, which is why the refusal above is asserted on the spy and not on a glob.
    assert not list((tmp_path / "data").glob("*.pid"))


def test_the_env_variable_names_a_config_too(tmp_path, run_main, monkeypatch, capsys) -> None:
    missing = tmp_path / "env-typo.toml"
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(missing))
    assert run_main() == (1, False)
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {missing}\n"


def test_the_flag_wins_over_the_variable_both_ways(tmp_path, run_main, monkeypatch, capsys):
    present = tmp_path / "present.toml"
    present.write_bytes(b"")
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(missing))
    assert run_main("-c", str(present)) == (0, True)
    capsys.readouterr()
    monkeypatch.setenv("MCUSCOPED_CONFIG", str(present))
    assert run_main("-c", str(missing)) == (1, False)  # the flag refuses; nothing served
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {missing}\n"


def test_a_missing_default_config_still_means_defaults(tmp_path, run_main, capsys) -> None:
    default = tmp_path / "cfgdir" / "config.toml"
    assert run_main() == (0, True)
    assert f"config: {default} not found, using defaults\n" in capsys.readouterr().out
    assert not default.exists()


def test_an_empty_variable_means_the_default(tmp_path, run_main, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MCUSCOPED_CONFIG", "")
    assert run_main() == (0, True)


def test_a_named_directory_is_unreadable_not_missing(tmp_path, run_main, capsys) -> None:
    d = tmp_path / "adir.toml"
    d.mkdir()
    assert run_main("-c", str(d)) == (1, False)
    err = capsys.readouterr().err
    assert "cannot read" in err and "no such config file" not in err, err


def test_a_dangling_symlink_is_missing(tmp_path, run_main, capsys) -> None:
    link = tmp_path / "link.toml"
    try:
        os.symlink(tmp_path / "gone.toml", link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink support")
    assert run_main("-c", str(link)) == (1, False)
    assert capsys.readouterr().err == f"mcuscoped: no such config file: {link}\n"
