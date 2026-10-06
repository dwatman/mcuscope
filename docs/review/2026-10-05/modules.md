# Modules leg, 2026-10-05

Picked by fewest commits and the REVIEW_LOG "Not read" lines (count, last touched).

| Module | Commits | Last | Why |
|---|---|---|---|
| `lockfile.py` | 3 | 2026-08-28 | REVIEW_LOG 1253: "Not read" |
| `dirs.py` | 2 | 2026-09-26 | fewest commits |
| `render.py` | 3 | 2026-09-24 | fewest commits |
| `cli_argv.py` | 4 | 2026-09-24 | few commits; class 5 lives here |
| `pjstream.py` | 6 | 2026-09-25 | few commits, a network sink |
| `update_check.py` | 11 | 2026-09-25 | REVIEW_LOG 1921: "Not read" |
| `app.js`, `theme.js` | 25, 4 | 2026-09-24, 08-11 | REVIEW_LOG 1670: "Not read" |
| `layout.js`, `freeze.js`, `exportrange.js` | 3, 6, 5 | 2026-09 | fewest commits in webui |

All read in full.

## MODULES-1 HIGH CONFIRMED: a symlinked db_path defeats the capture lock; two daemons write one capture

`host/mcuscope/lockfile.py:115` (`self.path = db_path + ".lock"`), fed by `config.resolve_db_path` (`config.py:155`), which uses `os.path.abspath` and never resolves symlinks.

- Failure: daemon A with `db_path = real.db`, daemon B with `db_path = link.db` (a symlink to `real.db`). The locks are `real.db.lock` and `link.db.lock`, two different files, so both acquire.
  - Both then write the same SQLite file. Every batch fails `UNIQUE constraint failed: lines.id` and goes row by row (285 warnings each in about 20 s); `write_errors` stays 0 on both, so `/status` shows nothing wrong.
  - The capture ends up with both daemons' rows interleaved under one alias (`sim`: 3024 rows, ids 1..3031 contiguous), and B's start closed A's open session (`sessions` 1 ends where 2 begins). Nothing in the record says which daemon wrote a row.
- A symlink on a *directory* in the path is safe (the lock path resolves through it); only a symlink to the file itself, or a hard link, slips through. Likelihood is low; the outcome is the corruption the lock exists to prevent.
- Repro (scratch `~/tt-data/mcuscope-2026-10-05/modules/`): in-process, `CaptureLock(S+'real.db').acquire()` then `CaptureLock(S+'link.db').acquire()` succeeds; `dirlink/real.db` and `./real.db` are refused. Daemons: `a.toml` (port 18680, `alias/real.db`) and `b.toml` (18681, `alias/link.db`), both `--sim`; logs in `run/a.log`, `run/b.log`; capture `alias/real.db`.
- Fix: lock `os.path.realpath(db_path) + ".lock"` (in `CaptureLock.__init__`, so every caller gets it); keep `db_path` as given for messages. Hard links stay uncovered; say so in the module docstring. A test: symlink to the db, second acquire must raise `LockError`.
- Class: none fits exactly; nearest is 52 (an artefact keyed differently from the thing it guards). Candidate new class: "a guard keyed on a path spelling rather than the file's identity".

## MODULES-2 MEDIUM CONFIRMED (Linux) / SUSPECTED (Windows): `db_path = ":memory:"` takes a lock on a file named `:memory:.lock` in the CWD

`host/mcuscope/daemon.py:449` (`CaptureLock(resolve_db_path(config))`), with `resolve_db_path` returning `":memory:"` verbatim (`config.py:160`).

- Failure (Linux, confirmed): two daemons with `db_path = ":memory:"` started from the same directory. Each has its own private in-memory database, but the second is refused with "capture database is already in use by another mcuscoped: :memory:", and a stray `:memory:.lock` file is left in whatever directory the user ran it from.
- Failure (Windows, suspected): `:` is not legal in a Windows file name, so `os.open(":memory:.lock")` should fail and `daemon.main` returns 1 with "cannot claim :memory:.lock". That would mean the SPEC 3.3 `:memory:` option never starts on Windows. Confirm with one `mcuscoped -c` run on the Windows desktop.
- Repro: `m.toml` (18682) and `m2.toml` (18683), both `db_path = ":memory:"`, started from `run/`; `run/m2.log` holds the refusal and `run/:memory:.lock` the stray file.
- Fix: skip the lock when `db_path == ":memory:"` (nothing is shared, so there is nothing to guard). `server.py:2859` and `store.py` already special-case `":memory:"` and `""`.
- Class: 52 (a per-daemon artefact keyed by something that is not per-daemon).

## MODULES-3 MEDIUM CONFIRMED: a narrower window leaves the sidebar at its old width; the right part, collapse button included, is clipped off-screen

`host/mcuscope/webui/app.js:55-67`: `applySideWidth` clamps the stored width to the workspace only at load, on a drag, a key nudge or a double-click. Nothing re-runs it on a window resize (`terminal.js:936` only calls `scheduleResizeRedraw`).

- Failure: drag the sidebar wide (saved `sideW: 1200` in a 1600 px window), then make the window 900 px wide. `--side-w` stays `1200px`. The grid is `minmax(320px,1fr) 6px var(--side-w)` inside `overflow: hidden`, so the sidebar runs from x=326 to x=1526 in a 900 px workspace, and `#collapseBtn` sits at x=1516, unreachable. Charts lose their right 626 px. The state lasts until a reload or a new drag.
  - Expanded mode (60 percent share) has the same root cause: the share does not follow a window resize either, despite the `layout.js:44` comment "expanded is a share, not a width, so it follows the window".
- Repro: `~/tt-data/mcuscope-2026-10-05/modules/resize.py` against a `--sim` daemon on 18684 (`ui.toml`). Output:
  - `1600 wide, saved 1200: sideRight 1600, collapseBtnRight 1590`
  - `after shrink to 900: wsRight 900, sideRight 1526, sideW 1200px, collapseBtnRight 1516`
  - `reload at 900: sideRight 900, sideW 574px` (the load-time clamp works)
- Fix: call `applySideWidth()` from a window `resize` listener in `app.js` (the clamp is cheap; the redraw is already rAF-coalesced). A JS test can drive it through `sideWidthFor` only; the DOM half is a manual or Playwright check.
- Class: none; nearest is 76 (a view derived from an input, here the window width, that is not re-read when the input changes).

## MODULES-4 LOW SUSPECTED: the divider drags never end on `pointercancel` or `lostpointercapture`

`host/mcuscope/webui/app.js:102-124` and `151-172`: `dragging` / `cpDragging` are cleared only in `pointerup`.

- Failure (reasoned): a touch drag on `#resizer` (no `touch-action: none` anywhere in `style.css`) lets the browser take the gesture as a pan and fire `pointercancel` instead of `pointerup`. `dragging` stays true and the `drag` class stays, so later pointer moves keep resizing the sidebar with no button held, and the width is never saved.
- Confirm: Playwright with a touch context (`has_touch=True`), a touch drag on `#resizer`, then a plain move; or dispatch `pointercancel` after a real `pointerdown`.
- Fix: one shared end handler on `pointerup`, `pointercancel` and `lostpointercapture`; `touch-action: none` on both dividers.
- Class: none.

## Checked and fine

- `pjstream._resolve` refuses IPv4-mapped multicast and unspecified (`[::ffff:224.0.0.251]`, `[::ffff:0.0.0.0]`, `[::]`, `[ff02::1]`) on 3.13; `ipaddress` on 3.10.20 and 3.12.11 classifies the mapped forms the same (probe in `uv run --no-project --python 3.10/3.12`).
- `pjstream`: `[::ffff:255.255.255.255]` passes `_resolve`, but `sendto` from the non-broadcast socket fails with EACCES on Linux, so it is inert, as the docstring says of directed broadcast. Windows not checked.
- `cli_argv`: walked all 142 params of the click tree. No `count`, `nargs>1` or optional-value option, and no subcommand option shadows `--json`/`--port`/`-p`/`--url`/`--token`/`--version`, so the value guard's "non-flag takes exactly one value" premise holds today (`walk_opts.py`).
- `cli_argv.split_global_opts` on `lines --match -psim --json`, `lines --limit --json 5`, `lines --limit -p`, `cmd -- --json`, `--json=1 status`, `-p sim -p other status`: values stay with their options, `--` stops hoisting.
- `render.fmt_ts`: 2000 timestamps one to five ulps below a whole second near 1.76e9 never print the previous second with `.000`; negative ts prints the floor second with the right milliseconds. Out-of-range or NaN ts raises, but stored ts are host `time.time()` values.
- `update_check`: the badge uses `textContent` and requires `available`, which needs `parse_version`, so a hand-edited cache `latest` cannot reach the DOM as markup.
- `lockfile`: the same db spelled `./real.db` or through a directory symlink is refused (in-process probe above).
- `layout.parseLayout`, `parseTitles`, `exportrange.validate`, `freeze.js` latch, `theme.js`: read; nothing beyond hand-edited-localStorage nits (`exportrange.validate` turns a stored `session: ""` or `NaN` into a `session=` param the daemon would refuse).

## Not covered

- Windows: MODULES-2's Windows half, `lockfile`'s `msvcrt` branch, `dirs.retry_sharing`.
- MODULES-4 not driven.
- `update_check` network paths not driven (MockTransport tests exist); proxy and redirect behaviour reasoned only.
- Hard links to the capture (MODULES-1 fix leaves them open).

### Modules deliberately not read (starting map for the next round)

Ordered by commit count, then size:

- Python: `__init__.py` (9), `link.py` (11), `cli_client.py` (12), `pidfile.py` (13; REVIEW_LOG 1921 "Not read"), `cli_daemonctl.py` (14), `cli_output.py` (16), `_stdio.py` (17; "Not read"), `config.py` (33).
- Web UI: `chrome.js` (12), `timewindow.js` (14), `exportdlg.js` (15), `pane.js` (18), `cmdbar.js` (19), `can.js` (23), `settings.js` (34), and the large, often-reviewed `terminal.js`, `statusbar.js`, `state.js`, `api.js`, `plots.js` (REVIEW_LOG 1921 lists the first four plus `plots.js`'s rendering half as "Not read").

## The two questions

1. Least confident: MODULES-1's severity. Re-driven by reading the shared capture after the run: 3031 rows, both daemons' rows under one `sim` alias, A's session closed by B's start, `write_errors` 0 on both. That holds HIGH on outcome; the precondition (a file symlink) is uncommon. MODULES-2's Windows half and MODULES-4 are reasoned, not driven.
2. Not thought about: whatever else keys on the db_path *string*. The pid record and the `files` notice print it, and the export and session paths compare `":memory:"`/`""` literally. A sweep for `db_path` comparisons and keys (`grep -n "db_path" host/mcuscope/*.py`) would show whether anything else treats two spellings of one file as two captures.

## Scratch

`~/tt-data/mcuscope-2026-10-05/modules/` (1.4 MB): configs `a/b/m/m2/ui.toml`, `alias/` (real.db, link.db symlink, dirlink, lock files), `run/` (daemon logs, pid files, `:memory:.lock`), `data/ cfg/ cache/` (env overrides), `ui.db`, probes `walk_opts.py` and `resize.py`. All daemons stopped (checked with `ps`); the Playwright browser closed itself and used no persistent profile.
