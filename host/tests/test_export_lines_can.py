"""Bounded windows and streaming exports: `until_ts`, /lines/export, /can/frames CSV.

The rows are seeded with explicit timestamps rather than taken from the simulator, because
every assertion here is about which side of a bound a row falls on.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from mcuscope import store as store_mod
from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.render import fmt_line
from mcuscope.server import create_app, export_filename

T0 = 1_700_000_000.0        # a fixed epoch, so a bound is never "now" by accident
RAW_TRICKY = 'a, b "quoted"'


def _mk_app(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "export.db")),
    )
    return create_app(config, config_path=tmp_path / "config.toml")


def _on_loop(client, coro):
    return asyncio.run_coroutine_threadsafe(coro, client.app.state.ports._loop).result(5)


def _add(client, *, ts: float, raw: str, chan: str = "debug", port: str = "board", can=None):
    store = client.app.state.store
    return _on_loop(
        client,
        store.add_line(ts=ts, port=port, dir="rx", chan=chan, seq=None, raw=raw, can=can),
    )


def _seed(client):
    """Five debug lines one second apart from T0, the last one CSV-hostile."""
    rows = [_add(client, ts=T0 + i, raw=f"line{i}") for i in range(4)]
    rows.append(_add(client, ts=T0 + 4, raw=RAW_TRICKY))
    return rows


def _can(client, ts: float, can_id: int, *, bus: int = 1, data: bytes = b"\xaa", ext=False):
    return _add(
        client, ts=ts, chan="event", raw=f"!can - {can_id:X} {data.hex().upper()}",
        can={"tick_ms": 5, "bus": bus, "can_id": can_id, "ext": ext, "rtr": False,
             "dlc": len(data), "data": data},
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(_mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


# -- until_ts on the query endpoints ----------------------------------------------------


def test_until_ts_includes_its_own_second_and_nothing_later(client) -> None:
    _seed(client)
    body = client.get("/lines", params={"until_ts": T0 + 2, "limit": 100}).json()
    assert [r["raw"] for r in body["lines"]] == ["line2", "line1", "line0"], (
        "until_ts is inclusive, like since_ts is not: the row stamped exactly at the bound "
        "belongs to the window"
    )


def test_until_ts_before_the_capture_selects_nothing(client) -> None:
    _seed(client)
    body = client.get("/lines", params={"until_ts": T0 - 3600, "limit": 100}).json()
    assert body["lines"] == []
    frames = client.get("/can/frames", params={"until_ts": T0 - 3600}).json()
    assert frames["frames"] == []


def test_inverted_bounds_are_refused_not_silently_empty(client) -> None:
    """An inverted window selects nothing, which reads exactly like an empty capture."""
    for path in ("/lines", "/lines/export"):
        r = client.get(path, params={"since_ts": T0 + 5, "until_ts": T0 + 1})
        assert r.status_code == 400, path
        assert r.json()["error"] == "until_ts is before since_ts", path
    equal = client.get("/lines", params={"since_ts": T0, "until_ts": T0})
    assert equal.status_code == 200, "an instant-wide window is a window, not an inversion"


def test_until_ts_intersects_a_session_rather_than_replacing_it(client) -> None:
    _add(client, ts=T0, raw="before")
    _on_loop(client, client.app.state.store.start_session("run one"))
    _add(client, ts=T0 + 1, raw="inside-early")
    _add(client, ts=T0 + 2, raw="inside-late")
    _on_loop(client, client.app.state.store.stop_session())
    _add(client, ts=T0 + 3, raw="after")

    body = client.get(
        "/lines", params={"session": "run one", "until_ts": T0 + 1, "limit": 100}
    ).json()
    raws = [r["raw"] for r in body["lines"]]
    assert "inside-early" in raws and "inside-late" not in raws, "until_ts must narrow"
    assert "before" not in raws, "the session bound must still apply"


def test_plot_export_takes_until_ts(client) -> None:
    for i in range(3):
        _on_loop(
            client,
            client.app.state.store.add_line(
                ts=T0 + i, port="board", dir="rx", chan="event", seq=None, raw=f"!p {i}",
                plot=[(i, "s1", "temp", float(i))],
            ),
        )
    csv = client.get("/plot/export", params={"names": "temp", "until_ts": T0 + 1}).text
    assert csv.count("\n") == 3, f"header plus two rows, got {csv!r}"


# -- /lines/export ----------------------------------------------------------------------


def test_text_export_is_byte_identical_to_the_cli_renderer(client) -> None:
    rows = _seed(client)
    body = client.get("/lines/export", params={"format": "text", "chan": "debug"}).text
    assert body == "".join(fmt_line(r) + "\n" for r in rows)


def test_jsonl_export_carries_the_same_keys_as_a_lines_row(client) -> None:
    _seed(client)
    api_row = client.get("/lines", params={"limit": 1, "order": "asc"}).json()["lines"][0]
    first = json.loads(client.get("/lines/export", params={"format": "jsonl"}).text.split("\n")[0])
    assert first == api_row


def test_csv_export_quotes_a_raw_line_with_a_comma_and_a_quote(client) -> None:
    _seed(client)
    lines = client.get("/lines/export", params={"format": "csv"}).text.splitlines()
    assert lines[0] == "id,ts,port,dir,chan,seq,raw"
    assert lines[-1].endswith('"a, b ""quoted"""'), (
        f"an unescaped comma or quote breaks the row into cells: {lines[-1]!r}"
    )


def test_export_streams_every_page_once(client, monkeypatch) -> None:
    """The id cursor is what advances between pages; off by one and rows repeat or vanish."""
    monkeypatch.setattr(store_mod, "_EXPORT_PAGE", 2)
    for i in range(7):
        _add(client, ts=T0 + i, raw=f"page{i}")
    body = client.get("/lines/export", params={"format": "text", "chan": "debug"}).text
    assert [line.split("| ")[1] for line in body.splitlines()] == [f"page{i}" for i in range(7)]


def test_export_has_no_row_limit(client) -> None:
    """`limit` is a query concern; an export returns the whole selection or fails visibly."""
    for i in range(150):
        _add(client, ts=T0 + i, raw=f"bulk{i}")
    body = client.get(
        "/lines/export", params={"format": "text", "limit": 10, "chan": "debug"}
    ).text
    assert len(body.splitlines()) == 150


def test_export_applies_the_match_filter(client) -> None:
    _seed(client)
    body = client.get("/lines/export", params={"format": "text", "match": "line[13]"}).text
    assert [line.split("| ")[1] for line in body.splitlines()] == ["line1", "line3"]


def test_export_refuses_a_bad_match_the_way_lines_does(client) -> None:
    r = client.get("/lines/export", params={"format": "text", "match": "(unclosed"})
    assert r.status_code == 400 and r.json()["error"].startswith("bad match regex")


def test_export_of_an_empty_window_is_a_header_or_nothing(client) -> None:
    _seed(client)
    params = {"until_ts": T0 - 3600}
    assert client.get("/lines/export", params={**params, "format": "text"}).text == ""
    assert client.get("/lines/export", params={**params, "format": "jsonl"}).text == ""
    csv = client.get("/lines/export", params={**params, "format": "csv"}).text
    assert csv == "id,ts,port,dir,chan,seq,raw\n", "a CSV keeps its header so the file parses"


def test_export_rejects_an_unknown_format_by_name(client) -> None:
    r = client.get("/lines/export", params={"format": "xml"})
    assert r.status_code == 400
    assert r.json()["error"] == "format must be 'text', 'jsonl' or 'csv'"


def test_export_media_types_and_extensions_match_the_format(client) -> None:
    _seed(client)
    for fmt, media, ext in (
        ("text", "text/plain", "txt"),
        ("jsonl", "application/x-ndjson", "jsonl"),
        ("csv", "text/csv", "csv"),
    ):
        r = client.get("/lines/export", params={"format": fmt})
        assert r.headers["content-type"].startswith(media), fmt
        assert r.headers["content-disposition"].endswith(f'.{ext}"'), fmt


def test_export_filename_carries_the_session_with_spaces_replaced(client) -> None:
    _on_loop(client, client.app.state.store.start_session("run one/two"))
    _add(client, ts=time.time(), raw="inside")
    _on_loop(client, client.app.state.store.stop_session())
    r = client.get("/lines/export", params={"session": "run one/two", "format": "text"})
    header = r.headers["content-disposition"]
    name = header.split('filename="')[1].rstrip('"')
    assert name.startswith("run_one_two_lines_"), header
    assert not any(c in name for c in (" ", "/", '"')), (
        f"an unescaped space, separator or quote breaks the download name: {header}"
    )


# -- /can/frames: id lists, until_ts, csv ------------------------------------------------


def test_can_id_list_selects_every_listed_id_and_no_other(client) -> None:
    _can(client, T0, 0x100)
    _can(client, T0 + 1, 0x200)
    _can(client, T0 + 2, 0x300)
    frames = client.get("/can/frames", params={"id": "0x100,200"}).json()["frames"]
    assert sorted(f["can_id"] for f in frames) == [0x100, 0x200], (
        "the list must accept both spellings /can/frames already takes"
    )


def test_can_id_list_refuses_an_empty_or_unparsable_element(client) -> None:
    for bad, msg in (("100,", "bad can id: "), ("100,zz", "bad can id: zz"),
                     (",100", "bad can id: ")):
        r = client.get("/can/frames", params={"id": bad})
        assert r.status_code == 400, bad
        assert r.json()["error"] == msg, bad
    over = client.get("/can/frames", params={"id": "100,FFFFFFFF"})
    assert over.status_code == 400 and "out of range" in over.json()["error"]


def test_can_csv_columns_and_flag_encoding(client) -> None:
    _can(client, T0, 0x123, bus=2, data=b"\xde\xad", ext=True)
    text = client.get("/can/frames", params={"format": "csv"}).text
    header, row = text.splitlines()
    assert header == "id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data"
    cells = row.split(",")
    assert cells[3:] == ["2", str(0x123), "1", "0", "2", "DEAD"], (
        f"bus/can_id/ext/rtr/dlc/data must survive the CSV as stored: {row!r}"
    )


def test_can_csv_ignores_limit_and_streams_the_whole_selection(client) -> None:
    for i in range(120):
        _can(client, T0 + i, 0x100 + i)
    text = client.get("/can/frames", params={"format": "csv", "limit": 5}).text
    assert len(text.splitlines()) == 121, "limit is a JSON concern; the CSV is the window"


def test_can_csv_is_ascending_where_the_json_is_newest_first(client) -> None:
    for i in range(3):
        _can(client, T0 + i, 0x100 + i)
    rows = client.get("/can/frames", params={"format": "csv"}).text.splitlines()[1:]
    assert [int(r.split(",")[4]) for r in rows] == [0x100, 0x101, 0x102]
    js = client.get("/can/frames").json()["frames"]
    assert [f["can_id"] for f in js] == [0x102, 0x101, 0x100]


def test_can_csv_of_an_empty_window_keeps_its_header(client) -> None:
    _can(client, T0, 0x100)
    text = client.get("/can/frames", params={"format": "csv", "until_ts": T0 - 60}).text
    assert text == "id,ts,tick_ms,bus,can_id,ext,rtr,dlc,data\n"


def test_can_frames_rejects_an_unknown_format_by_name(client) -> None:
    r = client.get("/can/frames", params={"format": "text"})
    assert r.status_code == 400 and r.json()["error"] == "format must be 'json' or 'csv'"


def test_can_csv_filename_is_the_can_kind(client) -> None:
    _can(client, T0, 0x100)
    name = client.get("/can/frames", params={"format": "csv"}).headers["content-disposition"]
    assert "capture_can_" in name and name.endswith('.csv"'), name


# -- export_filename --------------------------------------------------------------------


def test_export_filename_replaces_every_unsafe_character() -> None:
    name = export_filename("lines", 'a b/c\\d"e:f*', None, None, "txt")
    assert name == "a_b_c_d_e_f__lines_start-end.txt"


def test_export_filename_without_a_session_is_the_whole_capture() -> None:
    assert export_filename("plot", None, None, None, "csv") == "capture_plot_start-end.csv"
    assert export_filename("can", "", None, None, "csv").startswith("capture_can_")


def test_export_filename_names_only_the_bounded_side() -> None:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(T0))
    assert export_filename("lines", None, T0, None, "txt") == f"capture_lines_{stamp}-end.txt"
    assert export_filename("lines", None, None, T0, "txt") == f"capture_lines_start-{stamp}.txt"
    both = export_filename("bundle", "run", T0, T0 + 60, "zip")
    assert both.startswith(f"run_bundle_{stamp}-") and both.endswith(".zip")
