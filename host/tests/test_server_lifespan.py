"""The app's lifespan and config routes (SPEC 3.3): a raising shutdown step still stops the
store, a startup that cannot apply a setting says so in `config_warnings`, a saved setting
the runtime would refuse is refused, and a reconnect never undoes a detach."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from mcuscope import pjstream
from mcuscope.config import Config, PlotJugglerConfig, ServerConfig, StorageConfig
from mcuscope.serial_link import PortManager, SerialPort
from mcuscope.server import create_app
from mcuscope.update_check import UpdateChecker
from tests.support import UNOPENABLE

MULTICAST = "239.1.2.3:9870"


def _app(tmp_path, *, pj: PlotJugglerConfig | None = None, warnings: list[str] | None = None):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
        plotjuggler=pj or PlotJugglerConfig(),
    )
    return create_app(config, config_path=tmp_path / "config.toml", config_warnings=warnings)


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def _sys_rows(tmp_path) -> list[str]:
    with sqlite3.connect(tmp_path / "cap.db") as db:
        return [r[0] for r in db.execute("SELECT raw FROM lines WHERE chan = 'sys' ORDER BY id")]


# -- HEALTH-15 S03: each shutdown step is contained --------------------------------------------


@pytest.mark.parametrize("owner, name", [
    (PortManager, "stop_all"), (UpdateChecker, "aclose"), (pjstream.PlotJugglerStreamer, "close"),
])
def test_a_raising_shutdown_step_still_records_the_stop(tmp_path, monkeypatch, owner, name) -> None:
    def boom(*_a, **_k):
        raise RuntimeError(f"{name} failed")

    async def aboom(*_a, **_k):
        boom()

    monkeypatch.setattr(owner, name, aboom if inspect.iscoroutinefunction(getattr(owner, name))
                        else boom)
    with _client(_app(tmp_path)):
        pass
    rows = _sys_rows(tmp_path)
    assert "daemon start" in rows, "positive control: the query sees this run's rows"
    assert rows[-1] == "daemon stop", rows


# -- SRC-6: the PlotJuggler destination the runtime refuses -------------------------------------


def test_a_startup_that_cannot_enable_the_stream_says_so_in_status(tmp_path) -> None:
    app = _app(tmp_path, pj=PlotJugglerConfig(enabled=True, dest=MULTICAST), warnings=["loader"])
    with _client(app) as c:
        status = c.get("/status").json()
        assert status["plotjuggler"]["enabled"] is False
        warned = status["config_warnings"]
        assert warned[0] == "loader", "the loader's own warnings are kept"
        assert warned[1].startswith(f"plotjuggler: cannot enable for '{MULTICAST}': "), warned
        assert "unicast" in warned[1]


def test_a_startup_that_enables_the_stream_warns_nothing(tmp_path) -> None:
    app = _app(tmp_path, pj=PlotJugglerConfig(enabled=True, dest="127.0.0.1:9870"))
    with _client(app) as c:
        status = c.get("/status").json()
        assert status["plotjuggler"]["enabled"] is True and status["config_warnings"] == []


def test_saving_an_enabled_stream_the_runtime_would_refuse_is_refused(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        ok = c.put("/config/plotjuggler", json={"enabled": True, "dest": "127.0.0.1:9870"})
        assert ok.status_code == 200, ok.text                              # positive control
        r = c.put("/config/plotjuggler", json={"enabled": True, "dest": MULTICAST})
        assert r.status_code == 400 and "unicast" in r.json()["error"], r.text
        saved = c.get("/config").json()["plotjuggler"]
        assert saved == {"enabled": True, "dest": "127.0.0.1:9870"}, "the refusal wrote the file"


# -- PUT /config/server uses the one host check ----------------------------------------------


@pytest.mark.parametrize("host", ["   ", "a b", "a\x7fb", "a\x00b"])
def test_a_saved_host_is_held_to_the_loaders_check(tmp_path, host) -> None:
    with _client(_app(tmp_path)) as c:
        r = c.put("/config/server", json={"host": host, "port": 8558})
        assert r.status_code == 400, r.text
        assert r.json()["error"].startswith("host must be a host name or address")


def test_a_saved_host_is_stored_stripped(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        assert c.put("/config/server", json={"host": " 0.0.0.0 ", "port": 8558}).status_code == 200
        assert c.get("/config").json()["server"]["host"] == "0.0.0.0"


# -- LIFECYCLE-4: a reconnect racing a detach -------------------------------------------------


def test_a_detach_during_a_reconnect_is_not_undone(tmp_path, monkeypatch) -> None:
    with _client(_app(tmp_path)) as c:
        assert c.post("/ports", json={"alias": "r", "device": UNOPENABLE}).status_code == 200
        parked, release = threading.Event(), threading.Event()
        real_prime = SerialPort.prime_plot_defs

        async def slow_prime(self) -> None:
            parked.set()
            await asyncio.to_thread(release.wait, 10)
            await real_prime(self)

        monkeypatch.setattr(SerialPort, "prime_plot_defs", slow_prime)
        answer: list = []
        t = threading.Thread(target=lambda: answer.append(c.post("/ports/r/reconnect")))
        t.start()
        try:
            assert parked.wait(10), "the reconnect never reached its prime"
            assert c.delete("/ports/r").json() == {"ok": True}
        finally:
            release.set()
            t.join(10)
        assert answer[0].status_code == 400, answer[0].text
        assert answer[0].json() == {"error": "no such port: r"}
        assert c.get("/ports").json()["ports"] == [], "the detached port came back"


def test_bad_autoconnect_port_does_not_abort_startup(tmp_path) -> None:
    # One bad config entry (disallowed device scheme) must not kill the daemon.
    from fastapi.testclient import TestClient

    from mcuscope.config import Config, PortConfig, ServerConfig, StorageConfig
    from mcuscope.server import create_app

    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[
            PortConfig(alias="bad", device="spy://COM1", baud=115200, autoconnect=True),
        ],
    )
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/status")
        assert r.status_code == 200
        # the failure is recorded as a sys row
        rows = c.get("/lines", params={"chan": "sys", "limit": 10}).json()["lines"]
        assert any("autoconnect bad failed" in row["raw"] for row in rows)
