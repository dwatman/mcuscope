# Integrating the monitor into a bare-metal STM32 project

This guide wires the portable monitor into an existing LL (or register-level) superloop project: no RTOS, no HAL dependency in the monitor itself, one UART for the debug link.
It assumes you already have a working `main()` with a `while (1)` loop and a millisecond tick (SysTick).

The monitor is deliberately dumb about hardware.
It handles the line protocol (parse, dispatch, response and event formatting, typed plot streams) and calls out to a handful of shims you implement against your own drivers.
Buses you do not wire up answer `ERR 7 nosup` automatically.

## 1. Files to add

Copy into your project (or add the directory to your include/source paths):

- `monitor.h`         - the contract. Include it where you integrate.
- `monitor.c`         - core. Compile it. Do not edit.
- `monitor_cmds.c`    - built-in commands + weak default shims. Compile it. Do not edit.
- `monitor_port.c`    - **yours**. Start from `port_template/monitor_port_template.c`.

Add `monitor.c` and `monitor_cmds.c` to your build with the same C99 flags as the rest of your firmware.
A target with more than one CAN controller adds `-DMON_CAN_BUSES=2` (1 to 9, default 1; in CMake, `target_compile_definitions(... MON_CAN_BUSES=2)`).
Prefer the flag over editing the define in `monitor.h`, so the copied files stay identical to upstream and a later re-copy is a plain overwrite.
They pull in only `<stdint.h>`, `<stddef.h>`, `<stdbool.h>`, `<stdarg.h>`, `<stdio.h>` (for snprintf on the cold paths) and `<string.h>`.
No HAL, no LL, no CMSIS.
Budget is roughly 4 KB flash and under 1 KB RAM.

> snprintf/vsnprintf are used only for responses and `monitor_eventf`.
> If you pass a `%f` to `monitor_eventf` you will drag in the soft-float printf; the monitor's own code never does.
> The plot hot path (`monitor_plot`) uses no printf at all.

## 2. The three mandatory port callbacks

Fill these in in your `monitor_port.c`.
They sit on top of a **DMA + IRQ circular-buffer UART driver** (the recommended setup) or any non-blocking ring-buffer UART.

### Hardware assumptions in the examples

The examples in this guide use the peripherals most STM32 parts have, since that is what most ports will meet.
Some families renamed or redesigned those peripherals.
None of this changes the monitor contract; only the driver code behind the shims differs.

| The examples assume | Some families instead have | Seen on |
|---|---|---|
| Classic DMA: bytes remaining in `CNDTR`, circular is a mode bit | GPDMA / LPDMA: bytes remaining in `CBR1.BNDT`, circular is a linked-list node whose `CLLR` points back at itself | U5, H5, C5 |
| bxCAN: `CANx_RX0_IRQHandler`, read the FIFO0 mailbox | FDCAN: `FDCANx_IT0_IRQHandler`, or the HAL `HAL_FDCAN_RxFifo0Callback()` | G0, G4, L5, H7, U5, H5, C5 |
| USART without FIFO: interrupt on `RXNE` | USART with a FIFO: interrupt on `RXFNE`, and drain in a `while` loop rather than reading one byte | most parts released since roughly 2018 |

If your part is in the middle column the shim bodies differ, but their signatures and contracts do not.
Check the reference manual rather than assuming, since the naming is not a reliable guide to which generation you have.

### `size_t uart_read(uint8_t *buf, size_t max)`

Copy up to `max` bytes out of your RX ring and return how many you copied (0 when empty).
Must not block.
With DMA RX into a circular buffer, this is the gap between your "bytes consumed" index and the DMA's current write position, derived from the channel's bytes-remaining register (`CNDTR` on classic DMA, `CBR1.BNDT` on GPDMA/LPDMA).

Interrupt-driven RX into a ring is equally valid and is often the simpler choice.
The monitor never needs a per-byte interrupt: `monitor_poll()` reads whatever has accumulated, so RX only has to end up in a ring by the time you poll.
Plain RX-interrupt is worth preferring when the link carries mostly commands rather than bulk input, or when the part's DMA makes a circular receive awkward to set up.

Clear the USART error flags (`ORE`, `FE`, `NE`, `PE`) in the same ISR, unconditionally.
An uncleared `ORE` latches `RXNE` off on most STM32 parts, so one overrun (or a BREAK, which also sets `FE`) makes the monitor permanently deaf while `monitor_poll` keeps running and TX keeps working: `LL_USART_ClearFlag_ORE(USART1)` and friends, or read `ISR` then write `ICR`.

```c
static size_t port_uart_read(uint8_t *buf, size_t max) {
    return uart_rx_ring_read(buf, max);   // your driver
}
```

### `bool uart_write(const uint8_t *buf, size_t len)`

Enqueue `len` bytes (one complete line, including the trailing `\n`) into your TX ring **atomically**: either the whole line goes in or none of it does.
Return `false` if it does not fit right now; the monitor drops that line and increments a counter you can read with `monitor_tx_dropped()`.
Kick off TX (DMA or TXE IRQ) if idle.

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

**Line atomicity is a hard requirement.** The monitor emits each response/event as one `uart_write`.
If your application also prints debug text over the same UART, that printf must likewise enqueue whole lines atomically, or a response can end up interleaved mid-line and the daemon will misparse it.
The simplest safe rule: **all writers to the debug UART push complete `\n`-terminated lines through the same ring buffer, guarding the enqueue with a short critical section** (disable the TX IRQ / `__disable_irq()` around the ring update).
Application debug lines must not begin with `<` or `!` (those first characters are reserved for responses and events).

### `uint32_t tick_ms(void)`

Return a free-running millisecond counter.
It wraps at 2^32; the monitor's timers use unsigned subtraction so the wrap is handled.

`tick_ms` is the one callback you may leave NULL, at a cost in two places: `monitor_mark()` emits no `@tick` (the host stamps arrival time instead, and marker text starting with a tick sigil is refused), and the 5 s `!pd` rebroadcast falls back to re-emitting every `MON_PLOT_PD_POLLS` calls to `monitor_poll()`, which is a poll count and not a period.
Wire up a tick unless you truly have none.

```c
static volatile uint32_t g_systick_ms;   // written by SysTick_Handler

static uint32_t port_tick_ms(void) {
    return g_systick_ms;
}
```

The `volatile` is not optional.
The counter is written by an ISR and read from the main loop, so without it the compiler may hoist the load out of a `while (tick_ms() - t0 < n)` loop and spin forever at `-O2` - a port that passes every smoke test and then hangs.
The same applies to your RX ring's head/tail indices behind `uart_read`: mark them `volatile`, and if an index is wider than the core's atomic word, guard it with a critical section.

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

`monitor_poll()` does three things per call: drain some RX and dispatch at most one command, drain the CAN RX queue into `!can` events (up to 64 frames per poll), and rebroadcast any active plot definitions when 5 s have elapsed.
Keep calling it every loop pass; there is no interrupt or callback into the monitor.

`monitor_init()` resets line assembly, the plot-stream registry, and the `monitor_tx_dropped()` counter.
It does **not** clear the application command registry (`monitor_register`) or the software CAN filter; both persist across a re-init, so re-initializing is not a way to clear them.

Handlers may block briefly (a few milliseconds of bus timeout) inside the superloop; that is accepted for v1.
Keep it short so `application_step()` still runs often enough.

### Parser behavior worth knowing

- A command line is at most 255 bytes of content plus the LF (SPEC 2.1).
  An over-length line is discarded whole; if its seq was still parseable the monitor answers `ERR 8 overflow`, otherwise it stays silent.
- A line containing an embedded NUL or a non-ASCII byte is rejected whole with `ERR 2 badarg` (again only if a seq was parseable).
- A command may carry at most 12 tokens total (seq + command + arguments). More is `ERR 2 badarg`, never a silently truncated argv.
- Lines not starting with `>` are ignored, so other host-side traffic passes through harmlessly.

## 4. Bus shims

Implement only the buses your board has.
Each shim has a weak default in `monitor_cmds.c` returning `MONITOR_ERR_NOSUP`, so an unimplemented bus answers `ERR 7 nosup` with no work from you.
Map your driver's errors onto the shared codes: `nack` (5) for I2C no-ACK, `buserr` (4) for a bus fault / failed CAN TX, `timeout` (3) for a stuck bus, `busy` (6) to ask the caller to retry, `badarg` (2) for an unknown name/channel.

### Weak-symbol portability

The defaults in `monitor_cmds.c` are declared `MON_WEAK`, a macro in `monitor.h` that picks the right spelling for your toolchain: `__attribute__((weak))` on GCC/Clang, `__weak` on IAR (`__ICCARM__`) and Keil ARMCC5 (`__CC_ARM`).
On any other toolchain `MON_WEAK` expands to nothing, which makes the defaults ordinary strong symbols - providing your own `mon_can_tx` (or any other shim with a default) alongside them would then fail to link as a duplicate symbol.

If you are on such a toolchain, use the `#ifdef`-selected stub alternative that SPEC 5.3 already allows instead of relying on `MON_WEAK`: guard the specific default definitions you need to override behind a `#ifndef MON_HAVE_<BUS>` you control in your build of `monitor_cmds.c`, and provide only your real implementation in `monitor_port.c`.
This is a deliberate, documented exception to "do not edit monitor_cmds.c" for toolchains that cannot express weak symbols at all; leave a comment at the edit site noting why, so a future update of the shared file does not silently clobber it.

### I2C (`mon_i2c_xfer`)

One combined write-then-read entry point covers scan, write, read, and register-read:

- `wr_len == 0 && rd_len == 0` -> **address probe** (this is how `i2c scan` works): return `0` if the address ACKs, `MONITOR_ERR_NACK` otherwise.
- `wr_len > 0, rd_len == 0` -> plain write.
- `wr_len == 0, rd_len > 0` -> plain read.
- `wr_len > 0, rd_len > 0`  -> write, **repeated start**, read.

Reads are capped at 64 bytes by the command layer; write payloads are bounded by the 255-byte command line (roughly 119 bytes of hex data in practice).

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

Full-duplex transfer of `len` bytes with a named chip-select asserted around the whole transfer.
Resolve `cs_name` against your own table; reject an unknown one with `MONITOR_ERR_BADARG`.
Fill `rx` with `len` MISO bytes: returning 0 having filled fewer publishes the rest as if it were bus data.
The monitor zeroes `rx` (and I2C's `rd`) before the call so a short fill cannot leak stack residue, but zeros are not a short-read signal, so report the failure rather than relying on them.

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
For ADC, set `*raw` to the converted count and `*mv` to millivolts if you can compute it, otherwise leave `*mv = INT32_MIN` and the monitor reports `raw` only.

### Info extras (`mon_info_extra`)

Optional: append space-separated tokens to the `info` response (`rst=por fw=1.2.3`), return 0.
Write at most `max` bytes **including a NUL terminator** (`snprintf(buf, max, ...)` is the safe form; a `memcpy` of `max` bytes is not).
The monitor passes one byte less than its own buffer and terminates the last byte itself, so it cannot be walked off the end by a shim that fills everything it is offered.

### CAN (`mon_can_tx`, `mon_can_rx_pop`, `mon_can_filter`, `mon_can_stat`)

CAN is the one bus where **interrupt context and main-loop context meet**, so it needs a small queue.
The rule (SPEC 2.5) is that events are only ever emitted from the main loop: the RX IRQ pushes frames into a ring, and the monitor drains that ring during `monitor_poll()` via `mon_can_rx_pop`.

```c
// A tiny lock-free-ish SPSC ring: IRQ is the sole producer, main loop the sole consumer.
#define CAN_RX_DEPTH 16
static volatile mon_can_frame_t can_rx[CAN_RX_DEPTH];
static volatile uint8_t can_rx_head, can_rx_tail;
// g_systick_ms below is the SysTick counter declared in section 2. Both snippets belong in
// the same monitor_port.c, so declare it once there; this section does not redeclare it.

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

`volatile` carries this ring on a single core only: it stops the compiler reordering the payload store past the index publish, and ISR entry synchronises the rest.
On a dual-core part (an M7+M4 H7, an M33+M0 pairing) where the producer runs on the other core, put a `DMB` between writing `can_rx[head]` and writing `can_rx_head`, and another between reading the index and reading the entry.

`mon_can_rx_pop` need only set the fields the mailbox gives it: the monitor zeroes the frame before every call, so an untouched `tick_ms`, `ext` or `rtr` reads as 0 rather than as leftovers, and an untouched `bus` as bus 1.
It also masks the emitted id to the width the flags declare (11 bits, or 29 with `ext`), because the host refuses a wider one, but the shim still owns id validity.

The handler above is bxCAN.
On an FDCAN part the producer half becomes `FDCANx_IT0_IRQHandler` with the RX FIFO0 new-message interrupt enabled, or the HAL `HAL_FDCAN_RxFifo0Callback()` if you let the vendor generate the handler.
Configure FDCAN for classic frame format: the shim carries no BRS or FD-length field, and `mon_can_tx` is defined as one classic frame.
Everything from the ring downwards is identical either way.

`mon_can_tx` queues one classic frame on controller `f->bus` (map a full-mailbox condition to `MONITOR_ERR_BUSY` and a TX-error to `MONITOR_ERR_BUSERR`).
`mon_can_filter` may program a hardware filter on `bus` or just return `0`: the monitor keeps its own software id/mask filter per bus and applies it on drain regardless, so a no-op hardware filter is fine.
`mon_can_stat` reports `rx/tx/err` counters and the controller state string (`"active"`, `"passive"`, or `"busoff"`) for `bus`.
`bus` is always 1..`MON_CAN_BUSES` when a shim sees it; the monitor has already refused anything else with `ERR 2 badarg`, so a single-bus shim can ignore the argument.
The counters are cumulative since init and free-running (wrap is fine, resetting on read is not), and the state is the controller's current state, not a worst-seen latch (SPEC 2.4 pins both; a real bench firmware got this wrong in a way the host cannot detect).

`mon_can_tx` is also the right place for any bus-specific pacing your target needs.
Some devices specify a minimum period between requests, and `can tx` is defined as returning once the frame is *queued*, so a port may enqueue into its own paced ring and release to the peripheral on a timer without violating the contract.
Enforcing it here rather than host-side means it holds no matter what drives the bus.
Map a full paced queue to `MONITOR_ERR_BUSY` exactly as for a full mailbox.

### Two controllers

Build with `-DMON_CAN_BUSES=2` (section 1) and the host addresses them as `can tx` / `can2 tx`, `!can` / `!can2` (SPEC 2.4).
The ring above stays a single ring: each ISR tags the frame with its bus, and everything downstream routes on `f->bus`.
bxCAN on an F4 (CAN1 plus CAN2):

```c
// One ring for both controllers; the frame carries the bus it arrived on.
static void can_rx_push(uint8_t bus, CAN_TypeDef *can) {
    mon_can_frame_t f;
    bxcan_read_fifo0(can, &f);     // fill id/dlc/data/ext/rtr from the mailbox
    f.tick_ms = g_systick_ms;
    f.bus = bus;
    uint8_t next = (uint8_t)((can_rx_head + 1) % CAN_RX_DEPTH);
    if (next != can_rx_tail) {
        can_rx[can_rx_head] = f;
        can_rx_head = next;
    }
    bxcan_release_fifo0(can);
}
void CAN1_RX0_IRQHandler(void) { can_rx_push(1, CAN1); }
void CAN2_RX0_IRQHandler(void) { can_rx_push(2, CAN2); }

static CAN_TypeDef *can_of(uint8_t bus) { return bus == 2 ? CAN2 : CAN1; }
static uint32_t can_rx_count[2], can_tx_count[2], can_err_count[2];   // index bus-1

int mon_can_tx(const mon_can_frame_t *f) {
    int rc = bxcan_send(can_of(f->bus), f);   // ERR_BUSY on no free mailbox, ERR_BUSERR on fault
    if (rc == 0) can_tx_count[f->bus - 1]++;
    return rc;
}
int mon_can_filter(uint8_t bus, uint32_t id, uint32_t mask, bool ext) {
    // Filter banks are shared: CAN1 owns banks 0..CAN2SB-1, CAN2 owns CAN2SB..27
    // (CAN_FMR.CAN2SB, `SlaveStartFilterBank` in HAL). Program one bank per bus.
    return bxcan_set_filter(can_of(bus), bus == 2 ? CAN2_FIRST_BANK : 0, id, mask, ext);
}
int mon_can_stat(uint8_t bus, uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state) {
    *rx = can_rx_count[bus - 1]; *tx = can_tx_count[bus - 1]; *err = can_err_count[bus - 1];
    *state = bxcan_state_string(can_of(bus));   // "active" / "passive" / "busoff"
    return 0;
}
```

Two bxCAN traps that read as "CAN2 is dead": CAN2 is the slave controller and uses CAN1's SRAM, so CAN1's clock must be enabled (and CAN1 initialised first) even if CAN1 is otherwise unused; and with `CAN2SB` left at its reset value CAN2 sees no filter banks at all, so it accepts nothing.

On an FDCAN part (a C0 or G0 with two instances) the shape is the same with `FDCAN1_IT0_IRQHandler` and `FDCAN2_IT0_IRQHandler` as the two producers, or `HAL_FDCAN_RxFifo0Callback(hfdcan, ...)` with `hfdcan->Instance` deciding the bus.
Each FDCAN instance has its own message RAM and filter list, so there is no shared-bank split to configure.

A target with one controller changes nothing: leave `MON_CAN_BUSES` at 1, never set `f->bus`, and ignore the `bus` argument.

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

`resp_max` is the size of the buffer, not the size of a sendable payload.
The response goes out as `<SEQ OK <payload>\n`, and that prefix costs up to 10 bytes, so a handler that fills `resp_max` produces a line the emitter can only answer with `ERR 8 overflow` - it will never truncate a payload, because that could cut a hex pair in half.
If your payload is variable length, clamp it to `MON_OK_PAYLOAD_MAX`.

`monitor_register` returns `false` on a duplicate name or a full table.
Built-in commands are matched first, so a custom command cannot shadow `ping` or `can tx`.
The name string is stored by pointer, not copied: pass a string literal or other permanent storage.

### Typed plot streams

Stream signals without float printf using typed plot streams.
Define the layout once and feed a packed little-endian struct; the monitor emits big-endian hex and rebroadcasts the definition every 5 s:

```c
static const mon_plot_def_t imu = {
    .sid = '0',
    .body = "ax:s2*0.00098:g ay:s2*0.00098:g az:s2*0.00098:g",
};
struct __attribute__((packed)) { int16_t ax, ay, az; } s = { ax, ay, az };
monitor_plot(&imu, tick_ms(), &s, sizeof s);
```

Registration happens implicitly on the first `monitor_plot` call for a sid, and is subject to these rules:

- At most **4 concurrent streams**, each with at most **16 fields**; the body (plus the `!pd X ` prefix) must fit the 255-byte line limit.
  A bad body, a full table, or a `len` that does not match the summed field sizes returns `MONITOR_ERR_BADARG` (a failed first call leaves no registration behind).
  The first rejection of a sid also emits `!e plot <sid> badarg def|body|len|full` once (`full`: all 4 slots taken; a NULL body or a sid outside `'0'..'9'` gives `!e plot ? badarg sid`), so a stream that never appears on the host says why (`mcu lines --match "^!e"`); check the return value anyway.
- Channel names and bit-lane names share one namespace per stream: `vbat:u2 io:u1:/vbat,relay` is rejected (`def`).
- The `!pd` definition line is emitted together with the first valid sample, then re-emitted every 5 s while the stream stays registered, so a daemon that connects late can still decode the stream.
- `def->body` is cached **by pointer, not copied**. It must stay valid for the life of the stream: a string literal or other static storage, never a stack buffer.
- Re-registering a sid with a **different** body is `MONITOR_ERR_BADARG`; calling again with the same body is the normal streaming case.
  `monitor_init()` clears the registry if you truly need to redefine a stream.

The hot path (after the first call per stream) is a length check, a nibble-lookup hex encode into a static buffer, and one `uart_write`: no printf, no division, no allocation.

Note: the `!ps` contract is a **packed little-endian struct**.
Passing a native struct like the example above is only correct on little-endian targets (Cortex-M in its usual configuration, x86 hosts).
On a big-endian target, serialize each field into a byte buffer little-endian first and pass that buffer instead.

### Enum and packed-bits channels

The `<unit>` slot after the second `:` may instead carry a `<kind>` sigil (SPEC 2.5).
No firmware code change is needed to use either kind, but the body is not passed through blind: the monitor validates the whole field, tail included (scale, unit charset, enum items, bit lanes, and name uniqueness across channels and lanes), and `monitor_plot` returns `MONITOR_ERR_BADARG` for anything the host's definition parser would refuse.
So a mistyped label, a 17-character name, a `*scale` on an enum channel, or a 9th lane on a `u1` fails at the first call rather than registering a stream whose samples the host files as generic events forever.

- **Enum/state**: `=<v>=<label>,<v>=<label>,...` maps each raw decoded integer to a label for display, e.g. a one-byte state field:
  ```c
  static const mon_plot_def_t st = { .sid = '4', .body = "state:u1:=0=IDLE,1=ARMED,4=RUN" };
  uint8_t state = 1;
  monitor_plot(&st, tick_ms(), &state, sizeof state);
  ```
  Integer types only, not `f4`.
- **Packed bits**: `/<lane>,<lane>,...` expands the raw integer into one 0/1 channel per lane, LSB-first, e.g.:
  ```c
  static const mon_plot_def_t gp = { .sid = '5', .body = "gpio:u1:/led,irq,pwm_en" };
  uint8_t gpio = 0x05;
  monitor_plot(&gp, tick_ms(), &gpio, sizeof gpio);
  ```
  Unsigned integer types only, not `f4` or signed types.

Both forms count against the same 255-byte line limit as any other `!pd` (SPEC 2.1): the whole line, including every field's name, type, and any enum labels or bit lane names, must fit.
A long list of enum labels or bit lanes on a stream with several fields can push the line over the limit; keep labels short if you are close to it.

For throwaway "watch one variable" debugging, `monitor_eventf("p %lu v=%ld", tick, v)` emits an ad-hoc `!p` line.
`monitor_eventf` output beyond the 255-byte limit is truncated, not dropped.

### Markers

`monitor_mark("calibration start")` annotates the timeline: the host draws it as a full-width divider in the terminal, next to `mcu mark` and the session boundaries.

```c
monitor_mark("calibration start");      // -> "!m @<tick> calibration start"
```

- The MCU tick comes from your port's `tick_ms()` automatically, so the call takes text and nothing else.
- Text is free-form and may be built at runtime. It is sanitized on the way out like every other line, so an embedded newline cannot forge a second protocol line.
- It returns 0, or `MONITOR_ERR_BADARG` for text that emits nothing: NULL or empty, and (only on a port with no `tick_ms`) text whose first word is an `@<digits>` tick sigil, which the host would read back as a tick nobody set.

## 6. Manual smoke checklist (against real hardware)

1. Flash the firmware, connect the debug UART to the host (USB-serial or ST-Link VCP).
2. `mcuscoped -c config.toml` with the port pointing at your device (`device = "COM7"` or `"/dev/ttyACM0"`, `baud = 115200`).
3. `mcu status` -> the port shows `connected`, `rx` climbing if you print anything.
4. `mcu cmd ping` -> `monitor 1 myboard`.
5. `mcu cmd info` -> `up=<ms> ...`.
6. `mcu cmd 'i2c scan'` -> the addresses that actually ACK on your bus.
7. Exercise one command per implemented bus (`i2c rd`, `spi xfer`, `gpio set/get`, `adc read`, `can tx`).
8. If CAN is wired: send a frame from another node -> `mcu can dump` shows the decoded `!can` event with the right id and payload; check `mcu cmd 'can stat'`.
   - Silence here is more often the board than the firmware.
     Most CAN transceivers have an STBY or EN pin: confirm the GPIO driving it actually puts the part in normal mode, because a transceiver left asleep gives you a controller that looks perfectly healthy and a bus that never moves.
     Then confirm termination is 120 Ω at both ends.
   - Internal loopback mode is worth one run first. It exercises `can tx` all the way to the `!can` event without the transceiver or the bus, so a failure there is unambiguously firmware.
9. If you emit plot data: `mcu log export` shows `!pd`/`!ps` (or `!p`) lines flowing, and a fresh daemon start sees a `!pd` within ~5 s.
10. Unplug and replug the UART: the daemon reconnects and capture resumes with no restart.
