"""The text export tags the daemon's own rows (port "") `[-]` beside the boards' `[port]`
(SPEC 3.4), through the renderer `mcu` shares."""

from __future__ import annotations

import time

from tests.support import on_loop


def test_a_text_export_across_two_ports_tags_the_daemons_rows_with_a_dash(client) -> None:
    store = client.app.state.store
    for ts, port, raw in ((1.0, "a", "from a"), (2.0, "", "daemon note"), (3.0, "b", "from b")):
        on_loop(client, store.add_line(ts=time.time() + ts, port=port, dir="rx",
                                       chan="event", seq=None, raw=raw))
    resp = client.get("/lines/export", params={"format": "text"})
    assert resp.status_code == 200, resp.text
    rows = [ln.split(" ", 1)[1] for ln in resp.text.splitlines() if "event|" in ln]
    assert rows == ["[a]  event| from a", "[-]  event| daemon note", "[b]  event| from b"]
