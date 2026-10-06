"""A board's read carries the daemon's own rows (port ''), a verdict's never does (OP-7,
REVIEW class 84): a live `/wait` scoped to a port and a channel must not be satisfied by a
marker the host wrote with no port."""

from __future__ import annotations

from tests.support import Stack
from tests.test_server_live_verdicts import _http, _post_later


def test_a_live_wait_on_a_port_ignores_a_portless_marker(stack: Stack) -> None:
    wait = {"port": stack.alias, "chan": "marker", "match": "NOPORT-MARK", "timeout_ms": 1200}
    with _http(stack) as h:
        t = _post_later(stack, "/marker", {"text": "NOPORT-MARK"})
        body = h.post("/wait", json=wait).json()
        t.join()
        assert body["status"] == "timeout", body
        # Positive control: the same marker on the port is seen.
        t = _post_later(stack, "/marker", {"port": stack.alias, "text": "NOPORT-MARK"})
        body = h.post("/wait", json=wait).json()
        t.join()
        assert body["status"] == "match", body
