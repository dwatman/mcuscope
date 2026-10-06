"""A `can dump -f` poll refused after the first request reads like any other refusal
(SPEC 4 `kind`): it routes through the same classifier as the follow's first request."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from mcuscope import cli
from tests.support import UNREACHABLE, canned


@pytest.mark.parametrize(("status", "msg", "kind", "text"), [
    (401, "invalid or missing token", "daemon_error", "invalid or missing token"),
    (409, "port b is busy", "daemon_error", "port b is busy"),
    (422, "since_id: must be at least 0", "usage", "--since-id: must be at least 0"),
])
def test_a_later_poll_refusal_has_the_first_requests_kind(
    monkeypatch, capsys, status, msg, kind, text,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    polls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/can/frames":
            if "since_id" in request.url.params:   # a poll; the backfill and prime pass
                polls.append(request)
                return httpx.Response(status, json={"error": msg})
            return httpx.Response(200, json={"frames": []})
        return httpx.Response(200, json={})

    canned(monkeypatch, handler)
    rc = cli.main(["--json", "can", "dump", "-f", *UNREACHABLE])
    obj = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert len(polls) == 1, "the first poll's refusal ends the follow"
    assert rc == obj["exit_code"] == 1, obj
    assert obj["kind"] == kind, obj
    assert obj["error"] == text, obj
