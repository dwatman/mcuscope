# Class 43 floor sweep, 2026-09-25

HEAD `bd3eff5eb75ed208a95fa35d7be4af687e00cd29`.
Python 3.10.20, fresh venv `~/tt-data/mcuscope-2026-09-25/floor/low`, `uv pip install --resolution lowest-direct -e '.[dev]'` from a `git archive HEAD` copy (`floor/src`), `MCUSCOPE_CONFIG_DIR`/`MCUSCOPE_DATA_DIR` pointed at scratch dirs.

Result: whole suite, one run, random order: **2 failed, 2939 passed, 1 skipped** (530 s).

- The JS (`test_webui_js.py`, node 22.23.3) and firmware (`test_firmware_monitor.py`, gcc 13.3) wrappers ran and passed.
- The one skip is `test_reconnect.py:83`, Windows COM enumeration (expected on Linux).
- `ruff check .` with the floor ruff 0.13.0: clean.
- `except ImportError` grep over `host/mcuscope`, two sites, both compliant:
  - `server.py:174` logs a warning before its bare `return`.
  - `serial_link.py:52` is the Windows `termios` fallback.
  - At the uvicorn 0.35.0 floor, `websockets_sansio_impl.WebSocketsSansIOProtocol` imports, so the backpressure wiring is live.

Log: `~/tt-data/mcuscope-2026-09-25/floor/suite.log`.

## Failure 1 (floor-specific, test defect, no floor move needed)

`tests/test_cli_numeric_grammar.py::test_every_numeric_parameter_takes_the_ascii_grammar`

- Fails at `typer==0.26.0` (the floor) with click 8.4.2 or 8.5.0; passes at `typer==0.27.0` with either click. Passes in the normal `.venv` (typer 0.27.0, click 8.4.2).
- Cause: at typer 0.26 the parameter types report click's names (`integer`, `integer range`, `text`); 0.27 reports `int`, `int range`, `str`.
  The test filters on `prm.type.name in ("int", "int range", "float", "float range")`, so at 0.26 it sees only the 4 float params and trips `len(found) >= 33`.
- The product is correct at the floor: walking the same tree at typer 0.26 finds all 33 numeric params as `Ascii*` types, none plain (script `floor/walk2.py`).
  The other 31 tests in the file pass at the floor.
- Fix: make the test's filter floor-proof (`isinstance(prm.type, (click.types.IntParamType, click.types.FloatParamType))`, or both name sets).
  Raising the floor to `typer>=0.27` would also turn it green, but it would move a floor for a test-only defect. Owner should pick; the test fix is my recommendation.

## Failure 2 (not floor-specific, flaky test)

`tests/test_server_exports.py::test_an_abandoned_job_removes_its_files_whichever_side_ends_last[finished first]`

- Also fails in the normal `.venv`: failed on 1 of 3 runs of the node alone (`-p no:randomly`), and on the one file run there.
- Cause: in the `finished first` order, `_ExportJob.abandon()` (server.py ~2950) passes `_remove_all` to the `export-cleanup` pool and returns.
  The test asserts `not os.path.exists(made[0])` immediately, racing that worker.
- Fix: the test waits for the cleanup (poll with a deadline, or drain `_pool("export-cleanup", 1)`) before asserting. Product behaviour matches its docstring.

## Resolved versions (`uv pip freeze`)

```
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
certifi==2026.7.22
click==8.5.0
coverage==7.16.1
exceptiongroup==1.3.1
fastapi==0.115.7
h11==0.16.0
httpcore==1.0.9
httpx==0.27.0
idna==3.20
iniconfig==2.3.0
markdown-it-py==4.2.0
-e file:///home/daniel/tt-data/mcuscope-2026-09-25/floor/src/host
mdurl==0.1.2
packaging==26.3
platformdirs==4.0.0
pluggy==1.6.0
pydantic==2.0.2
pydantic-core==2.1.2
pygments==2.21.0
pyserial==3.5
pytest==8.0.0
pytest-asyncio==0.23.5
pytest-cov==5.0.0
pytest-randomly==3.15.0
pytest-timeout==2.3.1
regex==2026.2.19
rich==15.0.0
ruff==0.13.0
shellingham==1.5.4
sniffio==1.3.1
starlette==0.44.0
tomli==2.4.1
tomlkit==0.12.0
typer==0.26.0
typing-extensions==4.16.0
uvicorn==0.35.0
websockets==14.0
```

Every direct dependency resolved to its declared floor.
Transitives are at their latest, notably click 8.5.0 against 8.4.2 in the normal `.venv`; that difference was ruled out for failure 1.

## Notes

- `~/tt-data/mcuscope-tools/sweeps/class-43/` has no README, only `run-floor.sh`.
  That script runs file by file and skips the JS and firmware wrappers, so this run did not use it. The originals are unchanged.
  - A general improvement for it: run the whole suite in one pytest call, drop the wrapper skip, and take the scratch dir as `$1`.
- Timing: a whole-suite run at the floor (Python 3.10) takes about 9 min, against about 4 min documented.
