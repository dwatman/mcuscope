"""`/wait` with `repeat_ms`: resend a raw line until the match (SPEC 3.4).

The feature exists to catch a bootloader's autoboot window, so the interesting paths are
the ones where the write cannot succeed: the call is normally made before the target is
powered. Those get most of the coverage here, along with the refusals and the promise
that no repeat task outlives its response.
"""

from __future__ import annotations

import re
import threading
import time

import httpx

from tests.support import Stack

NEEDLE = "ZZNEEDLE"
SPRAY = "ZZSPRAY"
NEVER = "ZZNEVERMATCHES"


def client(stack: Stack) -> httpx.Client:
    return httpx.Client(base_url=stack.base_url, timeout=20.0)


def _tx_rows(c: httpx.Client, match: str) -> list[dict]:
    return c.get("/lines", params={"chan": "cmd", "match": match, "limit": 100}).json()["lines"]


class Stimulus:
    """A matchable line posted every 50 ms from `delay_s` until stopped.

    Re-armed rather than fired once (registry class 21): a single broadcast into the
    live feed can be shed, and the wait would then honestly answer timeout.
    """

    def __init__(self, stack: Stack, text: str, delay_s: float) -> None:
        self._stack = stack
        self._text = text
        self._delay = delay_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        if self._stop.wait(self._delay):
            return
        while not self._stop.is_set():
            try:
                httpx.post(
                    self._stack.base_url + "/marker",
                    json={"port": self._stack.alias, "text": self._text},
                    timeout=5.0,
                )
            except httpx.HTTPError:
                return
            self._stop.wait(0.05)

    def __enter__(self) -> Stimulus:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)


# -- refusals -------------------------------------------------------------------------


def test_repeat_without_send_is_refused(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={"match": NEVER, "timeout_ms": 500, "repeat_ms": 50})
    assert r.status_code == 400
    assert r.json()["error"] == "repeat_ms requires send"


def test_repeat_with_a_command_send_is_refused(stack: Stack) -> None:
    # A monitor command carries a seq; spraying it would reuse or burn seqs per write.
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 500, "send": "ping",
            "send_mode": "cmd", "repeat_ms": 50,
        })
    assert r.status_code == 400
    assert r.json()["error"] == 'repeat_ms requires send_mode "raw"'


def test_repeat_below_the_floor_is_refused(stack: Stack) -> None:
    with client(stack) as c:
        for bad in (9, 0, -1):
            r = c.post("/wait", json={
                "match": NEVER, "timeout_ms": 500, "send": SPRAY,
                "send_mode": "raw", "repeat_ms": bad,
            })
            assert r.status_code == 400, bad
            assert r.json()["error"] == "repeat_ms must be between 10 and timeout_ms (500)", bad


def test_repeat_above_the_timeout_is_refused(stack: Stack) -> None:
    # The ceiling is the window itself: a period longer than it writes exactly once.
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 200, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 201,
        })
    assert r.status_code == 400
    assert r.json()["error"] == "repeat_ms must be between 10 and timeout_ms (200)"
    # And the boundary itself is accepted, so the message is not off by one.
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 200, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 200,
        })
    assert r.status_code == 200


# -- matching -------------------------------------------------------------------------


def test_match_on_the_first_tick_sends_once(stack: Stack) -> None:
    # The stored tx row of the very first write is the match, and the period is longer
    # than the time it can take to find it, so a second write is impossible.
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": SPRAY, "timeout_ms": 3000, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 1000,
        }).json()
    assert r["status"] == "match"
    assert r["sends"] == 1
    assert r["send_failures"] == 0
    assert r["line"]["raw"] == SPRAY


def test_a_later_match_keeps_the_writes_coming(stack: Stack) -> None:
    with client(stack) as c, Stimulus(stack, NEEDLE, delay_s=0.4):
        r = c.post("/wait", json={
            "match": NEEDLE, "timeout_ms": 8000, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 20,
        }).json()
    assert r["status"] == "match"
    assert NEEDLE in r["line"]["raw"]
    # 400 ms of 20 ms ticks; even a runner making a twentieth of that progress writes twice.
    assert r["sends"] >= 2, r


def test_only_the_first_write_is_stored(stack: Stack) -> None:
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 600, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 20,
        }).json()
        assert r["status"] == "timeout"
        assert r["sends"] > 1, r
        # Many writes, one row: 20 a second for 30 s would bury the capture.
        assert len(_tx_rows(c, SPRAY)) == 1


# -- an unwritable port ---------------------------------------------------------------


def test_a_disconnected_port_is_counted_not_fatal(stack: Stack) -> None:
    with client(stack) as c:
        assert c.post(f"/ports/{stack.alias}/disconnect").status_code == 200
        assert stack.wait_connected(False)
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 600, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 20,
        }).json()
        assert r["status"] == "timeout"
        assert r["sends"] == 0, r
        assert r["send_failures"] >= 2, r
        # Nothing reached the wire, so nothing is claimed to have.
        assert _tx_rows(c, SPRAY) == []


def test_the_match_lands_once_the_port_connects_mid_wait(stack: Stack) -> None:
    """The bootloader case: the wait is started before the target is reachable."""
    result: dict = {}
    with client(stack) as c:
        assert c.post(f"/ports/{stack.alias}/disconnect").status_code == 200
        assert stack.wait_connected(False)

        def run() -> None:
            # Match the tx row of the first write that succeeds: it cannot exist until the
            # port is back, so the match itself is the evidence that the loop resumed
            # writing after a streak of failures.
            result.update(c.post("/wait", json={
                "match": SPRAY, "timeout_ms": 15000, "send": SPRAY,
                "send_mode": "raw", "repeat_ms": 20,
            }).json())

        t = threading.Thread(target=run, daemon=True)
        t.start()
        try:
            time.sleep(0.3)          # let the loop fail a few writes first
            assert c.post(f"/ports/{stack.alias}/reconnect").status_code == 200
        finally:
            t.join(timeout=20.0)
        assert not t.is_alive()

    assert result["status"] == "match", result
    assert result["line"]["raw"] == SPRAY, result
    assert result["send_failures"] >= 1, result
    assert result["sends"] >= 1, result
    with client(stack) as c:
        assert len(_tx_rows(c, SPRAY)) == 1


# -- lifetime -------------------------------------------------------------------------


def test_no_repeat_task_outlives_the_response(stack: Stack) -> None:
    port = stack.app.state.ports.get(stack.alias)
    with client(stack) as c:
        r = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 400, "send": SPRAY,
            "send_mode": "raw", "repeat_ms": 20,
        }).json()
    assert r["status"] == "timeout"
    assert r["sends"] > 1, r
    settled = port.lines_tx
    time.sleep(0.5)   # 25 periods: a surviving repeater would be plainly visible
    assert port.lines_tx == settled


# -- CLI ------------------------------------------------------------------------------


def test_cli_refuses_a_repeat_with_nothing_to_send(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    r = run_mcu(stack, "wait", "--match", NEVER, "--repeat-ms", "50", "--timeout", "500")
    assert r.returncode == 1
    with client(stack) as c:
        daemon = c.post("/wait", json={
            "match": NEVER, "timeout_ms": 500, "repeat_ms": 50,
        }).json()["error"]
    # The same refusal in the same words, wherever it was reached.
    assert daemon in r.stderr


def test_cli_refuses_a_period_outside_the_window(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    r = run_mcu(
        stack, "wait", "--match", NEVER, "--send", SPRAY, "--repeat-ms", "5",
        "--timeout", "500",
    )
    assert r.returncode == 1
    assert "repeat_ms must be between 10 and timeout_ms (500)" in r.stderr


def test_cli_repeat_implies_raw(stack: Stack) -> None:
    # No --raw, and the stored line is verbatim: routed as a command it would carry a seq.
    from tests.test_cli import run_mcu

    r = run_mcu(
        stack, "wait", "--send", SPRAY, "--repeat-ms", "50", "--match", SPRAY,
        "--timeout", "3000",
    )
    assert r.returncode == 0, r.stderr
    with client(stack) as c:
        assert [row["raw"] for row in _tx_rows(c, SPRAY)] == [SPRAY]


def test_cli_sends_an_empty_line_and_reports_the_count(stack: Stack) -> None:
    """`--send ""` is a line to send, not an absent one: it is the bootloader keystroke."""
    from tests.test_cli import run_mcu

    r = run_mcu(
        stack, "wait", "--send", "", "--repeat-ms", "50", "--match", "^!can",
        "--timeout", "5000",
    )
    assert r.returncode == 0, r.stderr
    assert re.search(r"sent \d+ times, \d+ writes failed", r.stderr), r.stderr
    with client(stack) as c:
        rows = c.get("/lines", params={"chan": "cmd", "limit": 100}).json()["lines"]
    assert any(row["raw"] == "" and row["dir"] == "tx" for row in rows), rows


def test_cli_reports_the_count_on_a_timeout_too(stack: Stack) -> None:
    from tests.test_cli import run_mcu

    r = run_mcu(
        stack, "wait", "--send", SPRAY, "--repeat-ms", "20", "--match", NEVER,
        "--timeout", "400",
    )
    assert r.returncode == 2
    assert re.search(r"sent \d+ times, \d+ writes failed", r.stderr), r.stderr
    assert "timeout" in r.stderr


# -- the plain wait is unchanged ------------------------------------------------------


def test_without_repeat_the_counts_are_still_reported(stack: Stack) -> None:
    with client(stack) as c:
        sent = c.post("/wait", json={
            "match": SPRAY, "timeout_ms": 2000, "send": SPRAY, "send_mode": "raw",
        }).json()
        quiet = c.post("/wait", json={"match": NEVER, "timeout_ms": 200}).json()
    assert (sent["sends"], sent["send_failures"]) == (1, 0)
    assert (quiet["sends"], quiet["send_failures"]) == (0, 0)
