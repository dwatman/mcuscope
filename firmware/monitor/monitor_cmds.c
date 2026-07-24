// monitor_cmds.c - built-in v1 command handlers and dispatch table (SPEC 2.4/5.4).
//
// Two-level dispatch: the first token selects a family (can/i2c/spi/gpio/adc) and the
// second a sub-command; ping/info are single-level. Application commands registered via
// monitor_register() match on the first token only. Each handler writes just the OK
// payload into resp (no "<seq OK" prefix, no newline) and returns 0, or a MONITOR_ERR_*
// code. All bus work goes through the shims in monitor.h; a project that omits a shim
// gets the weak default at the bottom of this file, which answers ERR 7 nosup.

#include "monitor.h"

#include <stdio.h>
#include <string.h>

#define MON_MAX_DATA 128   // max payload bytes carried by one i2c/spi command line

// --- CAN software filter (SPEC 2.4 `can filter`) ------------------------------------

static enum { FILT_ALL, FILT_NONE, FILT_MASK } g_filt_mode = FILT_ALL;
static uint32_t g_filt_id;
static uint32_t g_filt_mask;

bool monitor_can_filter_pass(uint32_t id, bool ext) {
    (void)ext;   // SPEC matching formula is over id/mask only
    switch (g_filt_mode) {
        case FILT_ALL:  return true;
        case FILT_NONE: return false;
        default:        return (id & g_filt_mask) == (g_filt_id & g_filt_mask);
    }
}

// --- helpers ------------------------------------------------------------------------

// Hex-encode `len` bytes of `data` into `resp`, clamping the byte count first so the
// hex digits plus the terminating NUL always fit within resp_max. This guards against
// ever truncating mid-nibble (which the old "clamp after encoding" pattern could do at
// the exact boundary): the clamp happens before mon_hex_encode ever writes a byte.
static void emit_hex_resp(const uint8_t *data, size_t len, char *resp, size_t resp_max) {
    size_t max_bytes = (resp_max > 0) ? (resp_max - 1) / 2 : 0;
    if (len > max_bytes) {
        len = max_bytes;
    }
    size_t hn = mon_hex_encode(data, len, resp);
    if (resp_max > 0) {
        resp[hn] = '\0';
    }
}

// Parse a CAN flags token (any of 'x','r'). Returns 0 on success.
static int parse_can_flags(const char *tok, bool *ext, bool *rtr) {
    *ext = false;
    *rtr = false;
    for (; *tok; tok++) {
        if (*tok == 'x') {
            *ext = true;
        } else if (*tok == 'r') {
            *rtr = true;
        } else {
            return -1;
        }
    }
    return 0;
}

// --- ping / info --------------------------------------------------------------------

static int cmd_ping(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv;
    const monitor_port_t *p = monitor_active_port();
    const char *name = (p && p->name) ? p->name : "monitor";
    snprintf(resp, resp_max, "monitor %d %s", MONITOR_PROTO_VERSION, name);
    return 0;
}

static int cmd_info(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv;
    const monitor_port_t *p = monitor_active_port();
    uint32_t up = (p && p->tick_ms) ? p->tick_ms() : 0;
    int n = snprintf(resp, resp_max, "up=%lu", (unsigned long)up);
    if (n < 0 || (size_t)n >= resp_max) {
        return 0;
    }
    char extra[64];
    extra[0] = '\0';
    if (mon_info_extra(extra, sizeof extra) == 0 && extra[0]) {
        snprintf(resp + n, resp_max - (size_t)n, " %s", extra);
    }
    return 0;
}

// --- CAN ----------------------------------------------------------------------------

static int cmd_can_tx(int argc, char **argv, char *resp, size_t resp_max) {
    (void)resp; (void)resp_max;
    // argv: can tx <id> <data|-> [flags]
    if (argc < 4 || argc > 5) {
        return MONITOR_ERR_BADARG;
    }
    mon_can_frame_t f;
    memset(&f, 0, sizeof f);
    if (mon_parse_hex_u32(argv[2], &f.id) != 0) {
        return MONITOR_ERR_BADARG;
    }
    if (argc == 5) {
        if (parse_can_flags(argv[4], &f.ext, &f.rtr) != 0) {
            return MONITOR_ERR_BADARG;
        }
    }
    if (f.id > (f.ext ? 0x1FFFFFFFu : 0x7FFu)) {
        return MONITOR_ERR_BADARG;   // 11-bit standard id, 29-bit with the x flag
    }
    if (f.rtr) {
        // Data token is a single decimal DLC digit for RTR requests.
        uint32_t dlc;
        if (mon_parse_dec_u32(argv[3], &dlc) != 0 || dlc > 8) {
            return MONITOR_ERR_BADARG;
        }
        f.dlc = (uint8_t)dlc;
    } else if (strcmp(argv[3], "-") == 0) {
        f.dlc = 0;
    } else {
        size_t len;
        if (mon_hex_decode(argv[3], f.data, sizeof f.data, &len) != 0 || len > 8) {
            return MONITOR_ERR_BADARG;
        }
        f.dlc = (uint8_t)len;
    }
    return mon_can_tx(&f);
}

static int cmd_can_filter(int argc, char **argv, char *resp, size_t resp_max) {
    (void)resp; (void)resp_max;
    // argv: can filter all|none | can filter <id> <mask> [flags]
    if (argc == 3) {
        if (strcmp(argv[2], "all") == 0) {
            g_filt_mode = FILT_ALL;
            return 0;
        }
        if (strcmp(argv[2], "none") == 0) {
            g_filt_mode = FILT_NONE;
            return 0;
        }
        return MONITOR_ERR_BADARG;
    }
    if (argc == 4 || argc == 5) {
        uint32_t id, mask;
        bool ext = false, rtr = false;
        if (mon_parse_hex_u32(argv[2], &id) != 0 ||
            mon_parse_hex_u32(argv[3], &mask) != 0) {
            return MONITOR_ERR_BADARG;
        }
        if (argc == 5 && parse_can_flags(argv[4], &ext, &rtr) != 0) {
            return MONITOR_ERR_BADARG;
        }
        g_filt_mode = FILT_MASK;
        g_filt_id = id;
        g_filt_mask = mask;
        mon_can_filter(id, mask, ext);   // best-effort hardware filter; nosup is fine
        return 0;
    }
    return MONITOR_ERR_BADARG;
}

static int cmd_can_stat(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv;
    uint32_t rx = 0, tx = 0, err = 0;
    const char *state = "active";
    int code = mon_can_stat(&rx, &tx, &err, &state);
    if (code != 0) {
        return code;
    }
    snprintf(resp, resp_max, "rx=%lu tx=%lu err=%lu state=%s",
             (unsigned long)rx, (unsigned long)tx, (unsigned long)err, state);
    return 0;
}

// --- I2C ----------------------------------------------------------------------------

static int cmd_i2c_scan(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv;
    // 7-bit address sweep 0x08..0x77; zero-length probe ACK means present.
    size_t pos = 0;
    for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
        if (mon_i2c_xfer(addr, NULL, 0, NULL, 0) == 0) {
            int n = snprintf(resp + pos, resp_max - pos,
                             (pos == 0) ? "%02X" : " %02X", addr);
            if (n < 0 || (size_t)n >= resp_max - pos) {
                resp[pos] = '\0';   // erase the partial write, keep the list well-formed
                break;
            }
            pos += (size_t)n;
        }
    }
    return 0;
}

static int cmd_i2c_wr(int argc, char **argv, char *resp, size_t resp_max) {
    (void)resp; (void)resp_max;
    if (argc != 4) {
        return MONITOR_ERR_BADARG;
    }
    uint32_t addr;
    uint8_t wr[MON_MAX_DATA];
    size_t wr_len;
    if (mon_parse_hex_u32(argv[2], &addr) != 0 || addr > 0x7F) {
        return MONITOR_ERR_BADARG;
    }
    if (mon_hex_decode(argv[3], wr, sizeof wr, &wr_len) != 0) {
        return MONITOR_ERR_BADARG;
    }
    return mon_i2c_xfer((uint8_t)addr, wr, wr_len, NULL, 0);
}

static int cmd_i2c_rd(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc != 4) {
        return MONITOR_ERR_BADARG;
    }
    uint32_t addr, n;
    if (mon_parse_hex_u32(argv[2], &addr) != 0 || addr > 0x7F) {
        return MONITOR_ERR_BADARG;
    }
    if (mon_parse_dec_u32(argv[3], &n) != 0 || n < 1 || n > 64) {
        return MONITOR_ERR_BADARG;
    }
    uint8_t rd[64];
    int code = mon_i2c_xfer((uint8_t)addr, NULL, 0, rd, n);
    if (code != 0) {
        return code;
    }
    emit_hex_resp(rd, n, resp, resp_max);
    return 0;
}

static int cmd_i2c_wrrd(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc != 5) {
        return MONITOR_ERR_BADARG;
    }
    uint32_t addr, n;
    uint8_t wr[MON_MAX_DATA];
    size_t wr_len;
    if (mon_parse_hex_u32(argv[2], &addr) != 0 || addr > 0x7F) {
        return MONITOR_ERR_BADARG;
    }
    if (mon_hex_decode(argv[3], wr, sizeof wr, &wr_len) != 0) {
        return MONITOR_ERR_BADARG;
    }
    if (mon_parse_dec_u32(argv[4], &n) != 0 || n < 1 || n > 64) {
        return MONITOR_ERR_BADARG;
    }
    uint8_t rd[64];
    int code = mon_i2c_xfer((uint8_t)addr, wr, wr_len, rd, n);
    if (code != 0) {
        return code;
    }
    emit_hex_resp(rd, n, resp, resp_max);
    return 0;
}

// --- SPI ----------------------------------------------------------------------------

static int cmd_spi_xfer(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc != 4) {
        return MONITOR_ERR_BADARG;
    }
    uint8_t tx[MON_MAX_DATA];
    uint8_t rx[MON_MAX_DATA];
    size_t len;
    if (mon_hex_decode(argv[3], tx, sizeof tx, &len) != 0) {
        return MONITOR_ERR_BADARG;
    }
    int code = mon_spi_xfer(argv[2], tx, rx, len);
    if (code != 0) {
        return code;
    }
    emit_hex_resp(rx, len, resp, resp_max);
    return 0;
}

// --- GPIO ---------------------------------------------------------------------------

static int cmd_gpio_set(int argc, char **argv, char *resp, size_t resp_max) {
    (void)resp; (void)resp_max;
    if (argc != 4) {
        return MONITOR_ERR_BADARG;
    }
    bool level;
    if (strcmp(argv[3], "0") == 0) {
        level = false;
    } else if (strcmp(argv[3], "1") == 0) {
        level = true;
    } else {
        return MONITOR_ERR_BADARG;
    }
    return mon_gpio_set(argv[2], level);
}

static int cmd_gpio_get(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc != 3) {
        return MONITOR_ERR_BADARG;
    }
    bool level = false;
    int code = mon_gpio_get(argv[2], &level);
    if (code != 0) {
        return code;
    }
    snprintf(resp, resp_max, "%d", level ? 1 : 0);
    return 0;
}

// --- ADC ----------------------------------------------------------------------------

static int cmd_adc_read(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc != 3) {
        return MONITOR_ERR_BADARG;
    }
    int32_t raw = 0, mv = INT32_MIN;
    int code = mon_adc_read(argv[2], &raw, &mv);
    if (code != 0) {
        return code;
    }
    if (mv == INT32_MIN) {
        snprintf(resp, resp_max, "raw=%ld", (long)raw);
    } else {
        snprintf(resp, resp_max, "raw=%ld mv=%ld", (long)raw, (long)mv);
    }
    return 0;
}

// --- dispatch table -----------------------------------------------------------------

typedef struct {
    const char       *c1;   // first token (command family)
    const char       *c2;   // second token, or NULL for single-level commands
    monitor_handler_t fn;
} cmd_row_t;

static const cmd_row_t g_cmds[] = {
    {"ping", NULL,     cmd_ping},
    {"info", NULL,     cmd_info},
    {"can",  "tx",     cmd_can_tx},
    {"can",  "filter", cmd_can_filter},
    {"can",  "stat",   cmd_can_stat},
    {"i2c",  "scan",   cmd_i2c_scan},
    {"i2c",  "wr",     cmd_i2c_wr},
    {"i2c",  "rd",     cmd_i2c_rd},
    {"i2c",  "wrrd",   cmd_i2c_wrrd},
    {"spi",  "xfer",   cmd_spi_xfer},
    {"gpio", "set",    cmd_gpio_set},
    {"gpio", "get",    cmd_gpio_get},
    {"adc",  "read",   cmd_adc_read},
};
#define CMD_COUNT (sizeof g_cmds / sizeof g_cmds[0])

// --- application command registry ---------------------------------------------------

#define MON_REG_SLOTS 8

static struct {
    const char       *name;
    monitor_handler_t fn;
} g_reg[MON_REG_SLOTS];
static int g_reg_count;

bool monitor_register(const char *name, monitor_handler_t fn) {
    if (name == NULL || fn == NULL) {
        return false;
    }
    for (int i = 0; i < g_reg_count; i++) {
        if (strcmp(g_reg[i].name, name) == 0) {
            return false;   // no duplicates
        }
    }
    if (g_reg_count >= MON_REG_SLOTS) {
        return false;
    }
    g_reg[g_reg_count].name = name;
    g_reg[g_reg_count].fn = fn;
    g_reg_count++;
    return true;
}

int monitor_dispatch(int argc, char **argv, char *resp, size_t resp_max) {
    // Two-level builtins first (so "can tx" is not shadowed by anything).
    if (argc >= 2) {
        for (size_t i = 0; i < CMD_COUNT; i++) {
            if (g_cmds[i].c2 && strcmp(argv[0], g_cmds[i].c1) == 0 &&
                strcmp(argv[1], g_cmds[i].c2) == 0) {
                return g_cmds[i].fn(argc, argv, resp, resp_max);
            }
        }
    }
    // Single-level builtins (ping/info).
    for (size_t i = 0; i < CMD_COUNT; i++) {
        if (g_cmds[i].c2 == NULL && strcmp(argv[0], g_cmds[i].c1) == 0) {
            return g_cmds[i].fn(argc, argv, resp, resp_max);
        }
    }
    // Application commands: match on the first token only.
    for (int i = 0; i < g_reg_count; i++) {
        if (strcmp(argv[0], g_reg[i].name) == 0) {
            return g_reg[i].fn(argc, argv, resp, resp_max);
        }
    }
    return MONITOR_ERR_BADCMD;
}

// --- weak default shims (SPEC 5.3) --------------------------------------------------
// A project overrides only the buses it has; everything else degrades to ERR 7 nosup.

MON_WEAK int mon_can_tx(const mon_can_frame_t *f) {
    (void)f;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK bool mon_can_rx_pop(mon_can_frame_t *f) {
    (void)f;
    return false;
}
MON_WEAK int mon_can_filter(uint32_t id, uint32_t mask, bool ext) {
    (void)id; (void)mask; (void)ext;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_can_stat(uint32_t *rx, uint32_t *tx, uint32_t *err,
                          const char **state) {
    (void)rx; (void)tx; (void)err; (void)state;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_i2c_xfer(uint8_t addr7, const uint8_t *wr, size_t wr_len,
                          uint8_t *rd, size_t rd_len) {
    (void)addr7; (void)wr; (void)wr_len; (void)rd; (void)rd_len;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_spi_xfer(const char *cs_name, const uint8_t *tx,
                          uint8_t *rx, size_t len) {
    (void)cs_name; (void)tx; (void)rx; (void)len;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_gpio_set(const char *name, bool level) {
    (void)name; (void)level;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_gpio_get(const char *name, bool *level) {
    (void)name; (void)level;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_adc_read(const char *name, int32_t *raw, int32_t *mv) {
    (void)name; (void)raw; (void)mv;
    return MONITOR_ERR_NOSUP;
}
MON_WEAK int mon_info_extra(char *buf, size_t max) {
    (void)buf; (void)max;
    return MONITOR_ERR_NOSUP;
}
