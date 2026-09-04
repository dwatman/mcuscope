// fake_shims.c - host-side fakes backing the monitor for the unit tests.
//
// Provides the port callbacks (UART read/write, tick), array-backed bus shims, and
// small control hooks the test driver uses to feed input, capture output, advance the
// fake clock, and push CAN frames.
//
// The fakes are deliberately no gentler than a real port: every shim that fills a caller's
// buffer has a mode that fills less than asked (or more than it reports), because that is
// what a hand-written driver shim does and it is the only way the monitor's defensive
// clamps and pre-zeroing get exercised. Their default modes keep the plain expectations,
// so a test opts into hostility. mon_spi_xfer and mon_info_extra are defined here rather
// than left to the weak defaults, so their data paths run at all; their disabled mode
// returns MONITOR_ERR_NOSUP so the "unimplemented bus answers ERR 7" cases still hold.

#include "../monitor/monitor.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

// --- UART / clock fakes -------------------------------------------------------------

static uint8_t  rxbuf[4096];
static size_t   rx_len;
static size_t   rx_pos;
static char     txbuf[16384];
static size_t   tx_len;
static uint32_t g_tick;
static bool     g_led;        // GPIO fake state, reset with the rest of the fakes
static bool     g_tx_reject;  // when true, fake_uart_write rejects every line
static bool     g_i2c_all_ack; // when true, every address ACKs (SDA stuck low)
static bool     g_i2c_short;   // when true, an i2c read fills one byte and still returns 0
static int      g_spi_mode;    // 0 = nosup, 1 = full duplex echo, 2 = fills one byte only
static int      g_info_mode;   // 0 = nosup, 1 = normal tokens, 2 = fills every byte offered
static int      g_can_stat_mode;  // 0 = normal, 1 = answers 0 with *state = NULL

void fake_reset(void) {
	rx_len = 0;
	rx_pos = 0;
	tx_len = 0;
	txbuf[0] = '\0';
	g_led = false;
	g_tx_reject = false;
	g_i2c_all_ack = false;
	g_i2c_short = false;
	g_spi_mode = 0;
	g_info_mode = 0;
	g_can_stat_mode = 0;
}

// 0 = normal, 1 = answer 0 having set *state to NULL (a shim the contract permits).
void fake_can_stat_set_mode(int mode) {
	g_can_stat_mode = mode;
}

void fake_spi_set_mode(int mode) {
	g_spi_mode = mode;
}

void fake_info_set_mode(int mode) {
	g_info_mode = mode;
}

// Simulate a shorted/stuck-low bus, where every probed address appears to ACK.
void fake_i2c_set_all_ack(bool all_ack) {
	g_i2c_all_ack = all_ack;
}

// Fill one byte of the read and still answer 0: the monitor's pre-zeroing is what keeps the
// rest off the wire.
void fake_i2c_set_short_read(bool short_read) {
	g_i2c_short = short_read;
}

void fake_tx_reset(void) {
	tx_len = 0;
	txbuf[0] = '\0';
}

// The asserts below keep a harness overrun from masquerading as a monitor defect: a test
// that feeds or captures more than these arrays hold would otherwise smash a static here
// and the failure would read as an out-of-bounds in monitor.c.
void fake_feed(const char *s) {
	size_t n = strlen(s);
	assert(rx_len + n <= sizeof rxbuf);
	memcpy(rxbuf + rx_len, s, n);
	rx_len += n;
}

// Feed raw bytes (may include NUL); fake_feed cannot express embedded NULs.
void fake_feed_raw(const void *p, size_t n) {
	assert(rx_len + n <= sizeof rxbuf);
	memcpy(rxbuf + rx_len, p, n);
	rx_len += n;
}

void fake_tx_set_reject(bool reject) {
	g_tx_reject = reject;
}

const char *fake_tx(void) {
	return txbuf;
}

void fake_set_tick(uint32_t t) {
	g_tick = t;
}

size_t fake_uart_read(uint8_t *buf, size_t max) {
	size_t avail = rx_len - rx_pos;
	size_t n = avail < max ? avail : max;
	memcpy(buf, rxbuf + rx_pos, n);
	rx_pos += n;
	return n;
}

bool fake_uart_write(const uint8_t *buf, size_t len) {
	if (g_tx_reject) {
		return false;   // simulate a full TX ring: whole line rejected
	}
	assert(tx_len + len + 1 <= sizeof txbuf);   // +1 for the NUL below
	memcpy(txbuf + tx_len, buf, len);
	tx_len += len;
	txbuf[tx_len] = '\0';
	return true;
}

uint32_t fake_tick_ms(void) {
	return g_tick;
}

// --- over-reporting UART read (SPEC 5.4 clamp) ---------------------------------------
// A shim that lies about how much it copied. Two real slips: a ring that copies
// min(max, avail) but returns avail, and an int-returning driver whose -1 becomes SIZE_MAX.
// It fills the whole buffer it is offered with eight-byte command lines, so the number of
// responses says exactly how many bytes the monitor consumed.

static bool g_over_huge;
static bool g_over_done;

void fake_uart_read_over_config(bool huge) {
	g_over_huge = huge;
	g_over_done = false;
}

size_t fake_uart_read_over(uint8_t *buf, size_t max) {
	if (g_over_done) {
		return 0;
	}
	g_over_done = true;
	static const char first[] = ">1 ping\n";   // 8 bytes
	static const char rest[]  = ">2 ping\n";   // 8 bytes
	for (size_t i = 0; i < max; i++) {
		buf[i] = (uint8_t)((i < 8) ? first[i] : rest[i % 8]);
	}
	return g_over_huge ? SIZE_MAX : max + 8;
}

// --- CAN fakes ----------------------------------------------------------------------

// Deeper than drain_can's 64-frames-per-poll bound, so the bound itself is testable.
static mon_can_frame_t canq[256];
static size_t canq_len;
static size_t canq_pos;
static bool   g_can_partial;   // fill only id/dlc/data, as a mailbox-reading shim would

static mon_can_frame_t g_last_tx;
static bool g_have_tx;
static uint8_t g_last_filter_bus;   // bus of the last mon_can_filter call, 0 if none

void fake_can_reset(void) {
	g_last_filter_bus = 0;
	canq_len = 0;
	canq_pos = 0;
	g_have_tx = false;
	g_can_partial = false;
}

// A shim reading a bxCAN/FDCAN mailbox sets the fields it has and leaves the rest alone;
// the monitor is what must zero them. This mode reproduces that.
void fake_can_set_partial_fill(bool partial) {
	g_can_partial = partial;
}

void fake_can_push(const mon_can_frame_t *f) {
	assert(canq_len < sizeof canq / sizeof canq[0]);
	canq[canq_len++] = *f;
}

const mon_can_frame_t *fake_can_last_tx(void) {
	return g_have_tx ? &g_last_tx : NULL;
}

int mon_can_tx(const mon_can_frame_t *f) {
	g_last_tx = *f;
	g_have_tx = true;
	return 0;
}

bool mon_can_rx_pop(mon_can_frame_t *f) {
	if (canq_pos >= canq_len) {
		return false;
	}
	const mon_can_frame_t *q = &canq[canq_pos++];
	if (g_can_partial) {
		// Write only what a mailbox-reading shim has, leaving the rest exactly as the caller
		// handed it over. The harness must not pre-zero here or the monitor's own memset
		// stops being load-bearing; the test dirties the stack instead.
		f->id = q->id;
		f->dlc = q->dlc;
		memcpy(f->data, q->data, sizeof f->data);
		return true;
	}
	*f = *q;
	return true;
}

uint8_t fake_can_last_filter_bus(void) {
	return g_last_filter_bus;
}

int mon_can_filter(uint8_t bus, uint32_t id, uint32_t mask, bool ext) {
	(void)id; (void)mask; (void)ext;
	g_last_filter_bus = bus;
	return 0;   // pretend the hardware filter took it
}

// Distinct counts per bus, so a test can tell which one `can<n> stat` reached.
int mon_can_stat(uint8_t bus, uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state) {
	if (g_can_stat_mode == 1) {
		*state = NULL;
		return 0;
	}
	*rx = bus == 2 ? 20 : 10;
	*tx = bus == 2 ? 5 : 3;
	*err = bus == 2 ? 1 : 0;
	*state = bus == 2 ? "passive" : "active";
	return 0;
}

// --- I2C fake -----------------------------------------------------------------------
// Devices at 0x48 (returns 06 42 ...) and 0x50 (returns A0 A1 ... from write offset).

int mon_i2c_xfer(uint8_t addr7, const uint8_t *wr, size_t wr_len,
				 uint8_t *rd, size_t rd_len) {
	bool present = g_i2c_all_ack || (addr7 == 0x48 || addr7 == 0x50);
	if (wr_len == 0 && rd_len == 0) {
		return present ? 0 : MONITOR_ERR_NACK;   // address probe
	}
	if (!present) {
		return MONITOR_ERR_NACK;
	}
	(void)wr;
	if (g_i2c_short && rd_len > 0) {
		rd[0] = 0x11;   // a short read the shim wrongly reports as success
		return 0;
	}
	if (rd_len > 0) {
		for (size_t i = 0; i < rd_len; i++) {
			rd[i] = (addr7 == 0x48) ? (uint8_t)((i % 2) ? 0x42 : 0x06)
									: (uint8_t)(0xA0 + i);
		}
	}
	return 0;
}

// --- SPI fake -----------------------------------------------------------------------
// Chip select "imu" only; MISO is the MOSI byte inverted, so a response is checkable.

int mon_spi_xfer(const char *cs_name, const uint8_t *tx, uint8_t *rx, size_t len) {
	if (g_spi_mode == 0) {
		return MONITOR_ERR_NOSUP;   // bus not wired up on this board
	}
	if (strcmp(cs_name, "imu") != 0) {
		return MONITOR_ERR_BADARG;
	}
	if (g_spi_mode == 2) {
		if (len > 0) {
			rx[0] = 0x55;   // short fill, wrongly reported as success
		}
		return 0;
	}
	for (size_t i = 0; i < len; i++) {
		rx[i] = (uint8_t)(tx[i] ^ 0xFF);
	}
	return 0;
}

// --- info extras fake -----------------------------------------------------------------

int mon_info_extra(char *buf, size_t max) {
	if (g_info_mode == 0) {
		return MONITOR_ERR_NOSUP;
	}
	if (g_info_mode == 2) {
		memset(buf, 'Z', max);   // fills every byte offered, leaving no room for a NUL
		return 0;
	}
	snprintf(buf, max, "rst=por fw=1.2.3");
	return 0;
}

// --- GPIO fake ----------------------------------------------------------------------

int mon_gpio_set(const char *name, bool level) {
	if (strcmp(name, "led") == 0) {
		g_led = level;
		return 0;
	}
	return MONITOR_ERR_BADARG;
}

int mon_gpio_get(const char *name, bool *level) {
	if (strcmp(name, "led") == 0) {
		*level = g_led;
		return 0;
	}
	return MONITOR_ERR_BADARG;
}

// --- ADC fake -----------------------------------------------------------------------

int mon_adc_read(const char *name, int32_t *raw, int32_t *mv) {
	if (strcmp(name, "vref") == 0) {
		*raw = 2048;
		*mv = 3300;
		return 0;
	}
	return MONITOR_ERR_BADARG;
}
