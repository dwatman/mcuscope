# firmware/monitor

The portable MCU monitor module for [MCUscope](https://github.com/dwatman/mcuscope) (specified in section 5 of [SPEC.md](https://github.com/dwatman/mcuscope/blob/main/docs/SPEC.md)).
It handles the UART line protocol (parse, dispatch, responses, `!can`, `!p` and typed plot events) and calls a small set of shims that your project implements.

- C99, no RTOS, no dynamic allocation, static buffers only.
- No HAL, LL or CMSIS in the core.

To add it to a project, follow [INTEGRATION.md](INTEGRATION.md).

## Files

| File | What it is |
|---|---|
| `monitor.h` | The public API and the shim declarations: the contract. |
| `monitor.c` | The core: line assembly, parse, dispatch, response and event formatting, typed plot streams, CAN RX drain. |
| `monitor_cmds.c` | The built-in command handlers (`can`, `i2c`, `spi`, `gpio`, `adc`, `ping`, `info`) and weak default shims, so an unimplemented bus answers `ERR 7 nosup`. |
| `port_template/monitor_port_template.c` | Copy to `monitor_port.c`, then fill in the three port callbacks (UART read, UART write, millisecond tick) and the buses your board has. |
| `INTEGRATION.md` | Step-by-step integration into a bare-metal superloop project: build flags (`MON_CAN_BUSES`, `MON_NO_<FAMILY>`), measured footprint, stack budget. |

## Tests

`../tests/` is a host-compiled (gcc) unit suite that drives the core through fake shims.

| Command | What it runs |
|---|---|
| `make -C firmware/tests run` | the suite |
| `make -C firmware/tests asan` | the suite under AddressSanitizer and UBSan |
| `make -C firmware/tests families` | the suite once per `MON_NO_<FAMILY>` flag |
| `make -C firmware/tests families-asan` | the same under the sanitizers |

All four are wired into the Python suite through `host/tests/test_firmware_monitor.py`.
