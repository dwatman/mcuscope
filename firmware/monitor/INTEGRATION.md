# Integrating the monitor into a bare-metal STM32 project

This guide wires the portable monitor into an existing LL (or register-level) superloop
project: no RTOS, no HAL dependency in the monitor itself, one UART for the debug link.
It assumes you already have a working `main()` with a `while (1)` loop and a millisecond
tick (SysTick).

The monitor is deliberately dumb about hardware. It handles the line protocol (parse,
dispatch, response and event formatting, typed plot streams) and calls out to a handful
of shims you implement against your own drivers. Buses you do not wire up answer
`ERR 7 nosup` automatically.

## 1. Files to add

Copy into your project (or add the directory to your include/source paths):

- `monitor.h`         - the contract. Include it where you integrate.
- `monitor.c`         - core. Compile it. Do not edit.
- `monitor_cmds.c`    - built-in commands + weak default shims. Compile it. Do not edit.
- `monitor_port.c`    - **yours**. Start from `port_template/monitor_port_template.c`.

Add `monitor.c` and `monitor_cmds.c` to your build with the same C99 flags as the rest
of your firmware. They pull in only `<stdint.h>`, `<stddef.h>`, `<stdbool.h>`,
`<stdarg.h>`, `<stdio.h>` (for snprintf on the cold paths) and `<string.h>`. No HAL, no
LL, no CMSIS. Budget is roughly 4 KB flash and under 1 KB RAM.

> snprintf/vsnprintf are used only for responses and `monitor_eventf`. If you pass a
> `%f` to `monitor_eventf` you will drag in the soft-float printf; the monitor's own
> code never does. The plot hot path (`monitor_plot`) uses no printf at all.

## 2. The three mandatory port callbacks

Fill these in in your `monitor_port.c`. They sit on top of a **DMA + IRQ circular-buffer
UART driver** (the recommended setup) or any non-blocking ring-buffer UART.

### `size_t uart_read(uint8_t *buf, size_t max)`

Copy up to `max` bytes out of your RX ring and return how many you copied (0 when
empty). Must not block. With DMA RX into a circular buffer, this is the gap between your
"bytes consumed" index and the current `DMA_CNDTR`-derived head.

```c
static size_t port_uart_read(uint8_t *buf, size_t max) {
    return uart_rx_ring_read(buf, max);   // your driver
}
```

### `bool uart_write(const uint8_t *buf, size_t len)`

Enqueue `len` bytes (one complete line, including the trailing `\n`) into your TX ring
**atomically**: either the whole line goes in or none of it does (return `false` if it
does not fit right now; the monitor drops that line and counts it, see
`monitor_tx_dropped()`). Kick off TX (DMA or TXE IRQ) if idle.

```c
static bool port_uart_write(const uint8_t *buf, size_t len) {
    if (uart_tx_ring_space() < len) {
        return false;          // do not partially enqueue
    }
    uart_tx_ring_write(buf, len);
    uart_tx_start_if_idle();
    return true;
}
```

**Line atomicity is a hard requirement.** The monitor emits each response/event as one
`uart_write`. If your application also prints debug text over the same UART, that printf
must likewise enqueue whole lines atomically, or a response can end up interleaved
mid-line and the daemon will misparse it. The simplest safe rule: **all writers to the
debug UART push complete `\n`-terminated lines through the same ring buffer, guarding the
enqueue with a short critical section** (disable the TX IRQ / `__disable_irq()` around
the ring update).

### `uint32_t tick_ms(void)`

Return a free-running millisecond counter. It wraps at 2^32; the monitor's timers use
unsigned subtraction so the wrap is handled.

```c
static uint32_t port_tick_ms(void) {
    return g_systick_ms;   // incremented in SysTick_Handler
}
```

## 3. Init and poll

```c
#include "monitor.h"

static const monitor_port_t port = {
    .uart_read  = port_uart_read,
    .uart_write = port_uart_write,
    .tick_ms    = port_tick_ms,
    .name       = "myboard",   // shown by `ping`
};

int main(void) {
    clocks_init();
    uart_init();
    // ... your init ...
    monitor_init(&port);

    while (1) {
        monitor_poll();        // cheap when idle; dispatches at most one command/pass
        application_step();    // your work
    }
}
```

`monitor_poll()` does three things per call: drain some RX and dispatch at most one
command, drain the CAN RX queue into `!can` events, and rebroadcast any active plot
definitions when 5 s have elapsed. Keep calling it every loop pass; there is no interrupt
or callback into the monitor.

Handlers may block briefly (a few milliseconds of bus timeout) inside the superloop; that
is accepted for v1. Keep it short so `application_step()` still runs often enough.

## 4. Bus shims

Implement only the buses your board has. Each shim has a weak default in
`monitor_cmds.c` returning `MONITOR_ERR_NOSUP`, so an unimplemented bus answers
`ERR 7 nosup` with no work from you. Map your driver's errors onto the shared codes:
`nack` (5) for I2C no-ACK, `buserr` (4) for a bus fault / failed CAN TX, `timeout` (3)
for a stuck bus, `busy` (6) to ask the caller to retry, `badarg` (2) for an unknown
name/channel.

### Weak-symbol portability

The defaults in `monitor_cmds.c` are declared `MON_WEAK`, a macro in `monitor.h` that
picks the right spelling for your toolchain: `__attribute__((weak))` on GCC/Clang,
`__weak` on IAR (`__ICCARM__`) and Keil ARMCC5 (`__CC_ARM`). On any other toolchain
`MON_WEAK` expands to nothing, which makes the defaults ordinary strong symbols -
providing your own `mon_can_tx` (or any other shim with a default) alongside them
would then fail to link as a duplicate symbol.

If you are on such a toolchain, use the `#ifdef`-selected stub alternative that
SPEC 5.3 already allows instead of relying on `MON_WEAK`: keep your own project-local
copy of the specific default definitions you need to override commented out (or
guarded behind a `#ifndef MON_HAVE_<BUS>` you control) in your build of
`monitor_cmds.c`, and provide only your real implementation in `monitor_port.c`. This
is a deliberate, documented exception to "do not edit monitor_cmds.c" for toolchains
that cannot express weak symbols at all; leave a comment at the edit site noting why,
so a future update of the shared file does not silently clobber it.

### I2C (`mon_i2c_xfer`)

One combined write-then-read entry point covers scan, write, read, and register-read:

- `wr_len == 0 && rd_len == 0` -> **address probe** (this is how `i2c scan` works):
  return `0` if the address ACKs, `MONITOR_ERR_NACK` otherwise.
- `wr_len > 0, rd_len == 0` -> plain write.
- `wr_len == 0, rd_len > 0` -> plain read.
- `wr_len > 0, rd_len > 0`  -> write, **repeated start**, read.

LL sketch (blocking, polled, STM32 I2Cv2 peripheral):

```c
int mon_i2c_xfer(uint8_t addr7, const uint8_t *wr, size_t wr_len,
                 uint8_t *rd, size_t rd_len) {
    uint8_t a = (uint8_t)(addr7 << 1);
    if (wr_len == 0 && rd_len == 0) {
        // Probe: START + address, check for ACK, STOP.
        return i2c_probe(a) ? 0 : MONITOR_ERR_NACK;
    }
    if (wr_len) {
        if (!i2c_write(a, wr, wr_len, /*stop=*/rd_len == 0)) {
            return MONITOR_ERR_NACK;   // or TIMEOUT on a stuck bus
        }
    }
    if (rd_len) {
        if (!i2c_read(a, rd, rd_len)) {
            return MONITOR_ERR_NACK;
        }
    }
    return 0;
}
```

### SPI (`mon_spi_xfer`)

Full-duplex transfer of `len` bytes with a named chip-select asserted around the whole
transfer. Resolve `cs_name` against your own table; reject an unknown one with
`MONITOR_ERR_BADARG`. Fill `rx` with `len` MISO bytes.

```c
int mon_spi_xfer(const char *cs_name, const uint8_t *tx, uint8_t *rx, size_t len) {
    const cs_pin_t *cs = cs_lookup(cs_name);
    if (!cs) return MONITOR_ERR_BADARG;
    cs_assert(cs);
    for (size_t i = 0; i < len; i++) {
        LL_SPI_TransmitData8(SPI1, tx[i]);
        while (!LL_SPI_IsActiveFlag_RXNE(SPI1)) { }
        rx[i] = LL_SPI_ReceiveData8(SPI1);
    }
    cs_deassert(cs);
    return 0;
}
```

### GPIO (`mon_gpio_set` / `mon_gpio_get`) and ADC (`mon_adc_read`)

Look the name up in your own pin/channel table; unknown name -> `MONITOR_ERR_BADARG`.
For ADC, set `*raw` to the converted count and `*mv` to millivolts if you can compute it,
otherwise leave `*mv = INT32_MIN` and the monitor reports `raw` only.

### CAN (`mon_can_tx`, `mon_can_rx_pop`, `mon_can_filter`, `mon_can_stat`)

CAN is the one bus where **interrupt context and main-loop context meet**, so it needs a
small queue. The rule (SPEC 2.5) is that events are only ever emitted from the main loop:
the RX IRQ pushes frames into a ring, and the monitor drains that ring during
`monitor_poll()` via `mon_can_rx_pop`.

```c
// A tiny lock-free-ish SPSC ring: IRQ is the sole producer, main loop the sole consumer.
#define CAN_RX_DEPTH 16
static volatile mon_can_frame_t can_rx[CAN_RX_DEPTH];
static volatile uint8_t can_rx_head, can_rx_tail;

// --- IRQ context: bxCAN FIFO0 message-pending handler ---
void CAN1_RX0_IRQHandler(void) {
    mon_can_frame_t f;
    bxcan_read_fifo0(&f);          // fill id/dlc/data/ext/rtr from the mailbox
    f.tick_ms = g_systick_ms;      // stamp reception time
    uint8_t next = (uint8_t)((can_rx_head + 1) % CAN_RX_DEPTH);
    if (next != can_rx_tail) {     // drop on full rather than overwrite
        can_rx[can_rx_head] = f;
        can_rx_head = next;
    }
    bxcan_release_fifo0();
}

// --- main-loop context: called by the monitor during poll ---
bool mon_can_rx_pop(mon_can_frame_t *f) {
    if (can_rx_tail == can_rx_head) {
        return false;
    }
    *f = can_rx[can_rx_tail];
    can_rx_tail = (uint8_t)((can_rx_tail + 1) % CAN_RX_DEPTH);
    return true;
}
```

`mon_can_tx` queues one classic frame (map a full-mailbox condition to `MONITOR_ERR_BUSY`
and a TX-error to `MONITOR_ERR_BUSERR`). `mon_can_filter` may program a hardware filter or
just return `0`: the monitor keeps its own software id/mask filter and applies it on drain
regardless, so a no-op hardware filter is fine. `mon_can_stat` reports `rx/tx/err`
counters and the controller state string (`"active"`, `"passive"`, or `"busoff"`).

## 5. Optional: custom commands and plot streams

Add project-specific commands (matched on the first token) from application code:

```c
static int cmd_calibrate(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv;
    run_calibration();
    snprintf(resp, resp_max, "done");   // becomes "<seq OK done"
    return 0;
}
monitor_register("calibrate", cmd_calibrate);   // up to 8 extra commands
```

Stream signals without float printf using typed plot streams. Define the layout once and
feed a packed little-endian struct; the monitor emits big-endian hex and rebroadcasts the
definition every 5 s:

```c
static const mon_plot_def_t imu = {
    .sid = '0',
    .body = "ax:s2*0.00098:g ay:s2*0.00098:g az:s2*0.00098:g",
};
struct __attribute__((packed)) { int16_t ax, ay, az; } s = { ax, ay, az };
monitor_plot(&imu, tick_ms(), &s, sizeof s);
```

Note: the `!ps` contract is a **packed little-endian struct**. Passing a native struct
like the example above is only correct on little-endian targets (Cortex-M in its usual
configuration, x86 hosts). On a big-endian target, serialize each field into a byte
buffer little-endian first and pass that buffer instead.

### Enum and packed-bits channels

The `<unit>` slot after the second `:` may instead carry a `<kind>` sigil (SPEC 2.5). The
monitor's body parser does not care: `parse_plot_body` only reads the two-character
`<type>` token right after the field's first `:` to compute that field's byte width, then
skips ahead to the next space. Everything after the type, including a `=...`/`/...` kind
sigil, rides through into the emitted `!pd` line untouched, so no firmware code change is
needed to use either kind.

- **Enum/state**: `=<v>=<label>,<v>=<label>,...` maps each raw decoded integer to a
  label for display, e.g. a one-byte state field:
  ```c
  static const mon_plot_def_t st = { .sid = '4', .body = "state:u1:=0=IDLE,1=ARMED,4=RUN" };
  uint8_t state = 1;
  monitor_plot(&st, tick_ms(), &state, sizeof state);
  ```
  Integer types only, not `f4`.
- **Packed bits**: `/<lane>,<lane>,...` expands the raw integer into one 0/1 channel per
  lane, LSB-first, e.g.:
  ```c
  static const mon_plot_def_t gp = { .sid = '5', .body = "gpio:u1:/led,irq,pwm_en" };
  uint8_t gpio = 0x05;
  monitor_plot(&gp, tick_ms(), &gpio, sizeof gpio);
  ```
  Unsigned integer types only, not `f4` or signed types.

Both forms count against the same 255-byte line limit as any other `!pd` (SPEC 2.1): the
whole line, including every field's name, type, and any enum labels or bit lane names,
must fit. A long list of enum labels or bit lanes on a stream with several fields can push
the line over the limit; keep labels short if you are close to it.

For throwaway "watch one variable" debugging, `monitor_eventf("p %lu v=%ld", tick, v)`
emits an ad-hoc `!p` line.

## 6. Manual smoke checklist (against real hardware)

1. Flash the firmware, connect the debug UART to the host (USB-serial or ST-Link VCP).
2. `mcuscoped -c config.toml` with the port pointing at your device
   (`device = "COM7"` or `"/dev/ttyACM0"`, `baud = 115200`).
3. `mcu status` -> the port shows `connected`, `rx` climbing if you print anything.
4. `mcu cmd ping` -> `monitor 1 myboard`.
5. `mcu cmd info` -> `up=<ms> ...`.
6. `mcu cmd 'i2c scan'` -> the addresses that actually ACK on your bus.
7. Exercise one command per implemented bus (`i2c rd`, `spi xfer`, `gpio set/get`,
   `adc read`, `can tx`).
8. If CAN is wired: send a frame from another node -> `mcu can dump` shows the decoded
   `!can` event with the right id and payload; check `mcu cmd 'can stat'`.
9. If you emit plot data: `mcu log export` shows `!pd`/`!ps` (or `!p`) lines flowing, and
   a fresh daemon start sees a `!pd` within ~5 s.
10. Unplug and replug the UART: the daemon reconnects and capture resumes with no restart.
