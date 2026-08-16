# CLAUDE_SNIPPET.md

Paste the block below into `~/.claude/CLAUDE.md` (or a project `CLAUDE.md`) so Claude Code knows the hardware debug bridge is available and how to drive it.

The examples quote with double quotes on purpose: an agent may run them through `cmd.exe` on Windows, where single quotes are not quoting characters and would reach `mcu` as part of the argument.

```markdown
## Hardware debug bridge (MCUscope)

A local serial-to-hardware bridge is available via the `mcu` CLI (daemon `mcuscoped` on
127.0.0.1:8765). Use it to talk to the attached MCU.

- Check it first: `mcu status` (exit 3 means the daemon is not running).
- Learn the full interface on demand: `mcu ai-guide`.
- Typical loop: `mcu cmd "<command>"` to send and get the response, `mcu wait --match <regex>`
  to send-and-wait for an async line, `mcu lines`/`mcu tail` to query the capture.
- Wrap a test run in `mcu session start <name>` / `mcu session stop`, then query just
  that run with `mcu lines --session <name>` instead of guessing at time windows.
- Decide pass/fail on an exit code rather than by reading the log:
  `mcu assert --session <name> --expect "CALIB DONE" --forbid "ERR|retry"`
  (exit 0 pass, 1 fail; add `--timeout MS` to judge a live window instead of a stored one).
- Always pass `--json` for machine-readable output. Exit codes: 0 ok/match, 1 error,
  2 timeout, 3 daemon unreachable.
```
