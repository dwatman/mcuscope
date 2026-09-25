"""Static web UI serving (SPEC 9.1): the daemon mounts webui/ at /ui and redirects /.

These are endpoint-level checks; the UI's own JS logic is exercised manually against
the simulator (see the smoke script in the Phase 6 notes).
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

import mcuscope
from mcuscope import __version__
from mcuscope import config as config_mod
from mcuscope import server as server_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.protocol import EOL_BYTES
from mcuscope.serial_link import SerialPort
from mcuscope.server import create_app
from tests.support import Stack, free_port, mk_app, on_loop, stack_client
from tests.test_server_exports import _app, _client


def test_root_redirects_to_ui(stack: Stack) -> None:
    with stack_client(stack) as c:
        r = c.get("/")
    assert r.status_code in (307, 308)
    assert r.headers["location"].rstrip("/").endswith("/ui")


def test_ui_index_served(stack: Stack) -> None:
    with stack_client(stack, follow=True) as c:
        r = c.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "mcu" in r.text
    assert 'src="app.js"' in r.text


def test_ui_static_assets_served(stack: Stack) -> None:
    with stack_client(stack) as c:
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
    with stack_client(stack, follow=True) as c:
        for path in ("/ui/", "/ui/app.js", "/ui/style.css"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers.get("cache-control") == "no-cache", path


def test_ui_vendor_uplot_served(stack: Stack) -> None:
    # Phase 7 vendors uPlot (JS + CSS) under webui/vendor/ for the plot panel; the page
    # references them, so they must be served (offline, no CDN) like the rest of the UI.
    with stack_client(stack) as c:
        js = c.get("/ui/vendor/uPlot.iife.min.js")
        css = c.get("/ui/vendor/uPlot.min.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "uPlot" in js.text
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


def test_devices_endpoint(stack: Stack) -> None:
    # Populates the attach dialog (SPEC 9.1). The sim is a socket:// URL so it never appears in
    # list_ports; the endpoint must still return a well-formed (possibly empty) device list.
    with stack_client(stack) as c:
        r = c.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("devices"), list)
    for dev in body["devices"]:
        assert "device" in dev


def test_plot_channels_reports_kinds(tmp_path) -> None:
    # /plot/channels must surface the enum/bits render metadata PlotDecoder.channel_meta()
    # exposes (kind, labels, group, bit), not just the analog type/unit/scale trio.
    # No simulator here: the sim does not emit enum/bits streams, so a bare daemon (no
    # autoconnect ports) is stood up and a SerialPort is wired in by hand, the same way
    # test_plot.py's test_plot_channel_meta_enum_and_bits seeds the same def.
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
        port.plot_decoder.learn("!pd 0 state:u1:=0=IDLE,1=ARMED gpio:u1:/led,irq")
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
    with stack_client(stack) as c:
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


# A `$("id")` index.html lacks is a control that silently does nothing. The test DOM stub
# manufactures any id on demand, so the JS suite cannot see a typo in either file; this is the
# only place the two are compared.
WEBUI = Path(mcuscope.__file__).parent / "webui"


def _index_html() -> str:
    return (WEBUI / "index.html").read_text(encoding="utf-8")


def _strings(text: str) -> list[str]:
    return re.findall(r'"([^"]+)"', text)


def _ids_in(text: str) -> set[str]:
    """Every id `text` resolves: `$("...")`, and the ids reached through a variable, which are
    the `sec:`/`save:` table keys, an id array iterated into `$(id)`, and `$({...}[key])`."""
    ids = set(re.findall(r'\$\("([^"]+)"\)', text))
    ids.update(re.findall(r'\b(?:sec|save): "([^"]+)"', text))
    for _var, src in re.findall(r"for \(const (\w+) of (\[[^\]]*\]|\w+)\) \$\(\1\)", text):
        if not src.startswith("["):
            src = re.search(rf"\b{src} = (\[[^\]]*\])", text).group(1)
        ids.update(_strings(src))
    for table in re.findall(r"\$\(\{([^}]*)\}\[", text):
        ids.update(_strings(table))
    return ids


def _resolved_ids() -> tuple[set[str], set[str]]:
    """Every id a module resolves, and every id a module assigns itself."""
    resolved: set[str] = set()
    created: set[str] = set()
    for js in WEBUI.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        resolved.update(_ids_in(text))
        created.update(re.findall(r'\.id = "([^"]+)"', text))
    return resolved, created


def test_the_id_scan_follows_ids_reached_through_a_variable() -> None:
    text = (
        'const T = [{ sec: "aSec", save: "aSave", name: "A" }];\n'
        "for (const s of T) $(s.sec).x();\n"
        'const LIST = ["bOne",\n  "bTwo"];\n'
        "for (const id of LIST) $(id).disabled = on;\n"
        'for (const id of ["cOne", "cTwo"]) $(id).hidden = true;\n'
        '$({ a: "dOne", b: "dTwo" }[mode]).focus();\n'
    )
    assert _ids_in(text) == {"aSec", "aSave", "bOne", "bTwo", "cOne", "cTwo", "dOne", "dTwo"}
    resolved, _ = _resolved_ids()
    reached_only_by_table = {"cfgSecServer", "cfgSecStorage", "cfgSecUpdate", "cfgSecToken",
                             "cfgSecPorts"}
    assert reached_only_by_table <= resolved


def test_index_declares_every_id_the_modules_resolve() -> None:
    resolved, created = _resolved_ids()
    assert len(resolved) > 100, "the scan no longer finds the modules' $() calls"
    html_ids = set(re.findall(r'\bid="([^"]+)"', _index_html()))
    missing = sorted(resolved - created - html_ids)
    assert not missing, f"index.html is missing ids the JS resolves: {missing}"


def test_cmd_eol_select_offers_the_port_default() -> None:
    """A pick in the command bar is a browser-side override of the port's own eol; without a
    way back to "port default" the only escape is clearing localStorage."""
    select = _index_html().split('id="cmdEol"', 1)[1].split("</select>", 1)[0]
    assert '<option value="">' in select


def test_the_web_ui_eol_choices_are_the_daemons() -> None:
    """state.js fills every line-ending select; a value the daemon lacks is a 422 on send."""
    state_js = (Path(mcuscope.__file__).parent / "webui" / "state.js").read_text(encoding="utf-8")
    table = re.search(r"const EOL_CHOICES = \[(.*)\];", state_js).group(1)
    assert sorted(re.findall(r'\["(\w+)", "', table)) == sorted(EOL_BYTES)


def test_static_js_is_served_as_javascript_whatever_the_registry_says() -> None:
    """A registry .js -> text/plain mapping blanked the whole UI: app.js is a module."""
    import mimetypes

    from mcuscope.server import _pin_static_mimetypes

    mimetypes.add_type("text/plain", ".js")   # simulate the hostile registry entry
    try:
        _pin_static_mimetypes()
        assert "javascript" in (mimetypes.guess_type("app.js")[0] or "")
        assert mimetypes.guess_type("style.css")[0] == "text/css"
    finally:
        _pin_static_mimetypes()


# -- chrome F1: the page carries the serving version ------------------------------------------


def test_the_served_page_carries_the_daemon_version(tmp_path) -> None:
    with _client(_app(tmp_path)) as c:
        r = c.get("/ui/")
        assert r.status_code == 200
        assert f'<meta name="mcuscope-version" content="{__version__}">' in r.text
        assert "__MCUSCOPE_VERSION__" not in r.text
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert c.get("/ui/index.html").text == r.text
        again = c.get("/ui/", headers={"if-none-match": r.headers["etag"]})
        assert again.status_code == 304 and again.headers["etag"] == r.headers["etag"]
        # Other files keep StaticFiles' own response.
        js = c.get("/ui/app.js")
        assert js.status_code == 200 and "text/javascript" in js.headers["content-type"]


def test_a_new_version_changes_the_pages_etag_though_the_file_did_not(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "index.html").write_text(
        '<meta name="mcuscope-version" content="__MCUSCOPE_VERSION__">', encoding="utf-8")
    app = Starlette(routes=[Mount("/ui", server_mod._NoCacheStatic(directory=tmp_path, html=True))])
    with TestClient(app) as c:
        old = c.get("/ui/")
        assert 'content="' + __version__ + '"' in old.text
        server_mod._stamped_index.cache_clear()
        monkeypatch.setattr(server_mod, "__version__", "9.9.9")
        new = c.get("/ui/", headers={"if-none-match": old.headers["etag"]})
        assert new.status_code == 200, "a cached page of the old version was revalidated"
        assert 'content="9.9.9"' in new.text
    server_mod._stamped_index.cache_clear()


# -- the JS tests' hand-kept mirrors of index.html and the daemon (R27-23, N-JS-3, R75-1) --------

JS_TESTS = Path(__file__).resolve().parent / "webui_js"


def _selector_classes(text: str) -> set[str]:
    """Every `.class` in a querySelector(All) or closest literal."""
    sels = re.findall(r"""(?:querySelector(?:All)?|closest)\(\s*["'`]([^"'`]*)["'`]""", text)
    return {c for s in sels for c in re.findall(r"\.([A-Za-z0-9_-]+)", s)}


def _missing_classes(html: str, sources: list[str], vendor: str) -> list[str]:
    """Selector classes that neither index.html, a module's own className, nor uPlot declares."""
    declared = {c for a in re.findall(r'\bclass="([^"]*)"', html) for c in a.split()}
    for text in sources:
        for lit in re.findall(r"""className\s*=\s*["'`]([^"'`]*)["'`]""", text):
            declared.update(re.findall(r"[A-Za-z0-9_-]+", lit))
    wanted = set().union(*(_selector_classes(t) for t in sources))
    return sorted(c for c in wanted - declared if f'"{c}"' not in vendor and f"'{c}'" not in vendor)


def test_every_class_the_modules_select_is_declared() -> None:
    """The stub hands back a detached element for a selector that matches nothing, so a renamed
    class breaks the page and no JS test."""
    sources = [js.read_text(encoding="utf-8") for js in WEBUI.glob("*.js")]
    vendor = "".join(p.read_text(encoding="utf-8") for p in (WEBUI / "vendor").rglob("*.js"))
    assert len(set().union(*(_selector_classes(t) for t in sources))) > 15, "the scan found nothing"
    assert _missing_classes(_index_html(), sources, vendor) == []
    # Negative control: the scan catches a class index.html stops declaring.
    renamed = _index_html().replace('class="side-body"', 'class="side-bodyX"')
    assert renamed != _index_html()
    assert _missing_classes(renamed, sources, vendor) == ["side-body"]


def test_the_js_config_doubles_answer_as_the_daemon_does(tmp_path) -> None:
    """settings_revision and settings_late_answers stand in for PUT /config/*: the same 409
    text, and a revision checked only when the body carries one."""
    for name in ("settings_revision.test.mjs", "settings_late_answers.test.mjs"):
        src = (JS_TESTS / name).read_text(encoding="utf-8")
        assert f'"{config_mod.CONFLICT_MESSAGE}"' in src, name
        assert "body.revision !== undefined && body.revision !== `r${d.rev}`" in src, name
    path = tmp_path / "c.toml"
    path.write_text("[server]\nport = 1\n", encoding="utf-8")
    config_mod._read_doc(path, None)   # no revision: not checked
    with pytest.raises(config_mod.ConfigConflict, match=re.escape(config_mod.CONFLICT_MESSAGE)):
        config_mod._read_doc(path, "stale")


def test_the_js_lines_clamp_is_the_daemons(tmp_path) -> None:
    """api.js pages /lines by the daemon's limit clamp; api_backfill_paging's fake serves it."""
    js = {
        "api.js": (WEBUI / "api.js").read_text(encoding="utf-8"),
        "api_backfill_paging": (JS_TESTS / "api_backfill_paging.test.mjs").read_text(
            encoding="utf-8"),
    }
    mirrored = {
        "api.js": re.search(r"const LINES_LIMIT_MAX = (\d+);", js["api.js"]),
        "api_backfill_paging": re.search(r"const SERVER_CLAMP = (\d+);", js["api_backfill_paging"]),
    }
    values = {k: int(m.group(1)) for k, m in mirrored.items() if m}
    assert len(values) == 2, mirrored
    clamp = values["api.js"]
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as client:
        store = client.app.state.store
        for i in range(clamp + 1):
            on_loop(client, store.add_line(ts=1000.0 + i, port="p", dir="rx", chan="debug",
                                           seq=None, raw=f"l{i}"))
        served = client.get("/lines", params={"limit": clamp * 5}).json()["lines"]
    assert len(served) == clamp, "the daemon's /lines clamp moved"
    assert values["api_backfill_paging"] == clamp


def test_the_export_double_declares_the_routes_parameters(tmp_path) -> None:
    """exportdlg_guards.mjs refuses a parameter its DECLARED list lacks, as the daemon does."""
    src = (JS_TESTS / "exportdlg_guards.mjs").read_text(encoding="utf-8")
    block = re.search(r"const DECLARED = \{(.*?)\n\};", src, re.S).group(1)
    double = {route: re.findall(r'"([^"]+)"', names)
              for route, names in re.findall(r'"(/[^"]+)": \[([^\]]*)\]', block)}
    paths = mk_app(tmp_path).openapi()["paths"]
    daemon = {route: [p["name"] for p in paths[route]["get"].get("parameters", [])
                      if p["in"] == "query"]
              for route in double}
    assert set(double) == {"/lines/export", "/can/frames", "/plot/export"}
    assert double == daemon
