"""Fix-diff round 2, CLI batch (FC-1 .. FC-9): the paged `can dump` window, `daemon
restart`/`start` against a daemon's own answers, the Windows stream-wrapper chain, and the
refusal wordings. Every daemon here is a canned httpx.MockTransport behind `cli.Client.open`
and every spawn a fake Popen: nothing starts mcuscoped and nothing touches port 8558.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from mcuscope import _stdio, cli, cli_output

UNREACHABLE = ["--url", "http://127.0.0.1:1"]
STATUS = {"version": "0.4.0", "uptime_s": 1.0, "ports": []}


def _mock(monkeypatch, handler) -> list:
    """Route every request through `handler`, recording (path, params) per request."""
    seen: list = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return handler(request)

    monkeypatch.setattr(cli.Client, "open",
                        lambda self: httpx.Client(transport=httpx.MockTransport(wrapped)))
    return seen


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
            return httpx.Response(200, json=STATUS)
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

    return _mock(monkeypatch, handler)


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


# -- FC-2 / FC-3: daemon start and restart ----------------------------------------------

class _FakeProc:
    """The spawned daemon: alive, with a pid the readiness probe can be made to report."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid

    def poll(self):
        return None

    def terminate(self) -> None:
        pass

    def wait(self, timeout=None) -> int:
        return 0


def _fake_spawn(monkeypatch, tmp_path, pid: int = 4242) -> list:
    """Record the argv of each `daemon start` spawn; keep the pid record out of the user's
    data dir (nothing real is started)."""
    spawns: list = []

    def popen(args, **kwargs):
        spawns.append(list(args))
        return _FakeProc(pid)

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_pid_file", lambda s: str(tmp_path / "mcuscoped.pid"))
    return spawns


def _phased(monkeypatch, phases: list) -> list:
    """Answer /status from `phases`, one entry per probe, the last repeating for ever.

    Each entry is (status_code, json body) or None for "nothing answered there".
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/status":
            return httpx.Response(200, json={"ports": []})
        entry = phases[min(calls["n"], len(phases) - 1)]
        calls["n"] += 1
        if entry is None:
            raise httpx.ConnectError("refused", request=request)
        code, body = entry
        return httpx.Response(code, json=body)

    return _mock(monkeypatch, handler)


def test_restart_carries_a_config_path_outside_the_clis_cwd(
        monkeypatch, tmp_path, capsys) -> None:
    """The daemon reports its config path absolute, so a restart from another directory
    checks and forwards that file, not a same-named one under the CLI's cwd."""
    elsewhere = tmp_path / "elsewhere" / "mcuscoped.toml"
    elsewhere.parent.mkdir()
    elsewhere.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    running = {**STATUS, "config_path": str(elsewhere), "pid": 4242}
    _phased(monkeypatch, [(200, running), None, (200, {**running, "pid": 4242})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_stop_daemon", lambda s, restarting=False: None)
    rc = cli.main([*UNREACHABLE, "daemon", "restart"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert "no such config file" not in cap.err
    assert spawns, "nothing was spawned"
    assert spawns[0][spawns[0].index("--config") + 1] == str(elsewhere), spawns[0]


def test_start_still_refuses_a_config_the_user_typed_that_does_not_exist(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control for the above: _named_config still runs on a --config, and nothing
    is spawned."""
    _phased(monkeypatch, [None])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start", "--config", str(tmp_path / "gone.toml")])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "no such config file" in err
    assert spawns == [], spawns


def test_start_reports_a_daemon_it_spawned_that_answers_with_its_guard(
        monkeypatch, tmp_path, capsys) -> None:
    """The 401 is the daemon this command just started: a success, not a failed start."""
    _phased(monkeypatch, [None, (401, {"error": "guard-sentinel-YY"})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert spawns, "nothing was spawned"
    assert "started mcuscoped (pid 4242)" in cap.out
    assert "requires a token" in cap.err and "guard-sentinel-YY" in cap.err
    assert "MCUSCOPE_TOKEN" in cap.err
    assert (tmp_path / "mcuscoped.pid").read_text(encoding="utf-8") == "4242"


def test_start_before_the_spawn_still_refuses_a_daemon_behind_a_guard(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control: the pre-spawn probe keeps the exit-1 refusal and spawns nothing,
    or a second daemon would die on the port."""
    _phased(monkeypatch, [(401, {"error": "guard-sentinel-YY"})])
    spawns = _fake_spawn(monkeypatch, tmp_path)
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert "refused the request (HTTP 401): guard-sentinel-YY" in err
    assert spawns == [], spawns


def test_start_whose_daemon_never_answers_is_still_a_failed_start(
        monkeypatch, tmp_path, capsys) -> None:
    """Positive control for the refusal arm: silence is not an answer, and the spawned
    daemon is dealt with rather than reported as started."""
    _phased(monkeypatch, [None])
    _fake_spawn(monkeypatch, tmp_path)
    monkeypatch.setenv("MCUSCOPE_START_TIMEOUT", "0.5")
    rc = cli.main([*UNREACHABLE, "daemon", "start"])
    cap = capsys.readouterr()
    assert rc == 1, cap.err
    assert "did not come up" in cap.err
    assert "started mcuscoped" not in cap.out


# -- FC-4: the Windows stream wrappers stack once, not once per main() -------------------

class _FakePipe:
    """A redirected stream: not a console, so the translator wraps it."""

    def isatty(self) -> bool:
        return False

    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass


def _chain(stream) -> list:
    names = []
    for _ in range(12):
        names.append(type(stream).__name__)
        inner = getattr(stream, "_stream", None)
        if inner is None:
            break
        stream = inner
    return names


def test_a_second_main_adds_no_stream_layer_on_windows(monkeypatch) -> None:
    """main() runs more than once in one process; each run used to add two wrappers."""
    monkeypatch.setattr(_stdio, "PIPE_CLOSE_IS_EINVAL", True)
    monkeypatch.setattr(sys, "stdout", _FakePipe())
    monkeypatch.setattr(sys, "stderr", _FakePipe())
    _stdio.translate_closed_pipe_errors()
    cli_output.guard_stdout()
    first = _chain(sys.stdout)
    assert first == ["_GuardedStdout", "_PipeErrorStream", "_FakePipe"], first
    for _ in range(3):
        _stdio.translate_closed_pipe_errors()
        cli_output.guard_stdout()
    assert _chain(sys.stdout) == first, _chain(sys.stdout)
    assert _chain(sys.stderr) == ["_PipeErrorStream", "_FakePipe"], _chain(sys.stderr)


def test_a_console_stream_is_still_left_alone(monkeypatch) -> None:
    """Positive control for the skip: an EINVAL from a real console keeps its meaning."""
    class _Console(_FakePipe):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(_stdio, "PIPE_CLOSE_IS_EINVAL", True)
    monkeypatch.setattr(sys, "stdout", _Console())
    monkeypatch.setattr(sys, "stderr", _Console())
    _stdio.translate_closed_pipe_errors()
    assert _chain(sys.stdout) == ["_Console"], _chain(sys.stdout)


# -- FC-5: the follow's frame cap is raised, not removed ---------------------------------

def test_the_follow_frame_cap_is_a_number_not_unbounded(monkeypatch, capsys) -> None:
    """max_size=None buffers whatever a wrong service on the port sends."""
    import websockets

    seen: dict = {}

    def connect(url, **kw):
        seen.update(kw)
        raise websockets.exceptions.InvalidURI(url, "stop here")

    monkeypatch.setattr(websockets, "connect", connect)
    cli.main([*UNREACHABLE, "tail", "-f", "-n", "0"])
    capsys.readouterr()
    assert seen.get("max_size") == 16 * 1024 * 1024, seen
    # 500 rows of up to 4 KB is about 2 MB: the cap has headroom and is still a cap.
    assert seen["max_size"] > 500 * 4096


# -- FC-6: the guide says what SPEC 4 says about a wait that is never answered ------------

def test_the_guide_gives_mcu_wait_the_exit_code_spec_4_gives_it() -> None:
    block = cli.AI_GUIDE.split("THE CORE LOOP")[1].split("mcu lines")[0]
    assert "never answers is exit 1, not 2" in block, block


def test_the_guide_still_names_the_wait_timeout_verdict() -> None:
    """Positive control: the clause is added to the wait block, not instead of it."""
    assert "exit 2 on" in cli.AI_GUIDE and "too many subscribers" in cli.AI_GUIDE


def test_the_guide_names_the_post_spawn_refusal() -> None:
    """FC-3's behaviour, in the one place an agent reads (SPEC 4 carries the same sentence)."""
    block = cli.AI_GUIDE.split("DAEMON CONTROL")[1]
    assert "exit 0 with a note that it wants a token" in block, block


# -- FC-8: the child suites move the config and cache dirs too ----------------------------

@pytest.mark.parametrize("name", ["test_rulings_cli_closed_pipe", "test_sweep_cli_closed_output"])
def test_the_child_suites_build_their_env_from_child_env(name) -> None:
    """A spawned child's config and cache dirs are not the user's (class 33): child_env
    moves all three, dict(os.environ, ...) moves none."""
    src = (Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8")
    assert "child_env(" in src, name
    assert "dict(os.environ" not in src, name


# -- FC-9: detach names the alias it refused ----------------------------------------------

def test_detach_says_it_refused_the_alias_rather_than_looked_it_up(monkeypatch, capsys) -> None:
    seen = _mock(monkeypatch, lambda r: httpx.Response(404, json={"error": "no such port: a"}))
    rc = cli.main([*UNREACHABLE, "detach", "a/b"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert seen == [], seen
    assert "invalid alias 'a/b'" in err
    assert "no such port" not in err, "the CLI claimed a lookup it never made"


def test_a_real_miss_still_reports_the_daemons_no_such_port(monkeypatch, capsys) -> None:
    """Positive control: the wording the refusal must not borrow does reach the user."""
    seen = _mock(monkeypatch, lambda r: httpx.Response(404, json={"error": "no such port: ab"}))
    rc = cli.main([*UNREACHABLE, "detach", "ab"])
    err = capsys.readouterr().err
    assert rc == 1, err
    assert seen, "the alias was refused before the request"
    assert "no such port: ab" in err
