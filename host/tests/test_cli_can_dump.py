"""`mcu can dump`: its window and paging against the daemon's cap, `--session`, and the
follow's polls (SPEC 4)."""

from __future__ import annotations

import json
import threading
import time

import httpx

from mcuscope import cli
from tests.support import STATUS, UNREACHABLE, canned, record_params, record_requests, recorder

# -- improvement 11 and the -f bound: `can dump` ---------------------------------------


def test_can_dump_forwards_the_session(monkeypatch, capsys) -> None:
    seen = recorder(monkeypatch, can_frames={"frames": []})
    rc = cli.main(["can", "dump", "--session", "run-3", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    assert dict(seen[0].url.params)["session"] == "run-3", seen[0].url


def test_can_dump_refuses_an_upper_bound_with_follow(capsys) -> None:
    """The follow polls live frames and cannot honour --to, so it ran past it for ever."""
    rc = cli.main(["can", "dump", "--to", "23:59", "-f", *UNREACHABLE])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "--to" in err and "-f" in err, err


def test_can_dump_still_follows_with_a_lower_bound(monkeypatch, capsys) -> None:
    """--from bounds the backfill only, and everything live is after it by definition."""
    recorder(monkeypatch, can_frames={"frames": []})
    monkeypatch.setattr(cli, "_dump_follow", lambda *a, **kw: None)
    rc = cli.main(["can", "dump", "--from", "00:01", "-f", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err


# -- FC-1: `can dump` converts --last-ms once, before paging -----------------------------

def _sliding_can_daemon(monkeypatch, total: int, span_s: float, latency_s: float) -> list:
    """/can/frames whose `last_ms` floor is re-evaluated per request, as the daemon's is.

    `total` frames end at "now" and reach `span_s` back; the daemon's clock jumps
    `latency_s` per request, so a walk that resends `last_ms` loses the rows it is walking
    towards. `since_ts` is absolute and loses nothing: the two answers are what separates
    the fix from the defect.
    """
    t0 = time.time()
    ts = {i: t0 - span_s + (i - 1) * (span_s / total) for i in range(1, total + 1)}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={**STATUS, "now": t0})
        q = request.url.params
        now = t0 + calls["n"] * latency_s
        calls["n"] += 1
        ids = list(range(1, total + 1))
        if "last_ms" in q:
            floor = now - int(q["last_ms"]) / 1000
            ids = [i for i in ids if ts[i] > floor]
        if "since_ts" in q:
            ids = [i for i in ids if ts[i] > float(q["since_ts"])]
        if "id_to" in q:
            ids = [i for i in ids if i <= int(q["id_to"])]
        ids.reverse()
        cap = min(int(q.get("limit", 100)), 1000)
        page = [{"line_id": i, "ts": ts[i], "tick_ms": None, "bus": 1, "can_id": 256,
                 "ext": 0, "rtr": 0, "dlc": 1, "data_hex": "00"} for i in ids[:cap]]
        if q.get("format") == "csv":
            body = "line_id\n" + "".join(f"{f['line_id']}\n" for f in page)
            return httpx.Response(200, text=body, headers={"content-type": "text/csv"})
        return httpx.Response(200, json={"frames": page, "truncated": len(ids) > cap})

    return record_params(monkeypatch, handler)


def _printed_ids(out: str) -> list:
    return [json.loads(line)["line_id"] for line in out.splitlines() if line.strip()]


def test_can_dump_n_with_last_ms_keeps_the_window_it_started_with(monkeypatch, capsys) -> None:
    """A three-page walk at half a second a page: the sliding floor would eat the oldest."""
    seen = _sliding_can_daemon(monkeypatch, 3000, span_s=2.9, latency_s=0.5)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "2500", "--last-ms", "3000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    ids = _printed_ids(cap.out)
    assert ids == list(range(501, 3001)), (len(ids), ids[:2], ids[-2:])
    assert "truncated at 2500 rows" in cap.err
    frames = [q for path, q in seen if path == "/can/frames"]
    assert len(frames) == 3, frames
    assert all("last_ms" not in q for q in frames), frames
    assert all("since_ts" in q for q in frames), frames   # the window is still applied


def test_can_dump_csv_with_last_ms_also_sends_the_absolute_window(monkeypatch, capsys) -> None:
    """The `--csv` request is one request, but it must name the same window as the walk."""
    seen = _sliding_can_daemon(monkeypatch, 10, span_s=2.9, latency_s=0.5)
    rc = cli.main([*UNREACHABLE, "can", "dump", "--csv", "--last-ms", "3000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    frames = [q for path, q in seen if path == "/can/frames"]
    assert frames and all("last_ms" not in q for q in frames), frames
    assert all("since_ts" in q and q["format"] == "csv" for q in frames), frames


def test_can_dump_without_last_ms_sends_no_window_at_all(monkeypatch, capsys) -> None:
    """Positive control: the conversion invents no bound where the user asked for none."""
    seen = _sliding_can_daemon(monkeypatch, 10, span_s=2.9, latency_s=0.5)
    assert cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5"]) == 0, capsys.readouterr()
    frames = [q for path, q in seen if path == "/can/frames"]
    assert frames and all("since_ts" not in q and "last_ms" not in q for q in frames), frames


# -- B-1: a session-scoped follow stays in the session -----------------------------------


def test_can_dump_follow_polls_inside_the_session(monkeypatch, capsys) -> None:
    import time

    frames: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status":
            return httpx.Response(200, json={**STATUS, "capture": "A"})
        frames.append(request)
        if "since_id" in request.url.params:
            raise KeyboardInterrupt      # the first live poll is all this needs
        return httpx.Response(200, json={"frames": []})

    canned(monkeypatch, handler)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    rc = cli.main(["can", "dump", "--session", "runA", "-f", *UNREACHABLE])
    assert rc == 0, capsys.readouterr().err
    polls = [r for r in frames if "since_id" in r.url.params]
    assert polls, "the follow never polled"
    assert all(r.url.params.get("session") == "runA" for r in frames), \
        [str(r.url) for r in frames]


# -- B-14: can dump -o with --json describes the file --------------------------------------


def test_can_dump_to_a_file_with_json_prints_the_summary(monkeypatch, capsys, tmp_path) -> None:
    recorder(monkeypatch, can_frames="ts,can_id\n1,256\n2,257\n")
    out = tmp_path / "cj.csv"
    rc = cli.main(["--json", "can", "dump", "-o", str(out), *UNREACHABLE])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert json.loads(captured.out) == {"file": str(out), "frames": 2,
                                        "bytes": out.stat().st_size}


def _failing_poll(fail):
    def handler(request):
        if request.url.path == "/can/frames" and "since_id" not in request.url.params:
            return httpx.Response(200, json={"frames": [], "truncated": False})
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c"})
        return fail(request)
    return handler


def test_a_can_follow_on_a_daemon_answering_500_gives_up_as_1_not_unreachable(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(cli, "FOLLOW_GIVE_UP_S", 0.2)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.01)
    record_requests(monkeypatch,
                    _failing_poll(lambda r: httpx.Response(500, json={"error": "boom"})))
    rc = cli.main(["can", "dump", "-n", "0", "-f"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "kept failing for 0.2s" in err and "unreachable" not in err, err


def test_a_can_follow_whose_polls_cannot_connect_still_gives_up_as_3(monkeypatch, capsys) -> None:
    """The control: a transport failure is the unreachable daemon exit 3 names."""
    monkeypatch.setattr(cli, "FOLLOW_GIVE_UP_S", 0.2)
    monkeypatch.setattr(cli, "FOLLOW_POLL_S", 0.01)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    record_requests(monkeypatch, _failing_poll(refuse))
    rc = cli.main(["can", "dump", "-n", "0", "-f"])
    err = capsys.readouterr().err
    assert rc == 3, err
    assert "daemon unreachable at" in err and "for 0.2s" in err, err


def _frames_daemon(monkeypatch, total: int, honour_id_to: bool = True) -> list:
    """A /can/frames double with the daemon's 1000-frame cap, newest first."""
    import httpx

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        seen.append(dict(q))
        if request.url.path == "/status":
            return httpx.Response(200, json={"capture": "c1"})
        ids = list(range(1, total + 1))
        if "since_id" in q:
            ids = [i for i in ids if i > int(q["since_id"])]
        if honour_id_to and "id_to" in q:
            ids = [i for i in ids if i <= int(q["id_to"])]
        ids.reverse()
        cap = min(int(q.get("limit", 100)), 1000)
        page = [{"line_id": i, "ts": 1.0, "tick_ms": None, "bus": 1, "can_id": 256, "ext": 0,
                 "rtr": 0, "dlc": 1, "data_hex": "00"} for i in ids[:cap]]
        return httpx.Response(200, json={"frames": page, "truncated": len(ids) > cap})

    canned(monkeypatch, handler)
    return seen


def test_can_dump_n_pages_past_the_daemon_cap(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "2500"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    ids = _printed_ids(cap.out)
    assert ids == list(range(501, 3001)), (len(ids), ids[:3], ids[-3:])
    assert "truncated at 2500 rows" in cap.err


def test_can_dump_n_above_the_window_prints_all_and_no_note(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert _printed_ids(cap.out) == list(range(1, 3001))
    assert "truncated" not in cap.err


def test_can_dump_against_a_daemon_ignoring_id_to_stops_and_notes(monkeypatch, capsys) -> None:
    seen = _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    rc = cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert len(seen) <= 3, f"paged the same page {len(seen)} times"
    assert "truncated at" in cap.err


def _client():
    from mcuscope.cli_client import Settings
    return cli.Client(Settings(url="http://127.0.0.1:1", json_out=False, port=None))


def test_a_follow_poll_pages_every_frame_past_the_watermark(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000)
    got = cli._poll_new_frames(_client(), {"limit": 1000}, since=500)
    assert [f["line_id"] for f in got] == list(range(3000, 500, -1))


def test_a_follow_poll_after_a_capture_change_replays_one_page(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000)
    got = cli._poll_new_frames(_client(), {"limit": 1000}, since=0)
    assert [f["line_id"] for f in got] == list(range(3000, 2000, -1))


def test_a_follow_poll_against_a_daemon_ignoring_id_to_returns(monkeypatch) -> None:
    _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    got: list = []
    t = threading.Thread(
        target=lambda: got.append(cli._poll_new_frames(_client(), {"limit": 1000}, since=500)),
        daemon=True)
    t.start()
    t.join(5)
    assert got, "the poll paged the same page for ever"
    assert len(got[0]) == 1000


def test_can_dump_ignoring_id_to_prints_no_frame_twice(monkeypatch, capsys) -> None:
    _frames_daemon(monkeypatch, 3000, honour_id_to=False)
    assert cli.main([*UNREACHABLE, "--json", "can", "dump", "-n", "5000"]) == 0
    ids = _printed_ids(capsys.readouterr().out)
    assert len(ids) == len(set(ids)) == 1000
