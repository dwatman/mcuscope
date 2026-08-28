"""Release check: is a newer MCUscope published on PyPI? (SPEC 3.6)

Driven by demand, not by a timer: `maybe_check()` is called once at daemon startup and
again on every `GET /status`, and `_due()` decides whether that becomes a request. There
is no polling task, because the cache below is what enforces the daily rate - a timer
would only have been asking the cache the same question on a schedule. Each check is
detached, so it never blocks startup or a status response, never raises into the loop,
and a failure (offline bench, proxy, PyPI down) is a debug log line and nothing more.

The result is surfaced by `GET /status`, which both consumers reach: `mcu status` prints
a line and the web UI shows a badge. Nothing here writes to the capture.

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
HTTP_TIMEOUT_S = 5.0
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


def env_override() -> bool | None:
    """The environment's answer, or None when it has none.

    `MCUSCOPE_UPDATE_CHECK` wins over the config file in both directions (SPEC 3.6):
    1/true/yes/on force the check on, every other value vetoes it, and unset or empty
    means "follow the config file". This is the switch CI, the test suite and an
    air-gapped install use, since it needs no config file to exist.
    """
    raw = os.environ.get(ENV_ENABLE)
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:                                          # empty reads as unset
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    # Anything else vetoes, rather than only the four spellings of "off": for the one
    # switch whose whole point is not phoning home from a private bench, resolving
    # `=disable`, `=none` or a typo to "make the request" is the wrong way to be wrong.
    if value not in {"0", "false", "no", "off"}:
        log.warning("%s=%r is not recognised; treating it as a veto on the update check",
                    ENV_ENABLE, raw)
    return False


def resolve_enabled(config_says: bool) -> bool:
    """Combine the config file with the environment, which wins either way."""
    override = env_override()
    return config_says if override is None else override


class UpdateChecker:
    """Owns the cached result and whichever check is currently in flight."""

    def __init__(self, enabled: bool = True, current: str = __version__,
                 path: Path | None = None, url: str = PYPI_URL,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.enabled = resolve_enabled(enabled)
        self.current = current
        self.url = url
        # Only the tests pass a transport (httpx.MockTransport): the alternative is a suite
        # that either talks to PyPI or monkeypatches httpx internals.
        self._transport = transport
        self._path = path if path is not None else cache_path()
        self.latest: str | None = None
        self.checked_at: float | None = None
        # One in-flight check at a time, and the hold after a failed one. A failure does
        # not move checked_at (so a link that comes back checks promptly), so without the
        # hold every /status while offline would start another request.
        self._task: asyncio.Task | None = None
        self._retry_after = 0.0
        self._load_cache()

    # -- state ------------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Apply a config change; the next `maybe_check` acts on it.

        The environment is re-applied here, so a config file that says yes still cannot
        overrule MCUSCOPE_UPDATE_CHECK=0, nor one that says no beat MCUSCOPE_UPDATE_CHECK=1.
        Enabling checks on the cache's normal schedule: a warm cache is not re-fetched
        just because the switch was flipped.
        """
        self.enabled = resolve_enabled(enabled)

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
            # is nan, so it survives every save and /status reports checked_at null beside
            # available true. Every comparison against nan is False, so `_due()` answers
            # False forever: one poisoned cache file and the release check (SPEC 3.6)
            # silently never runs again on that machine.
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
        # Pid-suffixed: two daemons for one user share user_cache_dir, so a fixed ".tmp"
        # name let one act on the other's half-written bytes.
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
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

    def _due(self) -> bool:
        """Is a request owed right now? False whenever the check is switched off.

        A cached timestamp that is not a real one can never make this True on a fast
        schedule: an unusable value is rejected at load (see _load_cache), leaving
        checked_at None, which is one check and then the normal daily cadence.
        """
        if not self.enabled or time.time() < self._retry_after:
            return False
        if self.checked_at is None:
            return True
        return time.time() >= self.checked_at + CHECK_INTERVAL_S

    def maybe_check(self) -> None:
        """Start a check if one is owed. Returns at once and never raises.

        Safe to call at any rate: `_due()` is the once-a-day guarantee (module docstring).
        """
        if not self._due() or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.get_running_loop().create_task(self._check_and_hold())

    async def _check_and_hold(self) -> None:
        if not await self.check_once():
            self._retry_after = time.time() + RETRY_INTERVAL_S

    async def aclose(self) -> None:
        """Cancel any in-flight check. Called by the lifespan on shutdown."""
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

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
                resp = await client.get(self.url, headers=headers)
                resp.raise_for_status()
                latest = resp.json().get("info", {}).get("version")
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
