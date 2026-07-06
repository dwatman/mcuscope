"""Static web UI serving (SPEC 9.1): the daemon mounts webui/ at /ui and redirects /.

These are endpoint-level checks; the UI's own JS logic is exercised manually against
the simulator (see the smoke script in the Phase 6 notes).
"""

from __future__ import annotations

import time

import httpx

from tests.support import Stack


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
