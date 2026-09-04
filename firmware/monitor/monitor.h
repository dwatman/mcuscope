// monitor.h - portable UART debug monitor: public API + port/shim contract.
// Contract and wire protocol: docs/SPEC.md sections 2 and 5 in the MCUscope repo.
//
// Core rules: C99, no dynamic allocation, no HAL/LL/CMSIS includes in
// monitor.c/monitor_cmds.c, no floating point, static buffers only.
//
// NOT REENTRANT, NO LOCKING. Every emit path formats through one shared static
// line buffer, so call every monitor entry point (monitor_poll, monitor_eventf,
// monitor_mark, monitor_plot) from the same context, and never from an ISR.
//
// A project integrates the monitor by:
//   1. providing a monitor_port_t (uart_read/uart_write/tick_ms/name),
//   2. implementing the bus shims below against its own drivers (any it omits
//      degrade to "ERR 7 nosup" via the weak defaults in monitor_cmds.c),
//   3. calling monitor_init() once and monitor_poll() every superloop pass.

#ifndef MONITOR_H
#define MONITOR_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdarg.h>

#define MONITOR_LINE_MAX 255
#define MONITOR_PROTO_VERSION 1

// Largest OK payload a command handler can return and still fit on the wire as
// "<SEQ OK <payload>\n" (the prefix is up to 10 bytes at seq 65535). Clamp any
// variable-length payload to this.
#define MON_OK_PAYLOAD_MAX (MONITOR_LINE_MAX - 10)

// Number of CAN controllers the monitor addresses, 1 to 9 (`can2 tx`, `!can2`).
// Override with -DMON_CAN_BUSES=<n> from the build.
#ifndef MON_CAN_BUSES
#define MON_CAN_BUSES 1
#endif
typedef char mon_can_buses_range_check[(MON_CAN_BUSES >= 1 && MON_CAN_BUSES <= 9) ? 1 : -1];

// The default bus shims in monitor_cmds.c are declared MON_WEAK so a project's
// own mon_* implementations override them at link time.
#if defined(__GNUC__) || defined(__clang__)
#define MON_WEAK __attribute__((weak))
#elif defined(__ICCARM__) || defined(__CC_ARM)
#define MON_WEAK __weak
#else
#define MON_WEAK
#endif

// --- error codes ---------------------------------------------------------------------
#define MONITOR_ERR_BADCMD   1
#define MONITOR_ERR_BADARG   2
#define MONITOR_ERR_TIMEOUT  3
#define MONITOR_ERR_BUSERR   4
#define MONITOR_ERR_NACK     5
#define MONITOR_ERR_BUSY     6
#define MONITOR_ERR_NOSUP    7
#define MONITOR_ERR_OVERFLOW 8
#define MONITOR_ERR_INTERNAL 9

// --- port layer -----------------------------------------------------------------------
typedef struct {
	// Pull up to max bytes from the UART RX circular buffer. Returns bytes copied.
	size_t   (*uart_read)(uint8_t *buf, size_t max);
	// Push one complete line (includes trailing \n) atomically to the TX circular
	// buffer. Returns false if it does not fit (monitor drops the line and counts it).
	bool     (*uart_write)(const uint8_t *buf, size_t len);
	// Free-running millisecond counter. Optional: without it markers carry no @tick
	// and the plot definition rebroadcast falls back to a poll count.
	uint32_t (*tick_ms)(void);
	const char *name;        // short project id for `ping`
} monitor_port_t;

// monitor_init resets the line-assembly and plot-stream state, but NOT the
// application command registry or the CAN software filter; both persist across
// a re-init.
void monitor_init(const monitor_port_t *port);
// Call from the superloop. Drains RX, dispatches at most one command per call,
// drains the CAN RX queue into events, rebroadcasts plot definitions. Cheap when idle.
void monitor_poll(void);
// Number of TX lines dropped because uart_write rejected them. Reset by monitor_init.
uint32_t monitor_tx_dropped(void);

// --- extending the command set (application code) ---
// argv[0] is the command name; write the OK payload into resp (no "OK" prefix,
// no newline, NUL-terminated). Return 0 for OK, or a MONITOR_ERR_* code; a code
// outside 1..9 is reported as 9 (internal).
typedef int (*monitor_handler_t)(int argc, char **argv,
								 char *resp, size_t resp_max);
// `name` is cached as a pointer and compared on every dispatch, and registrations
// are permanent, so it must point at static-lifetime storage (a string literal),
// never a stack buffer. Same rule as mon_plot_def_t.body.
bool monitor_register(const char *name, monitor_handler_t fn);   // static table, N=8 extra slots

// Emit an async event line "!<fmt...>" from main-loop context. A leading '!' and a
// trailing '\n' are added automatically; pass the body only, e.g.
// monitor_eventf("p %lu ax=%ld", tick, ax_mg) emits "!p <tick> ax=<n>\n".
void monitor_eventf(const char *fmt, ...);

// Emit a marker (timeline annotation): "!m @<tick> <text>\n", with the tick taken
// from the port's tick_ms(). text is free-form; it is sanitized so it cannot forge
// a second line. Main-loop context only. Returns 0, or MONITOR_ERR_BADARG for text
// that emits nothing: NULL, empty, or only spaces and tabs, and (on a port with no
// tick_ms) text whose first word is itself an "@<digits>" tick sigil.
int monitor_mark(const char *text);

// --- typed plot streams ---
typedef struct {
	char        sid;    // stream id digit '0' to '9'
	const char *body;   // definition body, e.g. "ax:s2*0.00098:g ay:s2*0.00098:g"
} mon_plot_def_t;
// Emit one "!ps" sample line. data points at a packed little-endian struct whose
// fields match the definition in order; len must equal the summed field sizes.
// Each stream's definition is parsed once, on first use (static registry, max 4
// streams); re-registering a sid with a different body is MONITOR_ERR_BADARG
// (same body is a no-op). Channel names and bit-lane names share one namespace per
// stream. The first rejection of a sid also emits "!e plot <sid> badarg def|body|len|full"
// once (a NULL def/body or a sid outside '0'..'9' emits "!e plot ? badarg sid" once), so
// a stream that never appears on the host says why; later rejections of that sid are
// silent until monitor_init(). The "!pd" definition line is re-emitted automatically
// every 5 s while the stream is active. def->body is cached as a raw pointer,
// not a copy, so it must remain valid while the stream is registered (a string
// literal or other static storage). Main-loop context only. Returns 0 or MONITOR_ERR_*.
int monitor_plot(const mon_plot_def_t *def, uint32_t tick,
				 const void *data, size_t len);

// --- bus shims ------------------------------------------------------------------------
// The project implements these in its monitor_port.c against its own drivers. Every
// shim has a weak default (monitor_cmds.c) returning MONITOR_ERR_NOSUP.
//
// i2c address-probe convention: `i2c scan` calls mon_i2c_xfer with wr_len 0 AND
// rd_len 0. The shim must return 0 if the address ACKs, MONITOR_ERR_NACK otherwise.
//
// Output-buffer rules:
//   - mon_i2c_xfer / mon_spi_xfer must fill all rd_len / len bytes when they return
//     0, or report the failure. The monitor zeroes both buffers first, so a short
//     fill reads as zeros rather than as stack residue on the wire.
//   - mon_can_rx_pop need only set the fields it has; the monitor zeroes the frame
//     before every call (tick 0, standard data frame; bus 0 reads as bus 1).
//   - mon_info_extra must NUL-terminate within the max it is given.
typedef struct {
	uint32_t id;
	uint8_t  dlc;
	uint8_t  data[8];
	bool     ext;
	bool     rtr;
	uint32_t tick_ms;       // set by the driver at reception
	uint8_t  bus;           // 1..MON_CAN_BUSES: set by the monitor on TX, by the
	                        // driver on RX (a single-bus shim never sets it)
} mon_can_frame_t;

// `bus` is always 1..MON_CAN_BUSES when a shim sees it; the monitor rejects
// anything else with ERR 2 badarg before the call.
int  mon_can_tx(const mon_can_frame_t *f);                       // ERR_* or 0
bool mon_can_rx_pop(mon_can_frame_t *f);                         // drain driver's RX queue
int  mon_can_filter(uint8_t bus, uint32_t id, uint32_t mask, bool ext);  // sw filter is fine
int  mon_can_stat(uint8_t bus, uint32_t *rx, uint32_t *tx, uint32_t *err,  // cumulative since
				  const char **state);  // init; state = current, may be left untouched

int  mon_i2c_xfer(uint8_t addr7,
				  const uint8_t *wr, size_t wr_len,              // may be 0
				  uint8_t *rd, size_t rd_len);                   // may be 0; both 0 = probe
int  mon_spi_xfer(const char *cs_name,
				  const uint8_t *tx, uint8_t *rx, size_t len);
int  mon_gpio_set(const char *name, bool level);
int  mon_gpio_get(const char *name, bool *level);
int  mon_adc_read(const char *name, int32_t *raw, int32_t *mv);  // *mv = INT32_MIN if n/a
int  mon_info_extra(char *buf, size_t max);                      // optional tokens for `info`

// --- internal interface shared between monitor.c and monitor_cmds.c -----------------
// Not part of the public contract; application code should ignore everything below.

// Dispatch one already-tokenized command (argv[0] = command name, no seq). Returns 0
// (write OK payload into resp) or a MONITOR_ERR_* code. Defined in monitor_cmds.c.
int  monitor_dispatch(int argc, char **argv, char *resp, size_t resp_max);
// True if a received frame passes that bus's software CAN filter (monitor_cmds.c).
// bus is 1..MON_CAN_BUSES; anything else fails.
bool monitor_can_filter_pass(uint8_t bus, uint32_t id, bool ext);
// The active port, for handlers that need tick_ms/name (monitor.c).
const monitor_port_t *monitor_active_port(void);

// Hex/number helpers (monitor.c), shared so monitor_cmds.c avoids snprintf on payloads.
size_t mon_hex_encode(const uint8_t *data, size_t len, char *out);       // 2*len chars, no NUL
int    mon_hex_decode(const char *s, uint8_t *out, size_t max, size_t *out_len);
int    mon_parse_hex_u32(const char *s, uint32_t *out);
int    mon_parse_dec_u32(const char *s, uint32_t *out);

#endif // MONITOR_H
