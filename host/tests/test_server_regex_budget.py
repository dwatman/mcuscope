"""User regexes are bounded before they compile, compile off the loop, and read `\\d \\w \\s \\b`
as ASCII (SPEC 3.4): `regex` expands counted repeats at compile time, so a short nested repeat
stalled the loop for a second and a slightly longer one exhausted memory."""

from __future__ import annotations

import asyncio

import pytest
import regex
from fastapi.testclient import TestClient

from mcuscope.config import Config, ServerConfig, StorageConfig
from mcuscope.server import create_app
from mcuscope.store import MAX_REPEAT_EXPANSION, repeat_expansion

NESTED = "(?:(?:a{100}){100}){100}"        # 1,010,100 once expanded
TOO_LARGE = "match regex too large: repeats expand to 1010100 (max 100000)"
# The verbose comment hides the paren that closes the middle group from a scan that does
# not know about the `x` flag, and the `#` before `(?x:` hides it from one that assumes
# verbose throughout: both read ~20,000 where regex expands 1,000,000.
SMUGGLED = "a#(?x:(?:(?:a{100}) #)\n{100}){100})"


@pytest.fixture
def c(tmp_path):
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=0),
        storage=StorageConfig(db_path=str(tmp_path / "cap.db")),
    )
    app = create_app(config, config_path=tmp_path / "config.toml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client


def _refusals(c, pattern: str) -> dict[str, tuple[int, str]]:
    out = {}
    for name, r in (
        ("lines", c.get("/lines", params={"match": pattern})),
        ("export", c.get("/lines/export", params={"match": pattern})),
        ("wait", c.post("/wait", json={"match": pattern, "timeout_ms": 20})),
        ("assert", c.post("/assert", json={"expect": [pattern]})),
    ):
        out[name] = (r.status_code, r.json().get("error", "") if r.status_code != 200 or
                     name != "export" else "")
    return out


def test_a_nested_repeat_is_refused_by_size_on_every_route(c) -> None:
    got = _refusals(c, NESTED)
    assert got["lines"] == (400, TOO_LARGE)
    assert got["export"] == (400, TOO_LARGE)
    assert got["wait"] == (400, TOO_LARGE)
    assert got["assert"] == (
        400, f"regex too large {NESTED!r}: repeats expand to 1010100 (max 100000)"
    )


def test_the_bound_counts_siblings_not_only_one_nesting_path(c) -> None:
    # Twenty of these fit in 200 characters and cost 20x the memory of one.
    r = c.get("/lines", params={"match": r"\d{65535}\d{65535}"})
    assert r.status_code == 400
    assert r.json()["error"] == "match regex too large: repeats expand to 131070 (max 100000)"


def test_a_verbose_comment_cannot_smuggle_a_repeat_past_the_scan(c) -> None:
    assert regex.compile(SMUGGLED)   # a valid pattern, so only the bound refuses it
    r = c.get("/lines", params={"match": SMUGGLED})
    assert r.status_code == 400 and "match regex too large" in r.json()["error"]
    # Positive control: a small verbose pattern still compiles.
    assert c.get("/lines", params={"match": r"(?x) ERR \s+ \d+ # code"}).status_code == 200


# Verbose mode reads each of these counts as 100: `regex` skips whitespace and a `#` comment
# (which may hold a `}`) inside the braces. A scan of `{digits}` alone read them as literals.
SPACED_COUNTS = ["{ 100 }", "{1 0 0}", "{100 ,}", "{ 1#c\n00 }", "{1#}\n00}", "{\u00a0100}"]


@pytest.mark.parametrize("count", SPACED_COUNTS)
def test_a_verbose_count_spelled_with_spaces_or_comments_is_bounded_on_every_route(
    c, count
) -> None:
    one = f"(?x)a{count}"
    assert regex.fullmatch(one, "a" * 100) and not regex.fullmatch(one, "a" * 99)
    pattern = f"(?x)(?:(?:a{count}){count}){count}"
    for route, (status, error) in _refusals(c, pattern).items():
        assert status == 400 and "regex too large" in error, (route, status, error)
    # Positive control: one such count is inside the bound.
    assert c.get("/lines", params={"match": one}).status_code == 200


@pytest.mark.parametrize("pattern", ["(?u)x", "(?L)x", "(?au)x"])
def test_an_inline_flag_clashing_with_ascii_is_a_400_on_every_route(c, pattern) -> None:
    why = "ASCII, LOCALE and UNICODE flags are mutually incompatible (user patterns always " \
        "compile as ASCII)"
    got = _refusals(c, pattern)
    assert got["lines"] == got["export"] == got["wait"] == (400, f"bad match regex: {why}")
    assert got["assert"] == (400, f"bad regex {pattern!r}: {why}")
    # Positive control: the ASCII flag spelled inline agrees with the dialect.
    assert c.get("/lines", params={"match": "(?a)x"}).status_code == 200


def test_the_largest_legal_single_repeat_is_accepted_everywhere(c) -> None:
    ok = r"\d{65535}"
    assert c.get("/lines", params={"match": ok}).status_code == 200
    assert c.get("/lines/export", params={"match": ok}).status_code == 200
    assert c.post("/wait", json={"match": ok, "timeout_ms": 20}).status_code == 200
    assert c.post("/assert", json={"expect": [ok], "allow_empty": True}).status_code == 200


def test_a_bad_pattern_keeps_its_own_refusal(c) -> None:
    r = c.get("/lines", params={"match": "("})
    assert r.status_code == 400 and r.json()["error"].startswith("bad match regex:")


def test_user_patterns_compile_off_the_event_loop(c, monkeypatch) -> None:
    seen: list[tuple[str, bool, int]] = []
    real = regex.compile

    def recording(pattern, *args, **kw):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        seen.append((pattern, on_loop, args[0] if args else kw.get("flags", 0)))
        return real(pattern, *args, **kw)

    monkeypatch.setattr(regex, "compile", recording)
    c.get("/lines", params={"match": "p1"})
    c.get("/lines/export", params={"match": "p2"})
    c.post("/wait", json={"match": "p3", "timeout_ms": 20})
    c.post("/assert", json={"expect": ["p4"], "forbid": ["p5"], "allow_empty": True})
    mine = [s for s in seen if s[0] in {"p1", "p2", "p3", "p4", "p5"}]
    assert {s[0] for s in mine} == {"p1", "p2", "p3", "p4", "p5"}   # positive control
    assert [p for p, on_loop, _ in mine if on_loop] == []
    # Every site, the live /wait and /assert copies included, reads the ASCII dialect.
    assert [p for p, _, flags in mine if not flags & regex.ASCII] == []


@pytest.mark.parametrize(("pattern", "size"), [
    (r"\d{65535}", 65535),
    (r"\d{65535}\d{65535}", 131070),
    (NESTED, 1010100),
    (r"(?:a{10}|b){10}", 120),        # alternation sums: `:`, a{10}, b, times 10
    (r"(?:a{10})+", 22),              # `+` compiles a copy for its loop
    (r"a{5}", 5), (r"a{0}", 1), (r"a{5,}", 6), (r"a{5,9}", 6), (r"a{,9}", 1), (r"a{}", 3),
    (r"a{50000}+", 50000),            # possessive suffix, not a second quantifier
    (r"a{50000}?", 50000),            # lazy suffix
    (r"\p{L}{100}", 100),             # the braces of \p{..} are not a count
    (r"\{100}", 5),                   # an escaped brace opens no count
    (r"[a{100}]{100}", 100),          # nor does a brace inside a set
    (r"[]{9}]{2}", 2),                # a leading ] is a member
    (r"[^]{9}]{2}", 2),
    (r"[[:alpha:]{9}]{2}", 2),        # the POSIX class's ] does not close the set
    (r"[\]{9}]{2}", 2),               # nor does an escaped one
    (r"(?#(((()a{7}", 7),             # a comment's parens open nothing
    (r"(?#\)(()a{7}", 7),             # and an escaped ) does not end it
    (r"(a{10}", 10),                  # an unclosed group still counts (compile refuses it)
    (r"a)b{5}", 7),                   # as does a stray )
    (r"(?x)a{3}b{4}", 12 * 3 * 4),    # verbose: length times every count, an upper bound
    (r"(?V1)a+", 7 * 2),
    ("(?x)a{ 3 }b{4 ,}", 16 * 3 * 5),  # a spaced count is still a count
    ("(?x)a{1#}\n0}", 12 * 10),       # a comment inside the braces may hold a }
    ("(?x)a{1#x\nb}", 12),            # not a count: regex reads it as literals
    ("(?V1)a{ 3 }", 11 * 3),          # V1 alone reads it as literals: counted anyway
])
def test_repeat_expansion_counts_what_regex_expands(pattern, size) -> None:
    assert repeat_expansion(pattern) == size


def test_the_limit_is_the_documented_one() -> None:
    assert MAX_REPEAT_EXPANSION == 100_000


def test_ascii_classes_skip_a_non_ascii_digit_in_a_marker(c) -> None:
    probe = {"expect": [r"reading \d"], "chan": "marker"}
    assert c.post("/marker", json={"text": "reading \u0663"}).status_code == 200
    assert c.get("/lines", params={"match": r"reading \d", "chan": "marker"}).json()["lines"] == []
    assert c.post("/assert", json=probe).json()["status"] == "fail"
    # Positive control: an ASCII digit still matches, on both paths.
    assert c.post("/marker", json={"text": "reading 3"}).status_code == 200
    rows = c.get("/lines", params={"match": r"reading \d", "chan": "marker"}).json()["lines"]
    assert [r["raw"] for r in rows] == ["reading 3"]
    assert c.post("/assert", json=probe).json()["status"] == "pass"
