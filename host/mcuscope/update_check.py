"""Release check: is a newer MCUscope published on PyPI? (SPEC 3.6)

A background task in the daemon lifespan, deliberately kept at arm's length from
everything else: it never blocks startup, never raises into the loop, and a failure
(offline bench, proxy, PyPI down) is a debug log line and nothing more. The result is
surfaced by `GET /status` and shown by the web UI; nothing here writes to the capture.

Three properties matter more than the feature itself:

- **Off by one key.** `[update] check = false` (or `MCUSCOPE_UPDATE_CHECK=0` in the
  environment, which also keeps the test suite off the network) stops the request being
  made at all. A hardware debug tool that phones home on a private bench with no way to
  say no would be a defect.
- **At most one request a day, across restarts.** The result is cached under
  `platformdirs.user_cache_dir`, so a daemon restarted twenty times in an afternoon
  still makes one request. The cache is advisory: a missing or corrupt file just means
  the next check happens sooner.
- **No new dependency.** httpx is already the CLI's HTTP client, and version comparison
  is a few lines here rather than a `packaging` dependency (SPEC 1: minimise deps).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path

import httpx
import platformdirs

from . import __version__
from .config import APP_NAME, replace_atomic

log = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/mcuscope/json"
PROJECT_URL = "https://pypi.org/project/mcuscope/"
CHECK_INTERVAL_S = 24 * 3600.0   # at most one request a day, cache-enforced across restarts
RETRY_INTERVAL_S = 3600.0        # after a failed request: an offline bench must not spin
FIRST_DELAY_S = 10.0             # let the daemon finish starting (and short runs finish) first
DISABLED_SLEEP_S = 3600.0        # idle poll while disabled; a config change wakes it sooner
HTTP_TIMEOUT_S = 5.0
MAX_BODY_BYTES = 4 << 20         # the PyPI JSON grows with the release count; bound it anyway
ENV_ENABLE = "MCUSCOPE_UPDATE_CHECK"

# A plain numeric release: 1, 0.2, 0.2.3, 1.2.3.4. Anything with a suffix (rc1, b2, dev0,
# post1, +local) is deliberately not matched: a pre-release is not something to nag a user
# about, and "post" releases are not worth the parsing rules they would need.
#
# [0-9], not \d: the string comes from the PyPI response body, and Python's \d matches
# every Unicode decimal digit, which int() then converts - '٩.٩.٩' parsed as (9, 9, 9)
# and reported an update that does not exist.
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """Parse a plain numeric version into a comparable tuple, or None if it is not one."""
    if not text:
        return None
    cleaned = text.strip()
    if not _VERSION_RE.fullmatch(cleaned) or len(cleaned) > 64:
        return None
    return tuple(int(part) for part in cleaned.split("."))


def is_newer(latest: str | None, current: str = __version__) -> bool:
    """True if `latest` is a plain release strictly newer than `current`.

    Unparsable on either side answers False: a locally built dev version, or a PyPI
    pre-release, must not produce an "update available" the user cannot act on.
    """
    new = parse_version(latest)
    have = parse_version(current)
    if new is None or have is None:
        return False
    width = max(len(new), len(have))
    return new + (0,) * (width - len(new)) > have + (0,) * (width - len(have))


def cache_path() -> Path:
    """Where the last check result is remembered, cross-platform via platformdirs."""
    return Path(platformdirs.user_cache_dir(APP_NAME)) / "update.json"


def env_allows_check() -> bool:
    """Environment veto: `MCUSCOPE_UPDATE_CHECK=0` disables the check regardless of config.

    Unset means "follow the config file"; set, only 1/true/yes/on allow the request and
    every other value vetoes it. This is the switch CI, the test suite and an air-gapped
    install use, since it needs no config file to exist.
    """
    raw = os.environ.get(ENV_ENABLE)
    if raw is None:
        return True
    value = raw.strip().lower()
    if not value or value in {"1", "true", "yes", "on"}:   # empty reads as unset
        return True
    # Anything else vetoes, rather than only the four spellings of "off": for the one
    # switch whose whole point is not phoning home from a private bench, resolving
    # `=disable`, `=none` or a typo to "make the request" is the wrong way to be wrong.
    if value not in {"0", "false", "no", "off"}:
        log.warning("%s=%r is not recognised; treating it as a veto on the update check",
                    ENV_ENABLE, raw)
    return False


class UpdateChecker:
    """Owns the cached result and the background polling task."""

    def __init__(self, enabled: bool = True, current: str = __version__,
                 path: Path | None = None, url: str = PYPI_URL,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.enabled = enabled and env_allows_check()
        self.current = current
        self.url = url
        # Only the tests pass a transport (httpx.MockTransport): the alternative is a suite
        # that either talks to PyPI or monkeypatches httpx internals.
        self._transport = transport
        self._path = path if path is not None else cache_path()
        self._wake = asyncio.Event()
        self.latest: str | None = None
        self.checked_at: float | None = None
        self._load_cache()

    # -- state ------------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Apply a config change live; enabling checks on the cache's normal schedule."""
        enabled = enabled and env_allows_check()
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self._wake.set()

    def status(self) -> dict | None:
        """The `update` field of GET /status, or None when there is nothing to report.

        A disabled checker reports nothing even with a warm cache from an earlier run:
        switching the check off means "stop telling me about releases", not "keep showing
        me yesterday's answer". The cache is still loaded, because it is what keeps a
        re-enabled check to one request a day.
        """
        if not self.enabled or self.checked_at is None:
            return None
        return {
            "latest": self.latest,
            "available": is_newer(self.latest, self.current),
            "checked_at": self.checked_at,
            "url": PROJECT_URL,
        }

    # -- cache ------------------------------------------------------------------------

    def _load_cache(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            latest = data["latest"]
            checked_at = float(data["checked_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return   # missing or corrupt: simply means the next check happens now
        if not math.isfinite(checked_at):
            # float() accepts "NaN" and "Infinity". NaN is the sticky one: min(nan, now)
            # is nan, so it survives every save, /status reports checked_at null beside
            # available true, and _delay() falls back to FIRST_DELAY_S - the once-a-day
            # guarantee (SPEC 3.6) turned into a 10 s poll.
            return
        # A timestamp in the future (clock change, a copied cache) would postpone checks
        # indefinitely, so treat it as "checked now" rather than trusting it.
        # The timestamp is honoured whatever `latest` turned out to be: a check that found
        # no usable release still writes {"latest": null}, and refusing to read that back
        # made every restart re-ask PyPI - the once-a-day guarantee (SPEC 3.6) undone in
        # exactly the case this cache exists for. Only `latest` itself is type-gated.
        self.checked_at = min(checked_at, time.time())
        if isinstance(latest, str) and len(latest) <= 64:
            self.latest = latest

    def _save_cache(self) -> None:
        payload = json.dumps({"latest": self.latest, "checked_at": self.checked_at})
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, not text: no newline translation, so the file is identical on both
            # platforms and nothing here depends on the host's line endings.
            tmp.write_bytes(payload.encode("utf-8"))
            replace_atomic(tmp, self._path)
        except OSError as exc:
            log.debug("update check: could not write %s: %s", self._path, exc)
            with contextlib.suppress(OSError):
                tmp.unlink()

    # -- polling ----------------------------------------------------------------------

    def _delay(self) -> float:
        if not self.enabled:
            return DISABLED_SLEEP_S
        if self.checked_at is None:
            return FIRST_DELAY_S
        return max(FIRST_DELAY_S, self.checked_at + CHECK_INTERVAL_S - time.time())

    async def run(self) -> None:
        """Poll forever. Cancelled by the lifespan; every other exception stays inside."""
        while True:
            woken = await self._sleep(self._delay())
            if woken or not self.enabled:
                continue      # a config change: re-evaluate rather than check immediately
            ok = await self.check_once()
            if not ok:
                # Failure does not update checked_at (so a working link checks promptly),
                # so hold off explicitly instead of retrying at the top of the loop.
                await self._sleep(RETRY_INTERVAL_S)

    async def _sleep(self, delay: float) -> bool:
        """Sleep, returning True if woken early by a config change.

        The event is cleared only after it fires, never before the wait: clearing first
        would swallow a toggle that arrived while a request was in flight, leaving the
        checker asleep for an hour after being switched on.
        """
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.1, delay))
        except TimeoutError:
            return False
        self._wake.clear()
        return True

    async def check_once(self) -> bool:
        """One request. Returns True if a version was learned; never raises."""
        try:
            headers = {
                "Accept": "application/json",
                "User-Agent": f"mcuscope/{self.current} (update check)",
            }
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_S, follow_redirects=True, transport=self._transport,
            ) as client:
                # Streamed, so MAX_BODY_BYTES actually bounds what is read: checking
                # len(resp.content) after a plain get() only measures a body already
                # buffered in full, which is no limit at all.
                async with client.stream("GET", self.url, headers=headers) as resp:
                    resp.raise_for_status()
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BODY_BYTES:
                            log.debug("update check: response over %d bytes", MAX_BODY_BYTES)
                            return False
                latest = json.loads(bytes(body)).get("info", {}).get("version")
        except Exception as exc:
            # Offline bench, proxy, DNS, PyPI outage, a body that is not the JSON we expect:
            # none of it is the user's problem, and none of it may reach the event loop.
            log.debug("update check failed: %s", exc)
            return False
        if not isinstance(latest, str) or parse_version(latest) is None:
            log.debug("update check: ignoring version %r", latest)
            # Still a successful round trip: record the time so a pre-release-only project
            # is not re-fetched every hour.
            self.checked_at = time.time()
            await asyncio.to_thread(self._save_cache)
            return True
        self.latest = latest.strip()
        self.checked_at = time.time()
        # Off the loop: the write is mkdir + write + os.replace, and replace_atomic can
        # sleep up to 0.9 s retrying a Windows sharing violation. On the loop that stalls
        # every WS feed and serial callback for the duration.
        await asyncio.to_thread(self._save_cache)
        if is_newer(self.latest, self.current):
            log.info("mcuscope %s is available (running %s)", self.latest, self.current)
        return True
