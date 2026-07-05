# firmware/monitor

The portable, RTOS-free MCU monitor module (SPEC section 5). Handles the UART line
protocol (parse, dispatch, responses, `!can`/`!p`/plot events) and calls out to a small
set of bus shims the integrating project implements. C99, no dynamic allocation, no
HAL/LL/CMSIS in the core, static buffers only.

Files:

- `monitor.h` - public API + shim declarations (the contract).
- `monitor.c` - core: line assembly, parse, dispatch, response/event formatting,
  typed plot streams, CAN RX drain.
- `monitor_cmds.c` - built-in v1 command handlers (can/i2c/spi/gpio/adc/ping/info)
  plus weak default shims so unimplemented buses answer `ERR 7 nosup`.
- `port_template/monitor_port_template.c` - copy to `monitor_port.c` and fill in the
  three port callbacks plus the buses your board has.
- `INTEGRATION.md` - step-by-step integration into a bare-metal LL superloop project.

Tests: `../tests/` is a host-compiled (gcc) unit suite driving the core through fake
shims. Build and run with `make -C firmware/tests run`; it is also wired into the Python
suite via `host/tests/test_firmware_monitor.py`.
