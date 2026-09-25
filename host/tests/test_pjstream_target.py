"""PlotJuggler surfaces report where datagrams go (`target`), beside the dest asked for
(SPEC 3.7, REVIEW class 17): `localhost` may resolve to `::1`, which the dest alone hides."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcuscope import pjstream
from mcuscope.config import Config, StorageConfig
from mcuscope.server import create_app


def _v6_localhost(host, port, *args, **kw):
    assert host == "localhost"
    return [(socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("::1", port, 0, 0))]


def test_every_surface_reports_the_resolved_target(tmp_path: Path, monkeypatch) -> None:
    if not socket.has_ipv6:
        pytest.skip("no IPv6 socket support")
    config = Config(storage=StorageConfig(db_path=str(tmp_path / "cap.db")))
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        off = c.get("/plotjuggler").json()
        assert off == {"enabled": False, "dest": pjstream.DEFAULT_DEST, "target": None}
        monkeypatch.setattr(pjstream.socket, "getaddrinfo", _v6_localhost)
        r = c.put("/plotjuggler", json={"enabled": True, "dest": "localhost:9870"})
        want = {"enabled": True, "dest": "localhost:9870", "target": "[::1]:9870"}
        assert r.status_code == 200 and r.json() == want
        assert c.get("/plotjuggler").json() == want
        assert c.get("/status").json()["plotjuggler"] == want
        # Disabled again: the target goes, the dest stays.
        r = c.put("/plotjuggler", json={"enabled": False})
        assert r.json() == {"enabled": False, "dest": "localhost:9870", "target": None}


def test_an_ipv4_target_is_not_bracketed() -> None:
    pj = pjstream.PlotJugglerStreamer("127.0.0.1:9870")
    try:
        pj.configure(True)
        assert pj.target == "127.0.0.1:9870"
    finally:
        pj.close()
