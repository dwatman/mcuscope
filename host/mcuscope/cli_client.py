"""The `mcu` CLI's HTTP side: Settings, the Client wrapper, and the exit-code map.

The SPEC 4 mapping from transport failures to exit codes is stated once here
(_daemon_errors) and every request policy - request, probe, download, stream_text -
routes through it. Commands and follow loops live in cli.py.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from .cli_output import die

if TYPE_CHECKING:
    import httpx

# httpx is imported inside the functions that use it: it costs about 40 ms of a ~190 ms
# CLI start, which `--help`, `--version` and `ai-guide` should not pay.

DEFAULT_URL = "http://127.0.0.1:8558"


@dataclass
class Settings:
    url: str
    json_out: bool
    port: str | None
    token: str | None = None

    def headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


def error_text(resp: httpx.Response) -> str:
    """The daemon's `{"error": ...}` envelope, or the raw body when it is not one."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text
    # A bare JSON list or string is a valid body and has no .get; only a dict carries the
    # envelope, and anything else is reported verbatim rather than as an AttributeError.
    return body.get("error", resp.text) if isinstance(body, dict) else resp.text


def start_hint(url: str) -> str:
    """How to get a daemon, appended to "unreachable" when the url is the default one.

    A custom --url names a daemon the user set up elsewhere; telling them to start a
    local one would be wrong advice.
    """
    if url.rstrip("/") != DEFAULT_URL:
        return ""
    return "; start it with 'mcu daemon start' (or run 'mcuscoped')"


def die_bad_url(url: str, exc: Exception) -> NoReturn:
    """A url no daemon can be reached at is exit 3 (SPEC 4), wherever it is noticed.

    Three sites parse or hand over a url: the request wrapper, the CAN follow poll (which
    must not route its other failures through that wrapper, since it retries them) and the
    pid-file host/port split. They answered with three spellings of the same sentence.
    """
    die(f"bad daemon url {url!r}: {exc}", 3)


@contextlib.contextmanager
def _daemon_errors(url: str, timeout_code: int = 2):
    """Map the transport failures of one daemon call onto the SPEC 4 exit codes.

    This mapping IS the exit-code contract, so it is stated once. Three copies of it used
    to live in Client alone, and they had already drifted: only `request` knew that SPEC 4
    forbids `mcu assert` exiting 2, so `timeout_code` is the exception made visible to
    every call rather than to one of them.
    """
    import httpx

    try:
        yield
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        die(f"daemon unreachable at {url}: {exc}{start_hint(url)}", 3)
    except httpx.TimeoutException as exc:
        die(f"request timed out: {exc}", timeout_code)
    except httpx.InvalidURL as exc:
        # Not an httpx.HTTPError subclass, so this once escaped as a raw traceback while
        # every neighbouring bad-url form was handled.
        die_bad_url(url, exc)
    except httpx.HTTPError as exc:
        die(f"daemon unreachable at {url}: {exc}{start_hint(url)}", 3)
    except ValueError as exc:
        # Not every failure of a request is an HTTPError: httpx raises UnicodeEncodeError
        # (a ValueError) while encoding a header or a query it cannot put on the wire, and
        # that escaped every handler as a traceback and a crash log. The sibling policy
        # (Client.probe) already catches ValueError; this is the mapped half.
        die(f"cannot send request to {url}: {exc}", 1)


class Client:
    def __init__(self, s: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.s = s
        # Substituted, not inspected: the tests supply a transport the way UpdateChecker
        # takes one, instead of standing up a threaded HTTP server to control a body. One
        # way in, so a whole-command run patches `open` rather than a second global.
        self._transport = transport

    def open(self) -> httpx.Client:
        """A fresh httpx client on this invocation's transport. Use as a context manager."""
        import httpx

        return httpx.Client(transport=self._transport)

    def request(
        self, method: str, path: str, timeout: float = 30.0,
        timeout_code: int = 2, **kw: Any,
    ) -> httpx.Response:
        """Issue a request, mapping transport failures onto the SPEC 4 exit codes.

        `timeout_code` exists for `mcu assert`, which SPEC 4 says never exits 2: a transport
        timeout there has to surface as an error, not as the timeout code.
        """
        with _daemon_errors(self.s.url, timeout_code):
            with self.open() as http:
                return http.request(
                    method, self.s.url + path, timeout=timeout,
                    headers=self.s.headers(), **kw
                )
        raise AssertionError("unreachable")  # for type-checkers; die() always raises

    def probe(self, method: str, path: str, timeout: float = 2.0) -> Any:
        """A call to a daemon that is allowed to be absent: the body, or None on failure.

        The third policy beside `request` (map onto an exit code) and `download`. The
        `mcu daemon` subcommands question a process that may not be there, so a refused
        connection, an unparseable url and a non-JSON answer all mean "not running"
        rather than an error. InvalidURL is named explicitly because it is not an
        HTTPError subclass, and once escaped as a traceback where every other unusable
        url counted as absent.
        """
        import httpx

        try:
            with self.open() as http:
                return http.request(
                    method, self.s.url + path, timeout=timeout, headers=self.s.headers()
                ).json()
        except (httpx.InvalidURL, httpx.HTTPError, json.JSONDecodeError, ValueError):
            return None

    def fail(self, resp: httpx.Response) -> NoReturn:
        """Exit 1 with the daemon's error. An ambiguous port lists the aliases to pick from."""
        msg = error_text(resp)
        if msg.startswith("port is ambiguous"):
            body = self.probe("GET", "/ports")
            ports = body.get("ports") if isinstance(body, dict) else None
            aliases = [pt["alias"] for pt in ports or [] if isinstance(pt, dict) and "alias" in pt]
            if aliases:
                msg += f" with -p, one of: {', '.join(aliases)}"
        die(f"error: {msg}", 1)
        raise AssertionError("unreachable")  # for type-checkers; die() always raises

    def json_or_die(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            self.fail(resp)
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # A proxy, a captive portal, or the wrong port answering 200 with non-JSON.
            # Report it as an error with an exit code, not as a JSONDecodeError traceback.
            die(f"malformed response from {self.s.url}: {exc}", 1)

    def get(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("GET", path, **kw))

    def post(self, path: str, body: dict[str, Any], **kw: Any) -> Any:
        return self.json_or_die(self.request("POST", path, json=body, **kw))

    def put(self, path: str, body: dict[str, Any], **kw: Any) -> Any:
        return self.json_or_die(self.request("PUT", path, json=body, **kw))

    def delete(self, path: str, **kw: Any) -> Any:
        return self.json_or_die(self.request("DELETE", path, **kw))

    def download(self, path: str, out_file: str, timeout: float = 300.0, **kw: Any) -> int:
        """Stream a binary response to a file. Returns bytes written.

        Streamed rather than buffered because the thing being downloaded is a database:
        a long run's export can be larger than it is polite to hold in memory twice.
        """
        started = ok = False
        try:
            with _daemon_errors(self.s.url), self.open() as http, http.stream(
                "GET", self.s.url + path, timeout=timeout, headers=self.s.headers(), **kw
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self.fail(resp)
                written = 0
                with open(out_file, "wb") as fh:
                    started = True
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
                        written += len(chunk)
                ok = True
                return written
        except OSError as exc:
            die(f"cannot write {out_file}: {exc}", 1)
        finally:
            if started and not ok:
                # A stream that dies mid-transfer leaves a truncated .db sitting where the
                # user asked for an export, indistinguishable from a whole one. The error
                # is reported by the handlers above; the wreckage goes here.
                with contextlib.suppress(OSError):
                    os.remove(out_file)
        raise AssertionError("unreachable")  # for type-checkers; die() always raises

    def stream_text(
        self, path: str, sink: Callable[[str], None], what: str = "output",
        timeout: float = 300.0, **kw: Any,
    ) -> None:
        """Stream a text response through `sink`, chunk by chunk.

        `/plot/export` is the one endpoint that can answer with a very large body (a long
        run's channel history), so it is consumed incrementally like a session export
        rather than materialised whole with `resp.text`. `what` names the destination in
        the write-error message.
        """
        try:
            with _daemon_errors(self.s.url), self.open() as http, http.stream(
                "GET", self.s.url + path, timeout=timeout, headers=self.s.headers(), **kw
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self.fail(resp)
                for chunk in resp.iter_text():
                    sink(chunk)
        except BrokenPipeError:
            raise                        # handled in cli.main(): the reader closed the pipe
        except OSError as exc:
            die(f"cannot write {what}: {exc}", 1)
