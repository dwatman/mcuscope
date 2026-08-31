# fix-batch-1

HEAD confirmed 8028f03. Nothing committed. The two pre-existing uncommitted edits in `host/mcuscope/__init__.py` and `host/mcuscope/config.py` were left untouched.

## Per file

- **docs/SPEC.md** - two edits: `Python >= 3.11` -> `Python >= 3.10`; dependency list gained `` `tomlkit` `` after `` `websockets` ``.
- **host/README.md** - line 18 `Requires Python 3.11 or newer.` -> `3.10 or newer.` Grep for other `3.11` in that file: none remain (the only hit was the one edited).
- **host/tests/test_protocol.py** - added `import json` (isort order: after `contextlib`, before `random`) and `test_lineclass_stringifies_as_its_value` immediately after `test_classify_tolerates_terminator`. Import alias `p` matched the file's existing `from mcuscope import protocol as p`.
- **CHANGELOG.md** - second sub-bullet added under the Python 3.10 bullet in `[Unreleased]` / `Changed`.
- **docs/ARCHITECTURE.md** - sentence added under the `serial_link.py` entry, as a continuation line of the existing "On command timeout the pending entry is popped" bullet, which was the natural anchor (no `send_command`/`timeout_ms` string exists in the file).

## Deviation from the brief

Ruff rejected the test as written: `UP031 Use format specifiers instead of percent format` on `assert "%s" % p.LineClass.EVENT == "event"`. The `%s` path is the point of that assertion, so it carries a targeted suppression rather than being rewritten:

```python
    assert "%s" % p.LineClass.EVENT == "event"  # noqa: UP031 - %s is the path under test
```

## Results

- 3.13 (`uv run python -m pytest -q -p no:cacheprovider tests/test_protocol.py`): 206 passed.
- 3.10 (`/tmp/mcu310/bin/python -m pytest ...`): 206 passed.
- **Test bites: yes.** With `__str__` deleted from `LineClass`, the new test FAILED on 3.13 at line 52 (`str(p.LineClass.EVENT)` produced `LineClass.EVENT`). `host/mcuscope/protocol.py` restored with `git checkout --`; it was confirmed clean beforehand and is absent from `git status --short` after.
- **Ruff: clean.** `uv run python -m ruff check .` -> All checks passed.

## git diff --stat

```
 CHANGELOG.md                |  1 +
 docs/ARCHITECTURE.md        |  1 +
 docs/SPEC.md                |  4 ++--
 host/README.md              |  2 +-
 host/mcuscope/__init__.py   |  8 ++++----
 host/mcuscope/config.py     |  4 ++--
 host/tests/test_protocol.py | 10 ++++++++++
 7 files changed, 21 insertions(+), 9 deletions(-)
```
