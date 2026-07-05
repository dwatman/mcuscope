# firmware/monitor

The portable, RTOS-free STM32 monitor module (SPEC section 5). Implemented in phase 4.

Planned files:

- `monitor.h` - public API + shim declarations (the contract).
- `monitor.c` - core: line assembly, parse, dispatch, response/event formatting.
- `monitor_cmds.c` - built-in v1 command handlers (can/i2c/spi/gpio/adc/ping/info).
- `port_template/monitor_port_template.c` - every shim stubbed with TODOs.
- `INTEGRATION.md` - step-by-step integration into an existing STM32 LL project.
