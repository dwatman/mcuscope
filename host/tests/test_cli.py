"""CLI tests: drive the `mcu` entry point as a subprocess against a live daemon.

Uses `python -m mcuscope.cli` (equivalent to the installed `mcu` console script) with
MCUSCOPE_URL pointed at the per-test stack, so the real exit-code contract and --json
output shapes are exercised end to end. Cross-platform.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable

from tests.support import Stack

MCU = [sys.executable, "-m", "mcuscope.cli"]


def run_mcu(
    stack: Stack | None, *args: str, url: str | None = None, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MCUSCOPE_URL"] = url if url is not None else (stack.base_url if stack else "")
    return subprocess.run(
        [*MCU, *args], capture_output=True, text=True, env=env, timeout=timeout
    )


# -- exit-code contract ---------------------------------------------------------------


def test_cmd_ok_exit0_prints_data(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "i2c scan")
    assert r.returncode == 0
    assert r.stdout.strip() == "48 50"


def test_cmd_err_exit1_stderr(stack: Stack) -> None:
    r = run_mcu(stack, "cmd", "gpio get nope")
    assert r.returncode == 1
    assert "badarg" in r.stderr


def test_cmd_timeout_exit2(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--drop-response", "1"])
    r = run_mcu(stack, "cmd", "ping", "--timeout", "500")
    assert r.returncode == 2
    assert "timeout" in r.stderr


def test_unreachable_exit3() -> None:
    r = run_mcu(None, "status", url="http://127.0.0.1:1")
    assert r.returncode == 3
    assert r.stderr.strip()  # one-line message on stderr


def test_bad_usage_exit1() -> None:
    r = run_mcu(None, "no-such-command", url="http://127.0.0.1:1")
    assert r.returncode == 1


# -- --json output shapes -------------------------------------------------------------


def test_cmd_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "cmd", "i2c scan")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and obj["data"] == "48 50"
    assert isinstance(obj["line_id"], int)


def test_status_json_shape(stack: Stack) -> None:
    obj = json.loads(run_mcu(stack, "--json", "status").stdout)
    assert obj["ports"][0]["connected"] is True
    assert "db_size_bytes" in obj


def test_lines_json_shape(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "lines", "--last-ms", "100000", "--limit", "5")
    obj = json.loads(r.stdout)
    assert "lines" in obj and "truncated" in obj


def test_wait_json_match(stack: Stack) -> None:
    r = run_mcu(stack, "--json", "wait", "--match", "^!can", "--timeout", "2000")
    assert r.returncode == 0
    assert json.loads(r.stdout)["status"] == "match"


def test_wait_timeout_exit2(stack: Stack) -> None:
    r = run_mcu(stack, "wait", "--match", "ZZZ_NEVER", "--timeout", "300")
    assert r.returncode == 2


def test_can_dump_json(stack: Stack) -> None:
    obj = None
    for _ in range(30):
        obj = json.loads(run_mcu(stack, "--json", "can", "dump", "--id", "100", "-n", "5").stdout)
        if obj["frames"]:
            break
        time.sleep(0.1)
    assert obj and obj["frames"]
    assert obj["frames"][0]["can_id"] == 0x100


# -- i2c sugar: --reg maps to wrrd ----------------------------------------------------


def test_i2c_rd_trailing_json(stack: Stack) -> None:
    # The SPEC acceptance form puts --json last: `mcu i2c rd 48 2 --json`.
    r = run_mcu(stack, "i2c", "rd", "48", "2", "--json")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["status"] == "ok" and len(obj["data"]) == 4  # two temp bytes


def test_i2c_reg_maps_to_wrrd(stack: Stack) -> None:
    # Write CAFE at EEPROM offset 0x10, then read it back via a register read (--reg).
    assert run_mcu(stack, "i2c", "wr", "50", "10CAFE").returncode == 0
    obj = json.loads(run_mcu(stack, "--json", "i2c", "rd", "50", "2", "--reg", "10").stdout)
    assert obj["status"] == "ok" and obj["data"] == "CAFE"


# -- ai-guide (no daemon needed) ------------------------------------------------------


def test_ai_guide() -> None:
    r = subprocess.run([*MCU, "ai-guide"], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0
    assert "EXIT CODES" in r.stdout
    assert "--json" in r.stdout


# -- plot channels / export (SPEC 9.2) ------------------------------------------------


def _wait_plot_names(stack: Stack, need: set[str], tries: int = 60) -> dict[str, dict]:
    by_name: dict[str, dict] = {}
    for _ in range(tries):
        obj = json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)
        by_name = {ch["name"]: ch for ch in obj["channels"]}
        if need <= set(by_name):
            break
        time.sleep(0.1)
    return by_name


def test_plot_channels_json(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    by_name = _wait_plot_names(stack, {"tri", "sine"})
    assert by_name["tri"]["sid"] == "0" and by_name["tri"]["unit"] == "V"
    assert by_name["sine"]["sid"] is None


def test_plot_export_wide_csv(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    for _ in range(60):
        by_name = {ch["name"]: ch for ch in
                   json.loads(run_mcu(stack, "--json", "plot", "channels").stdout)["channels"]}
        if by_name.get("tri", {}).get("count", 0) >= 10:
            break
        time.sleep(0.1)
    r = run_mcu(stack, "plot", "export", "--names", "tri,ramp,ftest", "--wide")
    assert r.returncode == 0
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "ts,tick_ms,tri,ramp,ftest"
    assert len(lines) >= 2


def test_plot_export_wide_mixed_streams_exit1(make_stack: Callable[..., Stack]) -> None:
    stack = make_stack(["--plot"])
    _wait_plot_names(stack, {"tri", "sine"})
    r = run_mcu(stack, "plot", "export", "--names", "sine,tri", "--wide")
    assert r.returncode == 1
    assert "error" in r.stderr
