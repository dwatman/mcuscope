# Platform floor review - commit 8028f03 (`requires-python = ">=3.10"`)

Target under review: Ubuntu 22.04 arm64, distro python3.10 (3.10.12), libsqlite3 3.37.2.
No tracked file was edited. Scratch venvs only (`/tmp/mcu310low`, `/tmp/mcu310min`, `/tmp/mcu313low`, `/tmp/tb`, `/tmp/fb`).

## Confirmed problems

### 1. HIGH - `typer>=0.12` floor is a runtime import break

`host/mcuscope/cli_output.py:250` reads `typer._click` at **module level**, unguarded:

```python
USAGE_ERRORS = tuple({click.exceptions.UsageError, typer._click.exceptions.UsageError})
```

`typer._click` did not exist until typer 0.26.0. Bisected in a clean 3.10 venv:

```
0.16.0 MISSING   0.20.0 MISSING   0.24.0 MISSING   0.25.0 MISSING   0.25.1 MISSING
0.26.0 OK        0.26.8 OK        0.27.0 OK
```

With the declared floor resolved (typer 0.12.0), every test module importing the CLI dies at collection:

```
E   AttributeError: module 'typer' has no attribute '_click'
ERROR tests/test_cli.py, test_plotjuggler.py, test_regressions.py,
      test_review_r2_cli.py, test_scaffold.py, test_sim_tcp.py
```

This is not test-only: `mcu` itself cannot start. Any environment that honours the declared floor (a constrained resolver, a lockfile pinned low, a distro/vendored typer) gets a hard crash on `import mcuscope.cli_output`. Ubuntu 22.04's own `python3-typer` is 0.4.x, far below.

Fix: `"typer>=0.26,<1.0"` in `host/pyproject.toml`.

### 2. MEDIUM - `uvicorn>=0.29` floor silently disables the WS backpressure mitigation

`host/mcuscope/server.py:148` imports `uvicorn.protocols.websockets.websockets_sansio_impl` and **swallows `ImportError` with a bare `return`**. That module first appeared in uvicorn 0.35.0 (bisected with `websockets` present, so the probe is valid):

```
uvicorn 0.29.0 MISSING   0.31.0 MISSING   0.33.0 MISSING   0.34.0 MISSING
uvicorn 0.35.0 HAS sansio; own pause_writing: False
```

On uvicorn 0.29-0.34 the daemon starts and looks healthy while the mitigation the surrounding docstring justifies (measured 1.34 MB of transport buffer per stalled client, `ws_dropped` stuck at 0, no gap announced) is inert. The only thing that notices is the test, which fails at the declared floor:

```
FAILED tests/test_hardening.py::test_ws_backpressure_callbacks_are_wired
E   ImportError: cannot import name 'websockets_sansio_impl' from 'uvicorn.protocols.websockets'
```

Severity is the silence, not the crash: a documented memory-safety property is optional under the declared floor.

Fix: `"uvicorn>=0.35,<1.0"`. If a lower uvicorn must stay supported, the `except ImportError` should log a warning rather than return quietly.

### 3. MEDIUM - `pytest-asyncio>=0.23` floor cannot collect the suite at all

`pytest>=8.0` + `pytest-asyncio` 0.23.0-0.23.3 is a resolution both floors permit, and it dies before running anything:

```
INTERNALERROR> File ".../pytest_asyncio/plugin.py", line 610, in pytest_collectstart
INTERNALERROR>   collector.obj.__pytest_asyncio_scoped_event_loop = scoped_event_loop
INTERNALERROR> AttributeError: 'Package' object has no attribute 'obj'
no tests ran in 0.02s   EXIT=3
```

Not 3.10-specific - reproduced identically on a 3.13 `lowest-direct` venv (`EXIT313=3`, same INTERNALERROR), so it predates this commit.

0.23.4 collects but declares `pytest<8`, so it is not a valid answer under `pytest>=8.0`. 0.23.5 is the first version that both allows pytest 8 and works:

```
pytest 8.0.0 asyncio 0.23.5 -> 1131 tests collected in 1.96s
```

Fix: `"pytest-asyncio>=0.23.5"`.

### 4. LOW - `fastapi>=0.110` floor fails 22 tests (test-only)

fastapi 0.110.0 pins `starlette<0.37.0,>=0.36.3`. The suite calls `TestClient(app, client=...)`, and the `client` kwarg landed in starlette 0.44.0:

```
0.37.2 FAIL  0.40.0 FAIL  0.41.0 FAIL  0.42.0 FAIL  0.43.0 FAIL
0.44.0 OK    0.45.0 OK    1.6.0 OK
```

22 failures, all one message:

```
E   TypeError: TestClient.__init__() got an unexpected keyword argument 'client'
tests/test_config_api.py (17), tests/test_plotjuggler.py (4), tests/test_hardening.py (1)
```

Runtime code is unaffected; this is the test suite only. `fastapi>=0.115.7` is the first release whose starlette range (`>=0.40.0,<0.46.0`) *admits* 0.44, but a floor alone does not force it - the honest fix is an explicit `starlette>=0.44` in the dev extra, or stop passing `client=`.

### Full-suite result on Python 3.10 at otherwise-lowest bounds

With only findings 1 and 3 patched (typer 0.26.0, pytest-asyncio 0.23.4; note uv held pytest at 7.4.4 for that combination, so this run is not pure lowest-direct):

```
23 failed, 1106 passed, 1 skipped, 89 warnings in 358.91s
```

All 23 are accounted for by findings 2 and 4 (22 starlette + 1 uvicorn). **Zero failures attributable to Python 3.10 itself.**

## Gap, not a finding

The libsqlite3 3.37.2 runtime could **not** be exercised on this machine. There is no `/usr/bin/python3.10` (`ls: cannot access`), and both available interpreters bundle far newer SQLite:

```
/tmp/mcu310/bin/python  -> 3.10.20, sqlite 3.53.1
host/.venv (uv run)     -> 3.13.5,  sqlite 3.47.1
```

Task 1's fallback (apt python3.10 + store/retention/vacuum tests) was therefore not runnable. The SQLite conclusion below is a static audit, not a run. Verifying it needs the actual arm64 box.

## Checked and refuted

- **SQLite feature floor is comfortably met.** AST-extracted all 113 SQL string literals from `store.py` (the only module issuing SQL - 67 `execute*` calls; every other hit was Python prose or Python functions). Highest requirement is **3.25** (`ROW_NUMBER() OVER (PARTITION BY ...)` in `query_plot_series`, `store.py:1787/1802/1803`). 3.37.2 meets it with margin.
- Absent entirely: STRICT (3.37), generated columns (3.31), `->`/`->>` (3.38), `json_*` in SQL (3.38), `UNIXEPOCH` (3.38), SQL `format()` (3.38), `IIF` (3.32), SQL math functions (3.35), `DROP COLUMN` (3.35), `RENAME COLUMN` (3.25), `FILTER (WHERE ...)` (3.30), `NULLS FIRST/LAST` (3.30), UPSERT `ON CONFLICT` (3.24), CTEs, `WITHOUT ROWID`, expression indexes.
- **RETURNING (3.35) is not used.** The lone regex hit was English prose in a docstring, `store.py:154` ("...and returning ~0.02% of the space"). `grep -n "RETURNING"` returns nothing.
- Everything else used is ancient: `AUTOINCREMENT`, `ADD COLUMN` (3.2), `RENAME TO`, partial index `WHERE` (3.8), `ON DELETE CASCADE` (3.6.19), `journal_mode=WAL` (3.7), `auto_vacuum`/`PRAGMA incremental_vacuum` (3.1), `COALESCE`, `INSERT OR REPLACE`.
- **Every floor version's `Requires-Python` admits 3.10** - the loosest is websockets 14.0 at `>=3.9`; all others are `>=3.7` or `>=3.8`, pyserial 3.5 declares none.
- **arm64 wheels are complete.** Of the 29 packages in the resolved (latest) environment, exactly two are compiled, and both ship a cp310 manylinux aarch64 wheel: `regex==2026.8.31` (`...cp310-cp310-manylinux2014_aarch64...whl`) and `pydantic-core==2.46.5` (transitive via fastapi, `...cp310-cp310-manylinux_2_17_aarch64...whl`). The other 27, including `ruff` (`py3-none-manylinux_2_17_aarch64`), are pure or have a pure wheel. `regex` was not the only C extension - `pydantic-core` was missed by the brief, but it is fine.
- **No uvicorn accelerator extras assumed.** `uvicorn[standard]`, `uvloop`, `httptools`, `watchfiles`, `orjson`, `ujson` appear nowhere in project sources, pyproject, or docs. Every grep hit was inside `host/.venv/lib/python3.13/site-packages/`.
- **tomlkit 0.12.0 has `.unwrap()`.** Verified directly: `tomlkit.parse('a = 1\n[t]\nb = "x"\n').unwrap()` -> `{'a': 1, 't': {'b': 'x'}}` with builtin `int`/`str`, which is exactly what `config.py:136` relies on.
- **Import-time on 3.10 with no dev deps is clean** (`/tmp/mcu310min`, `-e host` only). `mcuscoped --help`, `mcu --help`, `mcu-sim --help` all exit 0, no traceback. `mcuscoped --sim --port 8799` started, served, and answered `mcu status`, `mcu ports` and `mcu cmd 'i2c scan'` (the last correctly refusing with "port is ambiguous"). Daemon log had zero tracebacks.

## Two corrections to the brief

- `mcu --port` is a **serial port alias**, not the daemon HTTP port. `mcu --port 8799 status` would not have targeted the test daemon. The daemon URL is `--url` (or `MCUSCOPE_URL`); the smoke test used `mcu --url http://127.0.0.1:8799 status`.
- The smoke test ran against the user's **real** data dir (`~/.local/share/mcuscope/capture.db`) and real config - `mcuscoped` takes its DB path from platformdirs and there is no isolating flag on the command line given. It appended sim lines and opened auto-session id 2 before being killed. Worth an isolated `--db` next time.

## Resolved lowest-bounds versions (`uv pip list`, `/tmp/mcu310low`, python 3.10.20)

Direct deps at their declared floor, transitives resolved latest. typer and pytest-asyncio shown post-bump; their `lowest-direct` values were 0.12.0 and 0.23.0.

| Package | Version | | Package | Version |
|---|---|---|---|---|
| fastapi | 0.110.0 | | pytest | 7.4.4 (8.0.0 declared) |
| httpx | 0.27.0 | | pytest-asyncio | 0.23.4 (0.23.0 declared) |
| platformdirs | 4.0.0 | | pytest-cov | 5.0.0 |
| pyserial | 3.5 | | pytest-randomly | 3.15.0 |
| regex | 2024.4.16 | | pytest-timeout | 2.3.1 |
| tomlkit | 0.12.0 | | ruff | 0.5.0 |
| typer | 0.26.0 (0.12.0 declared) | | uvicorn | 0.29.0 |
| websockets | 14.0 | | | |

Transitives: annotated-doc 0.0.5, annotated-types 0.8.0, anyio 4.14.2, certifi 2026.7.22, click 8.5.0, coverage 7.16.0, exceptiongroup 1.3.1, h11 0.16.0, httpcore 1.0.9, idna 3.19, iniconfig 2.3.0, markdown-it-py 4.2.0, mdurl 0.1.2, packaging 26.3, pluggy 1.6.0, pydantic 2.13.5, pydantic-core 2.46.5, pygments 2.21.0, rich 15.0.0, shellingham 1.5.4, sniffio 1.3.1, starlette 0.36.3, tomli 2.4.1, typer-cli 0.12.0, typer-slim 0.12.0, typing-extensions 4.16.0, typing-inspection 0.4.4.

## Suggested pyproject changes (not applied)

```toml
"typer>=0.26,<1.0",      # typer._click, used at import time in cli_output.py
"uvicorn>=0.35,<1.0",    # websockets_sansio_impl, or the WS backpressure fix is inert
# dev
"pytest-asyncio>=0.23.5",  # 0.23.0-0.23.3 INTERNALERROR on pytest 8; 0.23.4 caps pytest<8
"starlette>=0.44",         # TestClient(client=...) used by 22 tests
```
