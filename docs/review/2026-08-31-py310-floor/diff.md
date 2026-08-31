# Adversarial review: 8028f03 "host: support Python 3.10"

Repo: /home/daniel/git/mcuscope, HEAD = 8028f03 (confirmed).
Interpreters used: `/tmp/mcu310/bin/python` (3.10.19), `host/.venv` via `uv run python` (3.13), plus a throwaway `/tmp/mcu39` (3.9.25) and `/tmp/tk012` (tomlkit==0.12.0 floor).
Old code reachable as `/tmp/oldpkg/mcuscope_old/config.py` (= `git show 8028f03~1:host/mcuscope/config.py`). No tracked file was modified.

---

## Confirmed defects

### 1. `docs/SPEC.md:311` still says "Python >= 3.11" - MEDIUM

```
docs/SPEC.md:311:- Python >= 3.11, cross-platform: Linux and Windows 10/11.
```

The commit edited SPEC 3.1's dependency bullet (line 320, `tomllib` -> `tomlkit`) but not the technology bullet nine lines above it.
SPEC is the authoritative design contract (`CLAUDE.md`: "When code and SPEC disagree, SPEC wins"), so as it stands the contract says the floor is 3.11 while `host/pyproject.toml:9` says `requires-python = ">=3.10"`, `.github/workflows/ci.yml:34` runs a 3.10 leg, and `host/mcuscope/__init__.py:25` guards at 3.10. By the repo's own rule the *code* is the thing that is wrong.

Fix: `- Python >= 3.10, cross-platform: Linux and Windows 10/11.`

### 2. `host/README.md:18` still says "Requires Python 3.11 or newer" - MEDIUM

```
host/README.md:18:Requires Python 3.11 or newer.
```

This is not a duplicate of the root README. `host/pyproject.toml:8` is `readme = "README.md"`, resolved relative to `host/`, so **`host/README.md` is the PyPI long description**. The root `README.md` was updated in all four places; the packaged one was missed, so the next release publishes a project page claiming 3.11 while the wheel installs on 3.10. The root README's own "Python 3.10 or newer is required" (line 101) then contradicts the PyPI page for the same version.

Evidence:
```
$ grep -rn "3\.11" --include='*.md' --include='*.toml' . | grep -v docs/review | grep -v CHANGELOG
host/pyproject.toml:23:    "Programming Language :: Python :: 3.11",   # correct, a classifier
host/README.md:18:Requires Python 3.11 or newer.                    # STALE
docs/SPEC.md:311:- Python >= 3.11, cross-platform...                # STALE (finding 1)
```
Nothing else outside `docs/review/` and CHANGELOG history is stale; `tomllib` and `StrEnum` survive only in deliberate, correct prose (`config.py:5`, `SPEC.md:320`, `protocol.py:59`, CHANGELOG entries describing the change).

Fix: `Requires Python 3.10 or newer.`

### 3. `host/mcuscope/__init__.py:17-18` - the new comment's claim is false - LOW/MEDIUM

```python
# as importing a submodule and then failed on whatever 3.10 feature it reached first
# (`X | Y` in an isinstance, `match`), with an error that never mentions the Python
# version.
```

Both named mechanisms are absent from the package, and the "never mentions the Python version" claim is also false for the failure that actually happens.

Evidence:

- `grep -rnE '^\s*match .*:\s*$' host/mcuscope/*.py` -> **no hits**. `grep -rnE '^\s*case .*:'` -> no hits. There is no `match` statement in the package.
- `grep -rnE 'isinstance\([^)]*\|' host/mcuscope/*.py` -> **no hits**. There is no runtime `X | Y` isinstance anywhere.
- Every module except `__init__.py` itself carries `from __future__ import annotations`, so all `X | Y` annotations are strings and never evaluated at import.
- Empirically, on a real 3.9 with the guard neutralised (package copied to `/tmp/mcu39pkg`, guard rewritten to `if False:`, deps installed into `/tmp/mcu39`):

```
mcuscope           -> OK
mcuscope.protocol  -> OK        (used to fail: "cannot import name 'StrEnum'")
mcuscope.config    -> OK        (used to fail: "No module named 'tomllib'")
mcuscope.store     -> OK
mcuscope.pjstream  -> OK
mcuscope.serial_link -> OK
mcuscope.sim       -> OK
mcuscope.server    -> TypeError: Unable to evaluate type annotation 'str | None'. If you
                      are making use of the new typing syntax (unions using `|` since
                      Python 3.10 ...)
mcuscope.daemon    -> same TypeError (imports server)
```

So the real first failure on a sub-floor interpreter is **pydantic evaluating a PEP 604 annotation while building the FastAPI request/response models in `server.py`**, and that error does name "since Python 3.10". The pre-change comment was accurate for its era (StrEnum from protocol, tomllib from config); the rewrite swapped two true statements for two false ones. This is the rationale a future reader would act on, and it is a REVIEW-class "comment naming a mechanism that does not exist".

Fix (comment only):
```
# as importing a submodule and then failed later, deep in pydantic's annotation
# evaluation for the server models, on a `str | None` union (PEP 604, 3.10+). Say it
# plainly at import of the package and name the interpreter, since the usual cause is a
# stray one earlier on PATH.
```
Note this also means the guard now fires *later in usefulness* than it used to: `protocol`, `config`, `store`, `serial_link` and `sim` all import cleanly on 3.9 now. The guard is still worth keeping (it is the only thing that names the interpreter path), but the comment must stop claiming a mechanism that no longer exists.

### 4. `host/mcuscope/protocol.py:59-61` - the `__str__` override is load-bearing and untested - LOW

```python
# (str, Enum) + __str__ is StrEnum for 3.10, where enum.StrEnum does not exist.
def __str__(self) -> str:
    return self.value
```

The override is **correct and necessary** - without it, `(str, Enum)` diverges both from StrEnum and *between the two supported versions*:

```
py3.10  str='Plain.EVENT'  fstring='event'        %s='Plain.EVENT'
py3.13  str='Plain.EVENT'  fstring='Plain.EVENT'  %s='Plain.EVENT'
```

(3.10's `Enum.__format__` delegates to the mixin, 3.12+ does not - so deleting the override would silently change f-string output *on one version only*.)

But nothing in the tree exercises it. Every `LineClass` use in `host/mcuscope` and `host/tests` is an identity comparison:

```
protocol.py:105-121   returns
sim.py:168            `is not p.LineClass.COMMAND`
serial_link.py:187,769,778,811,819   annotation + `is` comparisons
tests/test_protocol.py:33-45,630, tests/test_sim.py:204   `is` comparisons
```

`LineClass` never reaches sqlite3, `json.dumps`, a pydantic model, a dict key, or an f-string, so the override can be deleted today with a fully green suite - and the next person to write `f"{cls}"` gets a version-dependent bug with no test to catch it.

Fix: one assertion in `host/tests/test_protocol.py`, next to `test_classify`:
```python
def test_lineclass_stringifies_as_its_value() -> None:
    """(str, Enum) without the __str__ override gives 'LineClass.EVENT' from str() and,
    on 3.10 only, 'event' from an f-string. Pin StrEnum semantics on every version."""
    assert str(p.LineClass.EVENT) == "event"
    assert f"{p.LineClass.EVENT}" == "event"
    assert "%s" % p.LineClass.EVENT == "event"
    assert json.dumps(p.LineClass.EVENT) == '"event"'
```

### 5. Supporting 3.10 forks the `websockets` major, undocumented - LOW

`websockets>=14.0` is uncapped (`host/pyproject.toml:46`), and `websockets` 17.x requires Python >= 3.11:

```
$ VIRTUAL_ENV=/tmp/mcu310 uv pip install --dry-run "websockets==17.0.1"
  ... websockets==17.0.1 depends on Python>=3.11 ... cannot be used.
$ /tmp/mcu310/bin/python -c "import websockets; print(websockets.__version__)"   -> 16.1.1
```

So the new 3.10 CI leg is the only leg exercising websockets 16.x, and the daemon's WS server plus the CLI's WS client are now implicitly required to work across two majors of the one dependency the project does not pin. This is *tested* (the 3.10 matrix leg exists), so it is not broken - but it is a new support obligation that neither SPEC 3.1 nor the CHANGELOG entry records. One line in the CHANGELOG bullet or SPEC 3.1 would close it.

---

## Checked and refuted

- **`LineClass` (str, Enum) semantics, 27 usage forms, 3.10 vs 3.13 vs a 3.13 `enum.StrEnum` twin**: identical on all three for f-string, padded f-string, `format()`, `%s`, `str()`, `.value`, `.name`, `== "event"`, `hash()`, `in {"event", ...}`, dict key both directions, `LineClass("event")` construction, `json.dumps` (scalar and list), `sorted()`, `"x" + m`, `",".join`, `.upper()`, `.startswith()`, `isinstance(m, str)`, sqlite3 parameter binding (stores `'event'`, `typeof` = `text`), `.encode()`, `"{c}".format(c=m)`. The only difference from the StrEnum twin is the class name inside `repr()`, which is expected.
- **Pydantic / OpenAPI exposure of `LineClass`**: none. `grep -rn LineClass host/mcuscope host/tests` returns 15 hits, none in `server.py` and none in any request/response model, so no schema or serialised JSON can have changed. `classify()` has exactly two callers (`serial_link.py:764`, `sim.py:168`), both internal routing; the value never reaches the DB or the wire.
- **`unwrap()` returns builtins**: walked a document covering every TOML type (string, multiline, literal, int, float, bool, array, inline table, AoT, offset-datetime, local date, local time) and compared `tomlkit.parse(...).unwrap()` against `tomllib.loads(...)` node by node. Every node's exact type matches (`builtins.str/int/float/bool/list/dict`, `datetime.date/datetime/time`), including nested `[[ports]]` entries and nested inline tables. `k == l` is True. `type(port) is int` and `isinstance(port, bool)` is False, so `_as_int`'s `isinstance(x, bool) or not isinstance(x, int)` guard sees exactly what it saw before. Keys are builtin `str`.
- **Rejection parity for a TOML datetime / float where an int is expected**: identical messages. `port = 1979-05-27T07:32:00Z` -> "invalid value: [server] port must be a whole number, not datetime.datetime(...)"; `retention_days = 1979-05-27` -> "...not datetime.date(1979, 5, 27)"; `port = 8765.0` -> "...not 8765.0". Only the datetime *repr* differs cosmetically (`timezone.utc` vs an equivalent `timezone(timedelta(0))`).
- **`load_config` old vs new over a 38-case malformed corpus**: dup keys, table redefined, key both value and table, invalid escape, unterminated string (basic and multiline), invalid UTF-8 bytes, control char, NUL byte, bare garbage, unterminated table header, BOM-only file, empty file, CRLF, 2 MB file, deeply nested inline tables, `[[server]]` where a table is expected, `port = "8765"` / `8765.0` / `true` / `nan` / `0x1F41` / an array, `[[ports]]` with a non-string field and a non-string alias, `ports = "oops"`, `server = 3`, out-of-range port, config path being a directory, config path a symlink to `/dev/null`. **The ConfigError message class ("invalid TOML" / "invalid value" / "cannot read" / "not a table" / "not an array of tables") and the accept/refuse verdict are identical in all 38 cases.** Only the parser's own wording inside the "invalid TOML" message changed, which the commit already acknowledged by rewording the BOM comment and `test_config_with_a_utf8_bom_loads`'s docstring.
- **Can tomlkit raise a non-`TOMLKitError` on malformed input?** 60,000 random mutations of five realistic seeds through `tomlkit.parse(...).unwrap()`: **zero** escaping exception types. Every exception class in `tomlkit.exceptions` subclasses `TOMLKitError` (checked by MRO). Belt and braces: `ParseError` also subclasses `ValueError`, so any hypothetical miss would still land in the existing `except (TypeError, ValueError, AttributeError)` arm as "invalid value" rather than crash the daemon. `KeyAlreadyPresent` is the one class that is *not* a `ValueError` - and it is a `TOMLKitError`, so the new handler covers it (verified by the `dup_key` case).
- **Non-UTF-8 and I/O paths still classify the same**: `UnicodeDecodeError` (a `ValueError`) -> "invalid value", `IsADirectoryError` -> "cannot read", both old and new.
- **Deep nesting**: the new code is strictly *better*. Old: `inline_depth_500` and `array_depth_500` raised an uncaught `RecursionError` out of `load_config` (a traceback, not a ConfigError). New: tomlkit's 100-level guard gives `ConfigError: ... invalid TOML: TOML value nested more than 100 levels deep`. No regression; a fixed latent bug.
- **Differential accept/reject, tomllib vs tomlkit, 150,000 mutations**: tomlkit rejects nothing tomllib accepts (0 cases), and where both accept, the unwrapped values are **always equal** (0 value divergences). tomlkit accepts 26/150,000 inputs tomllib rejects - all TOML 1.1-draft relaxations (trailing comma in an inline table, newline inside an inline table, a `#` inside a datetime literal). None of them can change a typed config value, and `_check_shape` plus the `_as_*` helpers still run unchanged, so no input reaches a wrong value by this route.
- **`tomlkit.exceptions` reachable without an explicit import**: yes, transitively via `tomlkit/__init__.py`; verified on both 3.10 and 3.13 and at the declared floor. (Style nit, not a defect: `from tomlkit.exceptions import TOMLKitError` would not depend on tomlkit's internal import graph. If the attribute were ever absent, the `except` clause itself would raise `AttributeError` while handling the parse error.)
- **Declared dependency floor `tomlkit>=0.12,<1.0` supports `unwrap()`**: installed `tomlkit==0.12.0` into a clean 3.10 venv; `parse().unwrap()` works and returns builtins, and `tomlkit.exceptions.TOMLKitError` is reachable. The floor is honest.
- **tomlkit is ~10x slower than tomllib but irrelevant**: 3.4 ms vs 0.33 ms on an 8-port config. `load_config` has two callers - `daemon.py:283` (startup, once) and `server.py:980`, which already wraps it in `asyncio.to_thread`. No loop stall, no hot path.
- **The three converted `asyncio.TimeoutError` handlers each wrap only `asyncio.wait_for`**: `serial_link.py:1012-1013` (`wait_for(fut, ...)`), `server.py:1618-1619` (`wait_for(q.get(), WS_KEEPALIVE_S)`), `server.py:1792-1793` (`suppress` around `wait_for(q.get(), remaining)`). No `concurrent.futures.Future.result(timeout=)`, no `queue.get(timeout=)`, no thread `join(timeout=)` inside any of the three blocks.
- **No further dead builtin-`TimeoutError` sites** (the commit fixed four; there are no more). Swept `host/mcuscope/*.py`, `host/tests/*.py` and `tools/*.py` for `except .*TimeoutError`, `suppress(.*TimeoutError)`, `pytest.raises(.*TimeoutError)`, plus every timeout-capable API (`wait_for`, `.result(timeout=`, `as_completed`, `futures.wait`, `.get(timeout=`, `.join(timeout=`, `queue.Empty`). The remaining bare `except TimeoutError` sites are all correct on 3.10, verified by running the classes on 3.10:
  - `sim.py:707` - `socket.accept()` under `settimeout`. `socket.timeout is TimeoutError` -> **True on 3.10** (aliased in 3.10, not 3.11), so the handler is live.
  - `cli.py:449`, `server.py:1710`, `server.py:1931`, `store.py:391/394` - the `regex` module's `timeout=`. Measured on 3.10: `regex` raises `builtins.TimeoutError`, `isinstance(e, TimeoutError)` True, `isinstance(e, asyncio.TimeoutError)` **False** - so these must stay bare and correctly were not converted. Converting them would have been the mirror-image bug.
  - `tests/test_sim.py:511`, `tests/test_sim_tcp.py:43,198`, `tests/test_plotjuggler.py:138,154,172,410` - all socket recv timeouts, live on 3.10 for the same alias reason.
  - `tests/test_webui.py:119` `fut.result(timeout=5.0)` on an `asyncio.run_coroutine_threadsafe` future *is* a `concurrent.futures` future, and `concurrent.futures.TimeoutError` is **not** the builtin on 3.10 - but there is no `except` around it, so a timeout fails the test on every version either way. Not a defect.
  - Reference measurements on 3.10: `asyncio.TimeoutError is TimeoutError` False; `socket.timeout is TimeoutError` True; `wait_for` raises `asyncio.exceptions.TimeoutError` (not builtin); `cf.Future.result` raises `concurrent.futures._base.TimeoutError` (not builtin); `queue.get` raises `queue.Empty`. On 3.13 the first four are all the builtin.
- **`host/tests/test_scaffold.py:91` substring is right**: the message is `mcuscope requires Python 3.10 or newer; this is 3.9.25 (/tmp/mcu39/bin/python)` (captured from a real 3.9 run), so `"requires Python 3.10" not in out` matches the real text. (Pre-existing weakness, not introduced here: the assertion is unreachable-if-failing, since `proc.returncode == 0` is asserted first and the guard makes the exit non-zero.)
- **CHANGELOG placement**: the entry sits under `## [Unreleased]` -> `### Changed`, which is right for a support-floor move (Keep a Changelog). The nested sub-bullet naming `tomllib`/`StrEnum` is accurate.
- **`cli.py:1673` help text**: `grep -rn "addresses CAN controller"` over the whole repo (excluding `.git`, `.venv`) returns **no hits** - no test, doc, README or fixture quotes the old wording. The new line is 92 chars, inside the 100-char ruff limit.
- **Root `README.md` lines ~95-130 and ~372-400 read correctly** after the substitutions: "Python 3.10 or newer is required", "anything older than 3.10 ... let `uv` fetch one" (followed by `uv python install 3.12`, still a valid suggestion), "If that is older than 3.10, the install fails on `requires-python`", "CI runs ... across Python 3.10 to 3.13". `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md` and `docs/REVIEW.md:317` are all consistent.
- **Lint**: `ruff check .` clean under both `/tmp/mcu310/bin/python -m ruff` and the 3.13 venv, with `target-version = "py310"`. The `# noqa: UP036` on the guard is still required and still correct.
- **Full test suite on the floor interpreter (3.10.19)**: `1129 passed, 1 skipped, 1 warning in 361.78s`, exit 0 (the skip is the Windows-only COM enumeration test; the warning is starlette's `httpx2` deprecation, present on 3.13 too). This is REVIEW class 42's own prescribed sweep, and it is green - the commit's four handler conversions are complete and sufficient. `tests/test_scaffold.py` (the console-script wrapper leg, which skips when the package is not installed) ran 7 passed, not skipped, so the guard-message assertion really executed.
