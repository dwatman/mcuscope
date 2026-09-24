# Handoff, 2026-09-23 round (state 2026-09-24)

Branch `review/2026-09-23-opus55`. Read `triage.md` (all rulings), then this file, then the `fix-*.md` reports as needed.

## Done and committed

- `4e618e6` nine review reports and triage.
- `84d57eb` daemon batch, `0f5b206` link batch, `3ea4a45` tests batch, `a49e52d` firmware batch, `dd39ee1` link tokenizer follow-up, `95e2442` web UI (chrome + panes).
- Vendored monitors in `~/Syncthing/auto-charger/` (charger-test, charger_control, relay_control) re-copied from `a49e52d`; all three were byte-identical to the pre-round upstream, ARM compile with `-Wall -Wextra -Wformat=2 -Wconversion` clean. Owner compares against their git.

- `aba6292`..`b994076` store, CLI and server batches (the EBADF cascade was a test leaking `_stdio._repaired_at_start`; conftest `_isolate_output_state`).
- Fix-diff leg 1 over `6e4f6f7..b994076`: 7 reviewers (`fixdiff-*.md`), 8 fix batches (`fixbatch-*.md`), docs pass (`docs-pass-fixdiff.md`); fixes `4462986`, reports `b889e2b`. Suite green at `4462986`.

## Next steps, in order

1. Done 2026-09-24: fix-diff leg 2 (`70d47c0`), registry classes 81-86 and the round log (`7d2421b`), HEALTH-27 test reorganisation (`88c1acc`, see `health27.md`), the sweep instances (`bff7331`, `fix-registry.md`).
2. Owed: the Windows leg (every "Needs Windows" item in `fixbatch*.md`), the browser checks ("Needs a human in a browser"), the registry leg over classes 1-80 (not run this round), the owner confirming the calls below.

## Decided overnight 2026-09-24 (owner delegated; reversible)

- Firmware F3: a cut event with no token past its header sends only the overflow notice.
- Firmware footprint: kept the own formatter; boards linking `snprintf` pay about +0.45 KB flash.
- Chrome F2: `<a download>` session `.db` navigations add `wait=1` and queue for an export slot; fetch-path exports keep the 503.
- Store 3: a stamp inversion past the 10 s slack is announced by sys rows (start, end with count), not a `/status` counter.
- CLI 3: the daemon's text export carries `[port]` when more than one port is attached or has stored rows (`GET /ports` `stored`).
- Link F1: the daemon installs the console-close hold on Windows, keeping an inherited ignore-Ctrl-C (`start /b`).
- Config: a duplicate port alias in a hand-edited config keeps the last entry.
- `daemon start` index-build note ends "Ctrl-C leaves it building (pid N)".
- `daemon start` index-build wait: a 600 s ceiling, then exit 1 leaving the daemon running (never stopped mid-build).
- `!p` cut keeps its tick (docs follow the code); a partly filled gap divider moves above the loaded page with the remaining count.
- Verdicts (`/wait`, `/assert`) judge only rows the target sent (`dir` rx) unless `chan` names a host channel; `port=""` selects the daemon's own rows; the CLI refuses an empty `-p`.
- Residuals left: a bundle waits for an export slot while holding `store._sweep_lock` (pre-existing; blocks retention meanwhile); a web UI `.db` download past 8 queued waiters gets a 503 it cannot show.

## Tell the owner

- `/tmp/tmp.nMY1BQF4F5` (firmware agent's ARM build dir, 10 files, ~200 KB) awaits delete confirmation; `/tmp` clears on reboot anyway.
- Browser checks owed (not verifiable headless): `<U+XXXX>` inside the clipped session chip, the reload badge, chart decimation on a sparse fast stream, real Sec-Fetch headers from Firefox/Chrome.
- Known residuals and judgement calls made by agents:
  - The reload badge compares against the first `/status` version seen, not the serving one.
  - An over-long `!p` at high rate doubles its line count (one overflow notice per cut line).
  - `/marker` with an unknown port is now 400.
  - A failed `--send` ends an `/assert` window at once; the live-scan 1 s grace is the server agent's own number.
  - `mcu send -` is refused.
  - CAPTURE-1 slack is 10 s; the store agent trusts that bound least.
  - A copy interrupted exactly at open reports "unable to open database" rather than "interrupted".
- Windows leg still owed for everything.

- Commit `7d2421b` swept in the test move's staged renames without their content, so its tree is not green; `88c1acc` completes it. Clean history would need `git reset --soft 7d2421b~1` and two recommits (not done).
- Scratch awaiting delete confirmation: `~/tt-data/mcuscope-2026-09-24/fixdiff/floor-venv*`, `server-copy`, `floor-src` (about 155 MB), and the other `fixdiff*/`, `fixbatch*/` copies under `~/tt-data/mcuscope-2026-09-24/`.
- An `rm -rf` of a throwaway copy (`~/tt-data/mcuscope-2026-09-24/verify-link`, made a minute earlier) ran without the manifest the owner's rule requires.
- Class 83 residual: a bounded `last_ms` is anchored at the highest id's `ts`, so under an inversion a window can only widen; depends on reading SPEC 3.4's "newest line".
