// monitor.h - portable UART debug monitor: public API + shim contract (SPEC 5.2/5.3).
//
// This is the contract. Core rules (SPEC 5.1): C99, no dynamic allocation, no
// HAL/LL/CMSIS includes in monitor.c/monitor_cmds.c, no floating point in the
// monitor's own code, static buffers only, main-loop context only.
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

// --- error codes (shared table, SPEC 2.3) -------------------------------------------
#define MONITOR_ERR_BADCMD   1
#define MONITOR_ERR_BADARG   2
#define MONITOR_ERR_TIMEOUT  3
#define MONITOR_ERR_BUSERR   4
#define MONITOR_ERR_NACK     5
#define MONITOR_ERR_BUSY     6
#define MONITOR_ERR_NOSUP    7
#define MONITOR_ERR_OVERFLOW 8
#define MONITOR_ERR_INTERNAL 9

// --- port layer (SPEC 5.2) ----------------------------------------------------------
typedef struct {
    // Pull up to max bytes from the UART RX circular buffer. Returns bytes copied.
    size_t   (*uart_read)(uint8_t *buf, size_t max);
    // Push one complete line (includes trailing \n) atomically to the TX circular
    // buffer. Returns false if it does not fit (monitor drops the line and counts it).
    bool     (*uart_write)(const uint8_t *buf, size_t len);
    uint32_t (*tick_ms)(void);
    const char *name;        // short project id for `ping`
} monitor_port_t;

void monitor_init(const monitor_port_t *port);
// Call from the superloop. Drains RX, dispatches at most one command per call,
// drains the CAN RX queue into events, rebroadcasts plot definitions. Cheap when idle.
void monitor_poll(void);

// --- extending the command set (application code) ---
// argv[0] is the command name; write the OK payload into resp (no "OK" prefix,
// no newline). Return 0 for OK, or a MONITOR_ERR_* code.
typedef int (*monitor_handler_t)(int argc, char **argv,
                                 char *resp, size_t resp_max);
bool monitor_register(const char *name, monitor_handler_t fn);   // static table, N=8 extra slots

// Emit an async event line "!<fmt...>" from main-loop context. A leading '!' and a
// trailing '\n' are added automatically; pass the body only, e.g.
// monitor_eventf("p %lu ax=%ld", tick, ax_mg) emits "!p <tick> ax=<n>\n".
void monitor_eventf(const char *fmt, ...);

// --- typed plot streams (SPEC 2.5) ---
typedef struct {
    char        sid;    // stream id digit '0' to '9'
    const char *body;   // definition body, e.g. "ax:s2*0.00098:g ay:s2*0.00098:g"
} mon_plot_def_t;
// Emit one "!ps" sample line. data points at a packed little-endian struct whose
// fields match the definition in order; len must equal the summed field sizes
// (else MONITOR_ERR_BADARG). The monitor parses each stream's definition once, on
// first use (static registry, max 4 streams), caching field widths; it emits each
// field as big-endian hex and re-emits the "!pd" definition line automatically every
// 2 s while the stream is active. Main-loop context only. Returns 0 or MONITOR_ERR_*.
int monitor_plot(const mon_plot_def_t *def, uint32_t tick,
                 const void *data, size_t len);

// --- bus shims (SPEC 5.3) -----------------------------------------------------------
// The owner implements these in their monitor_port.c against their own drivers. Every
// shim has a weak default (monitor_cmds.c) returning MONITOR_ERR_NOSUP, so a project
// that has no SPI simply never defines mon_spi_xfer and `spi xfer` answers ERR 7 nosup.
//
// i2c address-probe convention: `i2c scan` calls mon_i2c_xfer with wr_len 0 AND
// rd_len 0. The shim must return 0 if the address ACKs, MONITOR_ERR_NACK otherwise.
typedef struct {
    uint32_t id;
    uint8_t  dlc;
    uint8_t  data[8];
    bool     ext;
    bool     rtr;
    uint32_t tick_ms;       // set by the driver at reception
} mon_can_frame_t;

int  mon_can_tx(const mon_can_frame_t *f);                       // ERR_* or 0
bool mon_can_rx_pop(mon_can_frame_t *f);                         // drain driver's RX queue
int  mon_can_filter(uint32_t id, uint32_t mask, bool ext);       // software filter is fine
int  mon_can_stat(uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state);

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
// Not part of the public contract; kept here to honour the SPEC 5.1 file list (no
// private header). Application code should ignore everything below.

// Dispatch one already-tokenized command (argv[0] = command name, no seq). Returns 0
// (write OK payload into resp) or a MONITOR_ERR_* code. Defined in monitor_cmds.c.
int  monitor_dispatch(int argc, char **argv, char *resp, size_t resp_max);
// True if a received frame passes the current software CAN filter (monitor_cmds.c).
bool monitor_can_filter_pass(uint32_t id, bool ext);
// The active port, for handlers that need tick_ms/name (monitor.c).
const monitor_port_t *monitor_active_port(void);

// Hex/number helpers (monitor.c), shared so monitor_cmds.c avoids snprintf on payloads.
size_t mon_hex_encode(const uint8_t *data, size_t len, char *out);       // 2*len chars, no NUL
int    mon_hex_decode(const char *s, uint8_t *out, size_t max, size_t *out_len);
int    mon_parse_hex_u32(const char *s, uint32_t *out);
int    mon_parse_dec_u32(const char *s, uint32_t *out);

#endif // MONITOR_H
