"""Request bodies are bounded before a route buffers them, and a 422 quotes a rejected value
cut short (SPEC 3.4)."""

from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

from mcuscope import server as server_mod
from mcuscope.server import MAX_BODY_BYTES, VERSION_HEADER
from tests.support import Stack, mk_app


@pytest.fixture
def c(tmp_path):
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as client:
        yield client


def _raw(stack: Stack, request: bytes, timeout: float = 5.0) -> bytes:
    """Send `request` on a fresh connection and return whatever comes back."""
    port = int(stack.base_url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        try:
            s.sendall(request)
        except OSError:
            pass   # the server may answer and close before every byte is taken
        out = b""
        while True:
            try:
                chunk = s.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def test_a_declared_length_over_the_cap_is_refused_before_any_body_is_sent(stack) -> None:
    # Only headers go out: an answer at all proves the daemon did not wait to buffer 2 MB.
    head = (
        "POST /marker HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Content-Length: {2_000_000}\r\nConnection: close\r\n\r\n"
    ).encode()
    out = _raw(stack, head)
    assert out.startswith(b"HTTP/1.1 413"), out[:200]
    assert f"request body over {MAX_BODY_BYTES} bytes refused".encode() in out
    assert VERSION_HEADER.lower().encode() in out.lower()    # inside the header middleware
    assert b"x-frame-options: deny" in out.lower()


def test_a_chunked_body_over_the_cap_is_refused(stack) -> None:
    chunk = b"x" * 16384
    body = b"".join(b"%x\r\n%s\r\n" % (len(chunk), chunk) for _ in range(6)) + b"0\r\n\r\n"
    head = (
        b"POST /marker HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    out = _raw(stack, head + body)
    assert out.startswith(b"HTTP/1.1 413"), out[:200]
    assert b"request body over" in out


def test_a_chunked_body_under_the_cap_reaches_the_route_whole(stack) -> None:
    # Positive control for the replay: split across chunks, the route reads every byte.
    text = "m" * 4000
    payload = json.dumps({"text": text}).encode()
    parts = [payload[:10], payload[10:2000], payload[2000:]]
    body = b"".join(b"%x\r\n%s\r\n" % (len(p), p) for p in parts) + b"0\r\n\r\n"
    head = (
        b"POST /marker HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    out = _raw(stack, head + body)
    assert out.startswith(b"HTTP/1.1 200"), out[:300]
    line_id = json.loads(out.split(b"\r\n\r\n", 1)[1])["line_id"]
    import httpx
    rows = httpx.get(f"{stack.base_url}/lines", params={"chan": "marker"}).json()["lines"]
    assert [r["raw"] for r in rows if r["id"] == line_id] == [text]


def test_a_length_beside_chunked_does_not_route_around_the_cap(stack) -> None:
    # h11 frames a request carrying both as chunked and ignores the length, so the small
    # declared length must not let the body past uncounted.
    chunk = b"x" * 16384
    body = b"".join(b"%x\r\n%s\r\n" % (len(chunk), chunk) for _ in range(6)) + b"0\r\n\r\n"
    head = (
        b"POST /marker HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    out = _raw(stack, head + body)
    assert out.startswith(b"HTTP/1.1 413"), out[:200]
    assert b"request body over" in out


def test_a_small_body_beside_both_headers_still_reaches_the_route(stack) -> None:
    # Positive control for the test above: the pair itself is served when under the cap.
    payload = json.dumps({"text": "pair"}).encode()
    body = b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload)
    head = (
        b"POST /marker HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    out = _raw(stack, head + body)
    assert out.startswith(b"HTTP/1.1 200"), out[:300]


def test_a_body_at_the_cap_is_served_and_one_byte_more_is_not(c) -> None:
    # Padding inside the JSON keeps the body valid, so the route itself answers at the cap.
    base = json.dumps({"text": "x"}).encode()
    at_cap = base[:-1] + b" " * (MAX_BODY_BYTES - len(base)) + b"}"
    assert len(at_cap) == MAX_BODY_BYTES
    ok = c.post("/marker", content=at_cap, headers={"content-type": "application/json"})
    assert ok.status_code == 200, ok.text
    over = at_cap[:-1] + b" }"
    r = c.post("/marker", content=over, headers={"content-type": "application/json"})
    assert r.status_code == 413 and "request body over" in r.json()["error"]


def test_a_rejected_value_is_quoted_cut_short(c) -> None:
    r = c.post("/marker", json={"text": "A" * 5000})
    assert r.status_code == 422
    err = r.json()["error"]
    assert "'AAAA" in err and "... (5000 characters)" in err, err   # the value, not its repr
    assert len(err) < 300, len(err)


def test_a_rejected_non_string_counts_the_characters_quoted(c) -> None:
    value = list(range(100))
    r = c.post("/marker", json={"text": value})
    assert r.status_code == 422
    assert f"... ({len(repr(value))} characters)" in r.json()["error"], r.text


def test_a_short_rejected_value_is_quoted_whole(c) -> None:
    r = c.post("/marker", json={"text": 5})
    assert r.status_code == 422 and "(got 5)" in r.json()["error"], r.text


def test_the_guards_refuse_before_the_body_limit(tmp_path) -> None:
    # A cross-origin oversized request gets the guard's 403, not a 413: the order holds.
    with TestClient(mk_app(tmp_path), base_url="http://127.0.0.1") as c:
        r = c.post("/marker", content=b"x" * (MAX_BODY_BYTES + 1),
                   headers={"Origin": "http://evil.example"})
        assert r.status_code == 403, r.text


def test_the_limit_is_the_documented_one() -> None:
    assert server_mod.MAX_BODY_BYTES == 64 * 1024
