"""Static web UI serving (SPEC 9.1): the daemon mounts webui/ at /ui and redirects /.

These are endpoint-level checks; the UI's own JS logic is exercised manually against
the simulator (see the smoke script in the Phase 6 notes).
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import uvicorn

from mcuscope import protocol as p
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.serial_link import SerialPort
from mcuscope.server import create_app
from tests.support import Stack, free_port


def client(stack: Stack, follow: bool = False) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=5.0, follow_redirects=follow)


def test_root_redirects_to_ui(stack: Stack) -> None:
    with client(stack) as c:
        r = c.get("/")
    assert r.status_code in (307, 308)
    assert r.headers["location"].rstrip("/").endswith("/ui")


def test_ui_index_served(stack: Stack) -> None:
    with client(stack, follow=True) as c:
        r = c.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "mcu" in r.text
    assert 'src="app.js"' in r.text


def test_ui_static_assets_served(stack: Stack) -> None:
    with client(stack) as c:
        js = c.get("/ui/app.js")
        css = c.get("/ui/style.css")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "refreshStatus" in js.text
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


def test_ui_assets_are_no_cache(stack: Stack) -> None:
    # The UI files change between daemon versions; the daemon marks them no-cache so browsers
    # always revalidate instead of serving a stale index.html/app.js/style.css after an update.
    with client(stack, follow=True) as c:
        for path in ("/ui/", "/ui/app.js", "/ui/style.css"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers.get("cache-control") == "no-cache", path


def test_ui_vendor_uplot_served(stack: Stack) -> None:
    # Phase 7 vendors uPlot (JS + CSS) under webui/vendor/ for the plot panel; the page
    # references them, so they must be served (offline, no CDN) like the rest of the UI.
    with client(stack) as c:
        js = c.get("/ui/vendor/uPlot.iife.min.js")
        css = c.get("/ui/vendor/uPlot.min.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "uPlot" in js.text
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


def test_devices_endpoint(stack: Stack) -> None:
    # Populates the attach dialog (SPEC 9.1). The sim is a socket:// URL so it never appears in
    # list_ports; the endpoint must still return a well-formed (possibly empty) device list.
    with client(stack) as c:
        r = c.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("devices"), list)
    for dev in body["devices"]:
        assert "device" in dev


def test_plot_channels_reports_kinds(tmp_path) -> None:
    # /plot/channels must surface the enum/bits render metadata plot_channel_meta()
    # exposes (kind, labels, group, bit), not just the analog type/unit/scale trio.
    # No simulator here: the sim does not emit enum/bits streams, so a bare daemon (no
    # autoconnect ports) is stood up and a SerialPort is wired in by hand, the same way
    # test_plot.py's test_plot_channel_meta_enum_and_bits seeds `_plot_defs` directly.
    http_port = free_port()
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=http_port),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db"), retention_days=7),
        ports=[],
    )
    app = create_app(config)
    uconfig = uvicorn.Config(app, host="127.0.0.1", port=http_port, log_level="warning")
    server = uvicorn.Server(uconfig)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not getattr(server, "started", False):
            time.sleep(0.02)
        assert server.started, "daemon did not start"

        store = app.state.store
        ports = app.state.ports
        loop = ports._loop  # the daemon's own event loop, running in `thread`

        port = SerialPort(store, loop, "board")
        port._plot_defs = {
            "0": p.parse_plot_def("!pd 0 state:u1:=0=IDLE,1=ARMED gpio:u1:/led,irq")
        }
        ports._ports["board"] = port
        fut = asyncio.run_coroutine_threadsafe(
            port._store_rx_line(time.time(), "!ps 0 10 01,02"), loop
        )
        fut.result(timeout=5.0)

        with httpx.Client(base_url=f"http://127.0.0.1:{http_port}", timeout=5.0) as c:
            chans = {ch["name"]: ch for ch in c.get("/plot/channels").json()["channels"]}
    finally:
        server.should_exit = True
        thread.join(timeout=8.0)

    assert chans["state"]["kind"] == "enum"
    assert chans["state"]["labels"] == [[0, "IDLE"], [1, "ARMED"]]
    assert chans["led"]["kind"] == "bit"
    assert chans["led"]["group"] == "gpio"
    assert chans["led"]["bit"] == 0
    assert chans["irq"]["kind"] == "bit"
    assert chans["irq"]["group"] == "gpio"
    assert chans["irq"]["bit"] == 1


def test_lines_backfill_is_newest_first(stack: Stack) -> None:
    # The terminal backfills the newest 200 lines via order=desc (SPEC 9.1) and reverses them
    # client-side. Guard that contract: order=desc returns ids strictly newest-first, capped.
    with client(stack) as c:
        deadline = time.monotonic() + 5.0
        lines: list[dict] = []
        while time.monotonic() < deadline:
            lines = c.get("/lines", params={"order": "desc", "limit": 10}).json()["lines"]
            if len(lines) >= 2:
                break
            time.sleep(0.1)
    assert len(lines) >= 2, "sim should have produced capture lines"
    ids = [ln["id"] for ln in lines]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) <= 10
