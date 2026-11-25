# Developing MCUscope

## Setup

All host development happens in `host/`.

```bash
cd host
uv venv --python 3.13               # creates .venv on a known-good interpreter
uv pip install -e '.[dev]'          # uv venvs have no pip; use `uv pip`
```

That gives you `uv run mcu` and `uv run mcuscoped` inside `host/`.
To put a source checkout on PATH the way a released install is, run `uv tool install -e ./host` from the repository root, and add `--reinstall` after a dependency change.

## Tests and lint

```bash
uv run python -m pytest             # full suite, about 4 minutes
uv run python -m ruff check .
```

- The suite runs the whole stack against the simulator, so it needs no hardware.
- The web UI JavaScript tests need node 18 or newer, and the firmware tests a C compiler. Both skip cleanly without them.
- CI runs the suite on Linux and Windows with Python 3.10 to 3.13, and the C monitor tests under AddressSanitizer and UBSan on Linux.

`uv run` picks the venv interpreter on either OS, so the commands are the same on Linux and Windows.

## Branches

Work is committed on `develop`.
A release merges `develop` into `main` and tags `main`, so `main` always matches the version on PyPI.
[RELEASING.md](RELEASING.md) has the steps.

## Conventions

- Everything works the same on Linux and Windows. Use plain pyserial, take filesystem paths from `platformdirs` (or the `MCUSCOPE_*_DIR` overrides), and prefer `socket://` to a pty in tests.
- [SPEC.md](SPEC.md) is the design contract: when code and SPEC disagree, SPEC wins.
- A CLI change updates `mcu ai-guide` (`AI_GUIDE` in `host/mcuscope/cli.py`) and the SPEC section 4 table in the same commit; `tests/test_cli_contract.py` fails otherwise.
- Never drop or truncate captured data silently: count it or announce it.
- No em or en dashes anywhere: code, comments, docs, commit messages.
- Keep the test suite and `ruff check` green before moving on.

## Repository layout

```
host/                 Python package: mcuscoped daemon, mcu CLI, web UI, and their tests
host/contrib/         example config file and systemd user unit
firmware/monitor/     portable C monitor module, port template, integration guide
firmware/tests/       host-compiled C tests for the monitor, run from pytest
tools/                development tools; tools/mcu_sim.py is a source-checkout shim for mcuscope.sim
docs/                 user guides and design documents
```

## Where to read next

| Document | What it holds |
|---|---|
| [`CLAUDE.md`](../CLAUDE.md) | the same conventions in full, written as instructions for AI coding agents |
| [SPEC.md](SPEC.md) | the design contract: wire protocol, REST and WebSocket API, database schema, firmware contract |
| [ARCHITECTURE.md](ARCHITECTURE.md) | the module map and the design constraints behind it; read it before changing one |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | the phased plan and its status tracker |
| [IDEAS.md](IDEAS.md) | the backlog of weighed ideas, including the ones deliberately not taken |
| [DBC_DECODING.md](DBC_DECODING.md) | design note for CAN DBC decoding (designed, not scheduled) |
| [REVIEW.md](REVIEW.md), [REVIEW_LOG.md](REVIEW_LOG.md) | the review runbook and the findings of each round |
| [RELEASING.md](RELEASING.md) | the PyPI release runbook |
| [SCREENSHOTS.md](SCREENSHOTS.md) | how to refresh `docs/img/webui.png` |
| [`tools/README.md`](../tools/README.md) | the simulator shim and the web UI smoke harness |
| [`CHANGELOG.md`](../CHANGELOG.md) | what changed in each release |
