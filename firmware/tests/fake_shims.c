// fake_shims.c - host-side fakes backing the monitor for the unit tests.
//
// Provides the port callbacks (UART read/write, tick), array-backed bus shims, and
// small control hooks the test driver uses to feed input, capture output, advance the
// fake clock, and push CAN frames. mon_spi_xfer and mon_info_extra are deliberately
// left undefined so the weak defaults in monitor_cmds.c answer "ERR 7 nosup".

#include "../monitor/monitor.h"

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

void fake_reset(void) {
    rx_len = 0;
    rx_pos = 0;
    tx_len = 0;
    txbuf[0] = '\0';
    g_led = false;
    g_tx_reject = false;
    g_i2c_all_ack = false;
}

// Simulate a shorted/stuck-low bus, where every probed address appears to ACK.
void fake_i2c_set_all_ack(bool all_ack) {
    g_i2c_all_ack = all_ack;
}

void fake_tx_reset(void) {
    tx_len = 0;
    txbuf[0] = '\0';
}

void fake_feed(const char *s) {
    size_t n = strlen(s);
    memcpy(rxbuf + rx_len, s, n);
    rx_len += n;
}

// Feed raw bytes (may include NUL); fake_feed cannot express embedded NULs.
void fake_feed_raw(const void *p, size_t n) {
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
    memcpy(txbuf + tx_len, buf, len);
    tx_len += len;
    txbuf[tx_len] = '\0';
    return true;
}

uint32_t fake_tick_ms(void) {
    return g_tick;
}

// --- CAN fakes ----------------------------------------------------------------------

static mon_can_frame_t canq[32];
static size_t canq_len;
static size_t canq_pos;

static mon_can_frame_t g_last_tx;
static bool g_have_tx;

void fake_can_reset(void) {
    canq_len = 0;
    canq_pos = 0;
    g_have_tx = false;
}

void fake_can_push(const mon_can_frame_t *f) {
    if (canq_len < sizeof canq / sizeof canq[0]) {
        canq[canq_len++] = *f;
    }
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
    if (canq_pos < canq_len) {
        *f = canq[canq_pos++];
        return true;
    }
    return false;
}

int mon_can_filter(uint32_t id, uint32_t mask, bool ext) {
    (void)id; (void)mask; (void)ext;
    return 0;   // pretend the hardware filter took it
}

int mon_can_stat(uint32_t *rx, uint32_t *tx, uint32_t *err, const char **state) {
    *rx = 10;
    *tx = 3;
    *err = 0;
    *state = "active";
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
    if (rd_len > 0) {
        for (size_t i = 0; i < rd_len; i++) {
            rd[i] = (addr7 == 0x48) ? (uint8_t)((i % 2) ? 0x42 : 0x06)
                                    : (uint8_t)(0xA0 + i);
        }
    }
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
