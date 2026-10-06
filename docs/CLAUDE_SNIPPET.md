# CLAUDE_SNIPPET.md

Paste the block below into `~/.claude/CLAUDE.md` (or a project `CLAUDE.md`) so Claude Code knows the hardware debug bridge is available and how to drive it.

The examples quote with double quotes on purpose: an agent may run them through `cmd.exe` on Windows, where single quotes are not quoting characters and would reach `mcu` as part of the argument.

```markdown
## Hardware debug bridge (MCUscope)

A local serial-to-hardware bridge is available via the `mcu` CLI (daemon `mcuscoped` on 127.0.0.1:8558).
Use it to talk to the attached MCU.

- Check it first: `mcu status` (exit 3 means the daemon is not running).
- Learn the full interface on demand: `mcu ai-guide` (read its PITFALLS block first).
- Typical loop:
  - `mcu cmd "<command>"` to send and get the response;
  - `mcu wait --match <regex>` to block until an async line arrives (`--send "<command>"` sends first);
  - `mcu lines`/`mcu tail` to query the capture.
- Wrap a test run in `mcu session start <name>` / `mcu session stop`, then query just that run with `mcu lines --session <name>` instead of guessing at time windows.
- Decide pass/fail on an exit code rather than by reading the log:
  - `mcu assert --session <name> --expect "CALIB DONE" --forbid "ERR|retry"` judges a stored run (exit 0 pass, 1 fail);
  - `mcu assert --timeout MS --expect ...` (no `--session`) judges a live window instead.
- Always pass `--json` for machine-readable output.
- Exit codes: 0 ok/match, 1 error (including a daemon that stopped answering), 2 a timeout the board or the wait reported, 3 daemon unreachable.
- Pitfalls:
  - With more than one port attached, every write (`cmd`, `send`, `wait`/`assert --send`, bus commands) needs `-p <alias>`; reads without `-p` span every port.
  - `wait` and `assert` judge only lines the board sent: their own command, markers and sys notices never match or count, so match the board's reply, not the command text.
  - A `--send` the monitor refuses or never answers ends `wait` at once with `send_failed`, exit 1.
  - A verdict over a window that held no lines is `empty`, exit 1 (a `--forbid` over nothing proves nothing); `--allow-empty` accepts it. One whose window lost lines to shedding is `incomplete`, exit 1; retry it.
  - `-p <alias>` reads also show the daemon's own rows (markers made without `-p`, session boundaries); `wait`/`assert` do not, so mark with `mcu -p <alias> mark ...`.
  - With `--json`, an error carries `kind` (`no_such_port`, `port_disconnected`, `usage`, ...) to act on instead of the text.
  - `mcu lines --since-id N --limit M` pages forward: the next M rows above id N, not the newest; while it reports `truncated`, call again from the newest id returned.
```
