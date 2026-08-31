// monitor_port_template.c - copy this into your project as monitor_port.c and fill it
// in against your own drivers. Everything here is a stub: every bus shim returns
// MONITOR_ERR_NOSUP, so out of the box the monitor answers "ERR 7 nosup" to every bus
// command while `ping`/`info` and the line protocol already work. Implement only the
// buses you actually have; delete the stubs you replace (or leave them: your strong
// definition overrides the weak default in monitor_cmds.c either way).
//
// See INTEGRATION.md for wiring details. The three port callbacks (uart_read,
// uart_write, tick_ms) are the only mandatory glue; they sit on top of your UART
// circular-buffer driver and your millisecond tick.

#include "../monitor.h"

// =====================================================================================
// 1. Port callbacks (MANDATORY) - back these with your UART ring buffer and systick.
// =====================================================================================

// TODO: pull up to `max` bytes out of your UART RX ring buffer (non-blocking).
//       Return the number of bytes copied (0 if the ring is empty). Never block.
static size_t port_uart_read(uint8_t *buf, size_t max) {
	(void)buf; (void)max;
	// Example against a ring: return uart_rx_read(buf, max);
	return 0;
}

// TODO: push `len` bytes (one complete line, includes the trailing '\n') into your
//       UART TX ring buffer *atomically*. Return false if the whole line does not fit
//       right now (the monitor then drops it). Do NOT partially enqueue: line atomicity
//       is what keeps responses/events from being interleaved with the app's printf.
static bool port_uart_write(const uint8_t *buf, size_t len) {
	(void)buf; (void)len;
	// Example: return uart_tx_write_atomic(buf, len);
	return false;
}

// TODO: return a free-running millisecond counter (e.g. from SysTick). Wraps at 2^32,
//       which the monitor handles.
static uint32_t port_tick_ms(void) {
	// Example: return HAL-free systick_ms();
	return 0;
}

static const monitor_port_t g_port = {
	.uart_read  = port_uart_read,
	.uart_write = port_uart_write,
	.tick_ms    = port_tick_ms,
	.name       = "template",     // TODO: short project id shown by `ping`
};

// Call this once from your startup code, then call monitor_poll() every superloop pass.
void monitor_port_init(void) {
	monitor_init(&g_port);
}

// =====================================================================================
// 2. Bus shims (OPTIONAL) - define only the buses your board has.
//
// Each function below is already provided as a weak default in monitor_cmds.c returning
// MONITOR_ERR_NOSUP. Uncomment and implement the ones you need; a strong definition here
// overrides the weak one. Delete the rest.
// =====================================================================================

#if 0   // ---- CAN (bxCAN or FDCAN in classic mode) ----------------------------------
// TODO: queue one classic CAN frame for transmission. Map driver errors to MONITOR_ERR_*
//       (ERR_BUSERR on TX failure, ERR_BUSY if all mailboxes are full, ERR_TIMEOUT).
int mon_can_tx(const mon_can_frame_t *f) {
	(void)f;
	return MONITOR_ERR_NOSUP;
}
// TODO: pop one received frame from the queue your RX IRQ fills. Return false when empty.
//       Set f->tick_ms to the reception time. The monitor drains this during poll and
//       emits "!can" events - IRQ context never touches the monitor.
//       With more than one controller (MON_CAN_BUSES > 1) set f->bus to 1..N; a single-bus
//       shim leaves it alone. On TX, f->bus says which controller to send on.
bool mon_can_rx_pop(mon_can_frame_t *f) {
	(void)f;
	return false;
}
// TODO: program a receive filter on `bus` if your hardware supports it. A pure software
//       filter is also fine (the monitor keeps its own id/mask per bus and filters on
//       drain regardless).
int mon_can_filter(uint8_t bus, uint32_t id, uint32_t mask, bool ext) {
	(void)bus; (void)id; (void)mask; (void)ext;
	return MONITOR_ERR_NOSUP;
}
// TODO: report counters and controller state ("active"/"passive"/"busoff") for `bus`.
//       rx/tx/err count since init and are never reset by a read (SPEC 2.4); state is
//       the controller's current state, not the worst seen.
int mon_can_stat(uint8_t bus, uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state) {
	(void)bus; (void)rx; (void)tx; (void)err; (void)state;
	return MONITOR_ERR_NOSUP;
}
#endif  // CAN

#if 0   // ---- I2C (master) --------------------------------------------------------
// TODO: combined write-then-read against a 7-bit address.
//   wr_len 0 && rd_len 0  -> address probe: return 0 if the device ACKs, else ERR_NACK.
//                            `i2c scan` relies on exactly this convention.
//   wr_len >0, rd_len 0   -> plain write.
//   wr_len 0, rd_len >0   -> plain read.
//   wr_len >0, rd_len >0  -> write, repeated start, read (register-read idiom).
// Map a NACK to MONITOR_ERR_NACK, a bus fault to MONITOR_ERR_BUSERR, a stuck bus to
// MONITOR_ERR_TIMEOUT.
int mon_i2c_xfer(uint8_t addr7, const uint8_t *wr, size_t wr_len,
				 uint8_t *rd, size_t rd_len) {
	(void)addr7; (void)wr; (void)wr_len; (void)rd; (void)rd_len;
	return MONITOR_ERR_NOSUP;
}
#endif  // I2C

#if 0   // ---- SPI (master) --------------------------------------------------------
// TODO: full-duplex transfer of `len` bytes with chip-select `cs_name` asserted around
//       the whole transfer. `cs_name` indexes your own CS table (e.g. "imu"); reject an
//       unknown name with MONITOR_ERR_BADARG. rx must be filled with `len` MISO bytes.
int mon_spi_xfer(const char *cs_name, const uint8_t *tx, uint8_t *rx, size_t len) {
	(void)cs_name; (void)tx; (void)rx; (void)len;
	return MONITOR_ERR_NOSUP;
}
#endif  // SPI

#if 0   // ---- GPIO ----------------------------------------------------------------
// TODO: drive / read a named pin from your own pin table. Unknown name -> ERR_BADARG.
int mon_gpio_set(const char *name, bool level) {
	(void)name; (void)level;
	return MONITOR_ERR_NOSUP;
}
int mon_gpio_get(const char *name, bool *level) {
	(void)name; (void)level;
	return MONITOR_ERR_NOSUP;
}
#endif  // GPIO

#if 0   // ---- ADC -----------------------------------------------------------------
// TODO: read a named ADC channel. Set *raw to the converted count. Set *mv to the
//       millivolt value if you can compute it, else leave it as INT32_MIN and the
//       monitor reports raw only. Unknown name -> ERR_BADARG.
int mon_adc_read(const char *name, int32_t *raw, int32_t *mv) {
	(void)name; (void)raw;
	*mv = INT32_MIN;
	return MONITOR_ERR_NOSUP;
}
#endif  // ADC

#if 0   // ---- info extras ---------------------------------------------------------
// TODO: append optional space-separated tokens to the `info` response, e.g.
//       "rst=por fw=1.2.3". Return 0 on success. Unknown/unused: leave the weak default.
// Must NUL-terminate within `max` (snprintf(buf, max, ...) does; memcpy of max bytes does
// not). The monitor passes one byte less than its own buffer and terminates the last byte
// itself, but a shim that ignores `max` still overruns.
int mon_info_extra(char *buf, size_t max) {
	(void)buf; (void)max;
	return MONITOR_ERR_NOSUP;
}
#endif  // info extras
