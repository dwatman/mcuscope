"""Release check (SPEC 3.6): version comparison, caching, the opt-out, and /status.

Nothing here touches the network: every request goes through an httpx.MockTransport, and
conftest sets MCUSCOPE_UPDATE_CHECK=0 so no app started by the rest of the suite makes a
real request either.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from mcuscope import update_check as uc


@pytest.fixture(autouse=True)
def _no_env_veto(monkeypatch):
    # conftest sets MCUSCOPE_UPDATE_CHECK=0 to keep the rest of the suite off the network.
    # These tests drive the checker directly through a mock transport, so they need the
    # environment out of the way; the two that test the veto itself set it back.
    monkeypatch.delenv(uc.ENV_ENABLE, raising=False)


def mock_transport(version: str | None = "9.9.9", status: int = 200,
                   body: object | None = None, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if body is not None:
            return httpx.Response(status, json=body)
        return httpx.Response(status, json={"info": {"version": version}})
    return httpx.MockTransport(handler)


def checker(tmp_path, **kw) -> uc.UpdateChecker:
    kw.setdefault("path", tmp_path / "update.json")
    kw.setdefault("current", "0.1.0")
    return uc.UpdateChecker(**kw)


# -- version comparison ------------------------------------------------------------------


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("0.2.0", "0.1.1", True),
        ("0.1.2", "0.1.1", True),
        ("1.0", "0.9.9", True),
        ("0.1.1", "0.1.1", False),
        ("0.1.0", "0.1.1", False),
        ("0.1", "0.1.0", False),      # equal once zero-padded
        ("0.1.0.1", "0.1", True),     # differing lengths compare on the extra component
        ("10.0.0", "9.99.99", True),  # numeric, not lexicographic
        # Pre-releases and junk are never "newer": the user cannot act on a notice for a
        # version that pip will not install by default.
        ("0.2.0rc1", "0.1.1", False),
        ("0.2.0.dev3", "0.1.1", False),
        ("v0.2.0", "0.1.1", False),
        ("", "0.1.1", False),
        (None, "0.1.1", False),
        ("0.2.0", "0.1.1.dev0+local", False),   # a dev install is never nagged
    ],
)
def test_is_newer(latest, current, expected) -> None:
    assert uc.is_newer(latest, current) is expected


def test_parse_version_rejects_absurd_input() -> None:
    assert uc.parse_version("1." * 40 + "1") is None   # over the length cap
    assert uc.parse_version("1..2") is None
    assert uc.parse_version(" 0.2.0 ") == (0, 2, 0)    # surrounding space is fine


def test_parse_version_takes_ascii_digits_only() -> None:
    """The string comes from the PyPI response body, so the grammar is [0-9], not `\\d`.

    Python's `\\d` is Unicode-wide and int() converts what it matches, so '٩.٩.٩'
    (U+0669) parsed as (9, 9, 9) and reported an update that does not exist.
    """
    assert uc.parse_version("٩.٩.٩") is None
    assert uc.parse_version("٣") is None
    assert uc.is_newer("٩.٩.٩", "0.1.1") is False


# -- the check itself --------------------------------------------------------------------


def test_check_once_records_and_caches(tmp_path) -> None:
    async def run() -> None:
        c = checker(tmp_path, transport=mock_transport("0.4.2"))
        assert await c.check_once() is True
        assert c.latest == "0.4.2"
        assert c.status()["available"] is True
        assert c.status()["latest"] == "0.4.2"

        cached = json.loads((tmp_path / "update.json").read_text(encoding="utf-8"))
        assert cached["latest"] == "0.4.2"
        assert cached["checked_at"] == pytest.approx(time.time(), abs=30)

        # A fresh checker over the same cache knows the answer without a request, and is
        # not due for another one: this is what keeps twenty daemon restarts to one request.
        calls: list = []
        again = checker(tmp_path, transport=mock_transport("0.4.2", calls=calls))
        assert again.latest == "0.4.2"
        assert again._due() is False
        assert calls == []

    asyncio.run(run())


def test_status_is_none_before_any_check(tmp_path) -> None:
    assert checker(tmp_path).status() is None


def test_disabled_checker_reports_nothing_despite_a_warm_cache(tmp_path) -> None:
    # Switching the check off means "stop telling me", so a result cached by an earlier
    # run must not keep surfacing on /status (and in the UI badge).
    (tmp_path / "update.json").write_text(
        json.dumps({"latest": "99.0.0", "checked_at": time.time()}), encoding="utf-8", newline="\n",
    )
    c = checker(tmp_path, enabled=False)
    assert c.latest == "99.0.0"     # still loaded: it is what schedules the next request
    assert c.status() is None
    c.set_enabled(True)
    assert c.status()["latest"] == "99.0.0"


def test_no_update_when_running_the_newest(tmp_path) -> None:
    async def run() -> None:
        c = checker(tmp_path, current="0.4.2", transport=mock_transport("0.4.2"))
        assert await c.check_once() is True
        assert c.status()["available"] is False

    asyncio.run(run())


def _raises_offline(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline bench")


def _html_body(_request: httpx.Request) -> httpx.Response:
    # What a captive portal or a proxy error page actually returns: HTTP 200, not JSON.
    return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>hi</html>")


def test_failed_check_is_silent(tmp_path) -> None:
    # An HTTP error or an unreachable network leaves the checker exactly as it was: no
    # exception, no cached result, and /status keeps reporting null.
    #
    # The last two cases are not httpx.HTTPError, which is the point: check_once runs as a
    # detached background task, so anything escaping it kills the release check with no
    # surface saying so, and narrowing the blanket `except Exception` to `except
    # httpx.HTTPError` (class 18: InvalidURL is not an HTTPError) is a one-word change that
    # the suite would otherwise wave through.
    async def run() -> None:
        for name, transport in (
            ("http-500", mock_transport(status=500)),
            ("offline", httpx.MockTransport(_raises_offline)),
            ("html-body", httpx.MockTransport(_html_body)),          # json.JSONDecodeError
        ):
            c = checker(tmp_path / name, transport=transport)
            assert await c.check_once() is False, name
            assert c.status() is None, name

        # A malformed URL raises httpx.InvalidURL, which is not an HTTPError and is not
        # raised by the transport at all - it comes out of the request build.
        c = checker(tmp_path / "bad-url", url="::not a url::")
        assert await c.check_once() is False
        assert c.status() is None

    asyncio.run(run())


def test_unexpected_body_is_a_successful_check_with_nothing_to_report(tmp_path) -> None:
    async def run() -> None:
        c = checker(tmp_path, transport=mock_transport(body={"nothing": "useful"}))
        assert await c.check_once() is True
        assert c.status()["available"] is False
        assert c.status()["latest"] is None

    asyncio.run(run())


def test_pre_release_only_project_does_not_notify(tmp_path) -> None:
    async def run() -> None:
        c = checker(tmp_path, transport=mock_transport("0.5.0rc1"))
        assert await c.check_once() is True
        assert c.latest is None
        assert c.status()["available"] is False
        # Still counted as checked, so a project publishing only pre-releases is not
        # re-fetched on every retry interval.
        assert c.checked_at is not None

    asyncio.run(run())


# -- opting out ---------------------------------------------------------------------------


def test_config_disabled_never_requests(tmp_path) -> None:
    async def run() -> None:
        calls: list = []
        c = checker(tmp_path, enabled=False, transport=mock_transport(calls=calls))
        assert c.enabled is False
        assert c._due() is False
        for _ in range(50):          # every /status while switched off
            c.maybe_check()
        await asyncio.sleep(0.05)
        await c.aclose()
        assert calls == []

    asyncio.run(run())


def test_repeated_demand_still_makes_one_request(tmp_path) -> None:
    """maybe_check is called on every GET /status, which the web UI issues every 5 s.

    `_due()` is therefore the whole once-a-day guarantee (SPEC 3.6) now that no timer
    stands between the caller and the request. Two ways to break it are covered here:
    asking again after the answer is cached, and asking again while a request is still
    in flight - the second is why maybe_check tracks the task rather than just the time.
    """
    async def run() -> None:
        calls: list = []
        c = checker(tmp_path, transport=mock_transport(calls=calls))
        for _ in range(20):          # a burst, before the first check can finish
            c.maybe_check()
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert c.latest == "9.9.9"

        for _ in range(20):          # ... and again now that the answer is cached
            c.maybe_check()
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        await c.aclose()

    asyncio.run(run())


def test_a_failed_check_is_not_retried_on_the_next_status(tmp_path) -> None:
    # Failure does not move checked_at, so without the explicit hold an offline bench
    # would start a fresh request on every 5 s status poll.
    async def run() -> None:
        calls: list = []

        def offline(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectError("offline bench")

        c = checker(tmp_path, transport=httpx.MockTransport(offline))
        c.maybe_check()
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        assert c._due() is False, "a failed check must hold off, not retry immediately"
        for _ in range(20):
            c.maybe_check()
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        await c.aclose()

    asyncio.run(run())


def test_env_overrides_config_in_both_directions(tmp_path, monkeypatch) -> None:
    """SPEC 3.6: the environment wins over the config file either way."""
    monkeypatch.setenv(uc.ENV_ENABLE, "0")
    c = checker(tmp_path, enabled=True)
    assert c.enabled is False
    c.set_enabled(True)          # the config saying yes does not beat the environment
    assert c.enabled is False
    monkeypatch.setenv(uc.ENV_ENABLE, "1")
    c.set_enabled(False)         # nor does the config saying no
    assert c.enabled is True
    assert checker(tmp_path, enabled=False).enabled is True


def test_env_accepts_the_usual_spellings(monkeypatch) -> None:
    for off in ("0", "false", "FALSE", "no", "off", " Off "):
        monkeypatch.setenv(uc.ENV_ENABLE, off)
        assert uc.env_override() is False
    for on in ("1", "true", "yes", "ON"):
        monkeypatch.setenv(uc.ENV_ENABLE, on)
        assert uc.env_override() is True
    # An empty value reads as unset: no answer, so the config file decides.
    for unset in ("", "   "):
        monkeypatch.setenv(uc.ENV_ENABLE, unset)
        assert uc.env_override() is None
    monkeypatch.delenv(uc.ENV_ENABLE, raising=False)
    assert uc.env_override() is None


def test_env_veto_treats_an_unrecognised_value_as_a_veto(monkeypatch, caplog) -> None:
    """`=disable`, `=none`, `=2` and typos must not resolve to "make the request".

    Only {0,false,no,off} used to disable, so every other way of writing "no" enabled the
    check - on the one switch whose whole point is that a private bench never phones home.
    """
    for value in ("disable", "disabled", "none", "2", "off ;", "nope"):
        monkeypatch.setenv(uc.ENV_ENABLE, value)
        assert uc.env_override() is False, value
    with caplog.at_level("WARNING", logger=uc.log.name):
        monkeypatch.setenv(uc.ENV_ENABLE, "disable")
        uc.env_override()
    assert "not recognised" in caplog.text   # and it says so rather than failing silently


# -- cache robustness ---------------------------------------------------------------------


def test_corrupt_cache_is_ignored(tmp_path) -> None:
    path = tmp_path / "update.json"
    path.write_text("{not json", encoding="utf-8", newline="\n")
    c = checker(tmp_path)
    assert c.status() is None
    assert c._due() is True            # nothing usable cached: check now, then daily


def test_nan_cache_timestamp_is_ignored(tmp_path) -> None:
    """float() accepts "NaN", and NaN is sticky: min(nan, now) is nan, so it survives
    every save. /status then reports checked_at null beside available true, and the
    check then never runs again: `time.time() >= nan + interval` is False, so `_due()`
    answers False forever and one poisoned cache file silently ends the release check
    (SPEC 3.6) on that machine."""
    path = tmp_path / "update.json"
    for bad in ("NaN", "Infinity", "-Infinity"):
        path.write_text(f'{{"latest": "9.9.9", "checked_at": {bad}}}',
                        encoding="utf-8", newline="\n")
        c = checker(tmp_path)
        assert c.checked_at is None
        assert c.status() is None          # nothing reported without a real timestamp
        assert c._due() is True


def test_future_cache_timestamp_does_not_postpone_forever(tmp_path) -> None:
    path = tmp_path / "update.json"
    path.write_text(
        json.dumps({"latest": "0.9.0", "checked_at": time.time() + 10 * uc.CHECK_INTERVAL_S}),
        encoding="utf-8", newline="\n",
    )
    c = checker(tmp_path)
    # Clamped to now, so the next check is one normal interval away rather than ten.
    assert c.checked_at <= time.time()
    assert c._due() is False


def test_unwritable_cache_dir_does_not_break_the_check(tmp_path) -> None:
    async def run() -> None:
        # The cache path's parent is a file, so mkdir/replace fail: the check itself must
        # still report its result, because nothing here is allowed to raise into the loop.
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8", newline="\n")
        c = uc.UpdateChecker(
            current="0.1.0", path=blocker / "sub" / "update.json",
            transport=mock_transport("0.4.2"),
        )
        assert await c.check_once() is True
        assert c.status()["available"] is True

    asyncio.run(run())
