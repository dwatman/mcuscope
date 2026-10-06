# Fix-diff 2, outer (cli and edge), 2026-10-05

Run by the orchestrator directly: the reviewer agent's launch was denied by the auto-mode classifier with the owner away.
Scope: the second fix round's changes in `fix2-cli.md` and `fix2-edge.md`.

## Findings

### FD2-OUTER-1 LOW SUSPECTED (Windows): `-o NUL` warns that the export may be left partial

- `cli_output.py` `AtomicOut.__init__`: if `os.stat("NUL")` raises on Windows, `st` is None, so the device is treated as a new regular file.
- `mkstemp` then fails in the device namespace, and the fallback opens `NUL` directly. The output is correct, but the user gets "no temporary file beside NUL ... an interrupted export leaves it partial".
- Confirm on Windows: `mcu lines -o NUL`. Fix if confirmed: treat a reserved device name (`os.path.isreserved` on 3.13+, or the `NUL`/`CON` set) as a non-regular target before `mkstemp`.

## Checked and fine

- `AtomicOut`: a symlink is resolved and its target replaced; a FIFO or device is written in place; a directory target raises naming the path asked for; a failed rename names the target and `discard()` removes the temp (clearing a read-only bit first); a replaced file keeps the old file's mode, a new one gets 0666 less the umask.
- `-p` check in `settings_of`: every `ctx.obj` reader goes through `settings_of` (grep); the commands with no direct call (`cmd`, `can tx/stat/filter`, `i2c`, `spi`, `gpio`, `adc`) reach it through `_run_cmd`; `--help` asks nothing (`test_help_after_an_unknown_p_asks_no_daemon`).
- `purge -p` refusal: `test_purge_refuses_any_p_before_asking_the_daemon` fails with the refusal removed (2 failed), passes restored (32 passed in the file).
- `die_daemon`: 33 sites, all `die(msg, 1, "daemon_error")`; exit codes at the stop paths match 5eaaeeb (`nothing to stop`, stale pid record: exit 1). `daemon_error` rather than `unreachable` there is SPEC-consistent: SPEC 4 ties `unreachable` to exit 3.
- Name cap in `SerialPort` (`serial_link.py:985-1025`): the set never exceeds `ADHOC_NAMES_MAX`; a name given back after a failed store while a later row with it was stored is re-admitted the next time it is seen, so the batch's "leak" is bounded and only follows an announced store failure.
- Firmware with the `g_ovf_send_hook` guard deleted: `make run` and `make asan` 323/323. `plot_grammar.test.mjs` 5 pass.

## Not covered

- Windows behaviour of `AtomicOut` (rename over an open target, read-only temp removal).
- A live browser run of the refused-`!p` path (FD-EDGE-2 awaits the owner's pick).

## The two questions

1. Least confident: FD2-OUTER-1 is reasoned, not driven; the Windows `os.stat` behaviour on `NUL` decides it.
2. Not thought about: whether `settings_of`'s one-shot `ctx.meta` flag survives a command that invokes another command through `ctx.invoke`; none does today (grep for `ctx.invoke` finds no command-to-command call that reads settings first).
