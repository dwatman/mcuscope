// monitor.c - core of the portable UART debug monitor (SPEC 5.1/5.4).
//
// Responsibilities: assemble lines from the RX byte stream, tokenize in place,
// dispatch one command per poll (handlers live in monitor_cmds.c), format the
// response, drain the CAN RX queue into "!can" events, emit async events and typed
// plot samples, and rebroadcast plot definitions every 5 s.
//
// No HAL/LL/CMSIS here, no floating point, no dynamic allocation. snprintf/vsnprintf
// are used only on the cold paths (responses and events); the plot hot path hand-rolls
// hex with a nibble table and never touches printf (SPEC 5.2 performance contract).

#include "monitor.h"

#include <stdio.h>
#include <string.h>

// --- module state -------------------------------------------------------------------

static const monitor_port_t *g_port;

// Line assembly. Bytes are staged from one uart_read per poll, then fed into g_line
// until LF. On overflow we keep the (truncated) prefix so a seq can still be parsed.
static uint8_t g_line[MONITOR_LINE_MAX + 1];
static size_t  g_line_len;
static bool    g_overflow;

static uint8_t g_stage[64];
static size_t  g_stage_len;
static size_t  g_stage_pos;

// Shared outgoing line buffer and response payload buffer (static: predictable RAM,
// small stack). The monitor runs in a single context so sharing g_out is safe.
static char g_out[MONITOR_LINE_MAX + 2];
static char g_resp[MONITOR_LINE_MAX + 1];

static const char HEX[] = "0123456789ABCDEF";

// --- typed plot registry (SPEC 2.5) -------------------------------------------------

#define MON_PLOT_MAX_STREAMS 4
#define MON_PLOT_MAX_FIELDS  16
#define MON_PLOT_PD_PERIOD_MS 5000u

typedef struct {
    bool        used;
    char        sid;
    const char *body;
    uint8_t     widths[MON_PLOT_MAX_FIELDS];
    uint8_t     nfields;
    uint16_t    total;
    uint32_t    last_pd_ms;
} plot_stream_t;

static plot_stream_t g_plots[MON_PLOT_MAX_STREAMS];

// --- small helpers ------------------------------------------------------------------

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static char *emit_hex_u32(char *o, uint32_t v) {
    // Unpadded uppercase hex (natural reading order), used for ids and plot ticks.
    char tmp[8];
    int n = 0;
    if (v == 0) {
        *o++ = '0';
        return o;
    }
    while (v) {
        tmp[n++] = HEX[v & 0xF];
        v >>= 4;
    }
    while (n--) {
        *o++ = tmp[n];
    }
    return o;
}

static char *emit_dec_u32(char *o, uint32_t v) {
    char tmp[10];
    int n = 0;
    if (v == 0) {
        *o++ = '0';
        return o;
    }
    while (v) {
        tmp[n++] = (char)('0' + (v % 10));
        v /= 10;
    }
    while (n--) {
        *o++ = tmp[n];
    }
    return o;
}

size_t mon_hex_encode(const uint8_t *data, size_t len, char *out) {
    for (size_t i = 0; i < len; i++) {
        out[2 * i]     = HEX[data[i] >> 4];
        out[2 * i + 1] = HEX[data[i] & 0xF];
    }
    return 2 * len;
}

int mon_hex_decode(const char *s, uint8_t *out, size_t max, size_t *out_len) {
    size_t n = strlen(s);
    if (n % 2 != 0 || n / 2 > max) {
        return -1;
    }
    for (size_t i = 0; i < n; i += 2) {
        int hi = hexval(s[i]);
        int lo = hexval(s[i + 1]);
        if (hi < 0 || lo < 0) {
            return -1;
        }
        out[i / 2] = (uint8_t)((hi << 4) | lo);
    }
    *out_len = n / 2;
    return 0;
}

int mon_parse_hex_u32(const char *s, uint32_t *out) {
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
    }
    if (*s == '\0') {
        return -1;
    }
    uint32_t v = 0;
    for (; *s; s++) {
        int d = hexval(*s);
        if (d < 0) {
            return -1;
        }
        v = (v << 4) | (uint32_t)d;
    }
    *out = v;
    return 0;
}

int mon_parse_dec_u32(const char *s, uint32_t *out) {
    if (*s == '\0') {
        return -1;
    }
    uint32_t v = 0;
    for (; *s; s++) {
        if (*s < '0' || *s > '9') {
            return -1;
        }
        v = v * 10 + (uint32_t)(*s - '0');
    }
    *out = v;
    return 0;
}

const monitor_port_t *monitor_active_port(void) {
    return g_port;
}

static const char *err_name(int code) {
    switch (code) {
        case MONITOR_ERR_BADCMD:   return "badcmd";
        case MONITOR_ERR_BADARG:   return "badarg";
        case MONITOR_ERR_TIMEOUT:  return "timeout";
        case MONITOR_ERR_BUSERR:   return "buserr";
        case MONITOR_ERR_NACK:     return "nack";
        case MONITOR_ERR_BUSY:     return "busy";
        case MONITOR_ERR_NOSUP:    return "nosup";
        case MONITOR_ERR_OVERFLOW: return "overflow";
        default:                   return "internal";
    }
}

static void write_line(const char *buf, size_t len) {
    if (g_port && g_port->uart_write) {
        g_port->uart_write((const uint8_t *)buf, len);
    }
}

// --- response formatting ------------------------------------------------------------

static void emit_ok(uint32_t seq, const char *resp) {
    int n;
    if (resp && resp[0]) {
        n = snprintf(g_out, sizeof g_out, "<%lu OK %s\n", (unsigned long)seq, resp);
    } else {
        n = snprintf(g_out, sizeof g_out, "<%lu OK\n", (unsigned long)seq);
    }
    if (n > 0) {
        if ((size_t)n >= sizeof g_out) {
            n = sizeof g_out - 1;
            g_out[n - 1] = '\n';
        }
        write_line(g_out, (size_t)n);
    }
}

static void emit_err(uint32_t seq, int code) {
    int n = snprintf(g_out, sizeof g_out, "<%lu ERR %d %s\n",
                     (unsigned long)seq, code, err_name(code));
    if (n > 0) {
        write_line(g_out, (size_t)n);
    }
}

// --- plot streams -------------------------------------------------------------------

static int parse_plot_body(const char *body, uint8_t *widths, uint8_t *nfields,
                           uint16_t *total) {
    const char *p = body;
    uint8_t nf = 0;
    uint16_t tot = 0;
    while (*p) {
        while (*p == ' ') {
            p++;
        }
        if (*p == '\0') {
            break;
        }
        const char *fend = p;
        while (*fend && *fend != ' ') {
            fend++;
        }
        // Find the ':' that separates name from type within [p, fend).
        const char *colon = p;
        while (colon < fend && *colon != ':') {
            colon++;
        }
        if (colon >= fend) {
            return -1;   // no type separator in this field
        }
        // colon[1]/colon[2] are safe to read: worst case they are the field's trailing
        // space or the body's NUL terminator, both of which fail type validation below.
        char t0 = colon[1];
        char t1 = colon[2];
        if (t0 != 'u' && t0 != 's' && t0 != 'f') {
            return -1;
        }
        if (t1 != '1' && t1 != '2' && t1 != '4') {
            return -1;
        }
        if (t0 == 'f' && t1 != '4') {
            return -1;
        }
        if (nf >= MON_PLOT_MAX_FIELDS) {
            return -1;
        }
        uint8_t w = (uint8_t)(t1 - '0');
        widths[nf++] = w;
        tot = (uint16_t)(tot + w);
        p = fend;
    }
    if (nf == 0) {
        return -1;
    }
    *nfields = nf;
    *total = tot;
    return 0;
}

static void emit_pd(const plot_stream_t *s) {
    int n = snprintf(g_out, sizeof g_out, "!pd %c %s\n", s->sid, s->body);
    if (n > 0 && (size_t)n < sizeof g_out) {
        write_line(g_out, (size_t)n);
    }
}

// Rebroadcast the definition (every 5 s) if at least PD_PERIOD has elapsed. Cheap when
// not due.
static void plot_rebroadcast(plot_stream_t *s, uint32_t now) {
    if ((uint32_t)(now - s->last_pd_ms) >= MON_PLOT_PD_PERIOD_MS) {
        emit_pd(s);
        s->last_pd_ms = now;
    }
}

static plot_stream_t *plot_find(char sid) {
    for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
        if (g_plots[i].used && g_plots[i].sid == sid) {
            return &g_plots[i];
        }
    }
    return NULL;
}

// Parse and reserve a slot for a new stream (does not emit !pd yet, so the caller can
// still reject a length mismatch and roll back). Returns NULL on bad body or full table.
static plot_stream_t *plot_alloc(const mon_plot_def_t *def, uint32_t now) {
    for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
        if (!g_plots[i].used) {
            plot_stream_t *s = &g_plots[i];
            if (parse_plot_body(def->body, s->widths, &s->nfields, &s->total) != 0) {
                return NULL;
            }
            s->used = true;
            s->sid = def->sid;
            s->body = def->body;
            s->last_pd_ms = now;
            return s;
        }
    }
    return NULL;
}

int monitor_plot(const mon_plot_def_t *def, uint32_t tick,
                 const void *data, size_t len) {
    if (def->sid < '0' || def->sid > '9') {
        return MONITOR_ERR_BADARG;
    }
    uint32_t now = (g_port && g_port->tick_ms) ? g_port->tick_ms() : 0;
    plot_stream_t *s = plot_find(def->sid);
    bool is_new = false;
    if (s == NULL) {
        s = plot_alloc(def, now);
        if (s == NULL) {
            return MONITOR_ERR_BADARG;   // bad definition or no free slot
        }
        is_new = true;
    }
    if (len != s->total) {
        if (is_new) {
            s->used = false;   // roll back the reservation
        }
        return MONITOR_ERR_BADARG;
    }
    if (is_new) {
        emit_pd(s);            // announce the definition now that the sample is valid
    } else {
        plot_rebroadcast(s, now);
    }

    // Hot path: length check done, now nibble-LUT hex into g_out, one uart_write.
    const uint8_t *d = (const uint8_t *)data;
    char *o = g_out;
    *o++ = '!'; *o++ = 'p'; *o++ = 's'; *o++ = ' ';
    *o++ = def->sid; *o++ = ' ';
    o = emit_hex_u32(o, tick);
    *o++ = ' ';
    size_t off = 0;
    for (uint8_t i = 0; i < s->nfields; i++) {
        uint8_t w = s->widths[i];
        // Little-endian struct field re-emitted big-endian: walk bytes in reverse.
        for (int b = (int)w - 1; b >= 0; b--) {
            uint8_t byte = d[off + (size_t)b];
            *o++ = HEX[byte >> 4];
            *o++ = HEX[byte & 0xF];
        }
        off += w;
        if (i + 1 < s->nfields) {
            *o++ = ',';
        }
    }
    *o++ = '\n';
    write_line(g_out, (size_t)(o - g_out));
    return 0;
}

// --- async events -------------------------------------------------------------------

void monitor_eventf(const char *fmt, ...) {
    g_out[0] = '!';
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(g_out + 1, sizeof g_out - 2, fmt, ap);
    va_end(ap);
    if (n < 0) {
        return;
    }
    size_t len = 1 + (size_t)n;
    if (len > MONITOR_LINE_MAX) {
        len = MONITOR_LINE_MAX;   // truncate to the framing limit
    }
    g_out[len++] = '\n';
    write_line(g_out, len);
}

// --- CAN RX drain -------------------------------------------------------------------

static void emit_can_event(const mon_can_frame_t *f) {
    char *o = g_out;
    *o++ = '!'; *o++ = 'c'; *o++ = 'a'; *o++ = 'n'; *o++ = ' ';
    o = emit_dec_u32(o, f->tick_ms);
    *o++ = ' ';
    if (!f->ext && !f->rtr) {
        *o++ = '-';
    } else {
        if (f->ext) {
            *o++ = 'x';
        }
        if (f->rtr) {
            *o++ = 'r';
        }
    }
    *o++ = ' ';
    o = emit_hex_u32(o, f->id);
    *o++ = ' ';
    if (f->rtr) {
        o = emit_dec_u32(o, f->dlc);            // RTR: DLC as a single decimal digit
    } else if (f->dlc == 0) {
        *o++ = '-';                             // zero-length data section
    } else {
        uint8_t dlc = f->dlc > 8 ? 8 : f->dlc;
        o += mon_hex_encode(f->data, dlc, o);
    }
    *o++ = '\n';
    write_line(g_out, (size_t)(o - g_out));
}

static void drain_can(void) {
    mon_can_frame_t f;
    int guard = 0;
    while (guard < 64 && mon_can_rx_pop(&f)) {   // bound work per poll
        guard++;
        if (monitor_can_filter_pass(f.id, f.ext)) {
            emit_can_event(&f);
        }
    }
}

// --- command line processing --------------------------------------------------------

// Tokenize g_line[1..] in place (replace spaces with NUL). tok[0] is the seq token,
// tok[1..] are the command argv. Returns the token count (max 12).
static int tokenize(char **tok) {
    int ntok = 0;
    size_t i = 1;   // skip the leading '>'
    while (i < g_line_len && ntok < 12) {
        while (i < g_line_len && g_line[i] == ' ') {
            i++;
        }
        if (i >= g_line_len) {
            break;
        }
        tok[ntok++] = (char *)&g_line[i];
        while (i < g_line_len && g_line[i] != ' ') {
            i++;
        }
        if (i < g_line_len) {
            g_line[i++] = '\0';
        }
    }
    return ntok;
}

static void process_line(void) {
    if (g_overflow) {
        // Discarded an over-length line. Respond only if a seq was parseable.
        if (g_line_len >= 2 && g_line[0] == '>') {
            g_line[g_line_len] = '\0';
            char *end = (char *)&g_line[1];
            while (*end && *end != ' ') {
                end++;
            }
            char saved = *end;
            *end = '\0';
            uint32_t seq;
            bool ok = (mon_parse_dec_u32((char *)&g_line[1], &seq) == 0 &&
                       seq >= 1 && seq <= 65535);
            *end = saved;
            if (ok) {
                emit_err(seq, MONITOR_ERR_OVERFLOW);
            }
        }
        return;
    }
    if (g_line_len == 0 || g_line[0] != '>') {
        return;   // ignore blank lines and anything not starting with '>'
    }
    g_line[g_line_len] = '\0';

    char *tok[12];
    int ntok = tokenize(tok);
    if (ntok == 0) {
        return;   // just a '>' with nothing after it
    }
    uint32_t seq;
    if (mon_parse_dec_u32(tok[0], &seq) != 0 || seq < 1 || seq > 65535) {
        return;   // no valid seq to echo: stay silent
    }
    if (ntok == 1) {
        emit_err(seq, MONITOR_ERR_BADCMD);   // seq but no command
        return;
    }
    g_resp[0] = '\0';
    int code = monitor_dispatch(ntok - 1, &tok[1], g_resp, sizeof g_resp);
    if (code == 0) {
        emit_ok(seq, g_resp);
    } else {
        emit_err(seq, code);
    }
}

// Feed staged bytes into the line buffer until one full line completes. Returns true
// if a line was dispatched (so the caller stops after one command per poll).
static bool assemble_one(void) {
    while (g_stage_pos < g_stage_len) {
        uint8_t c = g_stage[g_stage_pos++];
        if (c == '\n') {
            process_line();
            g_line_len = 0;
            g_overflow = false;
            return true;
        }
        if (c == '\r') {
            continue;   // tolerate CRLF
        }
        if (g_line_len < MONITOR_LINE_MAX) {
            g_line[g_line_len++] = c;
        } else {
            g_overflow = true;   // keep discarding until the next LF
        }
    }
    return false;
}

// --- public entry points ------------------------------------------------------------

void monitor_init(const monitor_port_t *port) {
    g_port = port;
    g_line_len = 0;
    g_overflow = false;
    g_stage_len = 0;
    g_stage_pos = 0;
    for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
        g_plots[i].used = false;
    }
}

void monitor_poll(void) {
    if (g_port == NULL) {
        return;
    }
    // One uart_read per poll; if the previous poll left staged bytes, consume those
    // first. At most one command is dispatched per poll (SPEC 5.2).
    if (g_stage_pos >= g_stage_len) {
        g_stage_len = g_port->uart_read ? g_port->uart_read(g_stage, sizeof g_stage) : 0;
        g_stage_pos = 0;
    }
    assemble_one();

    drain_can();

    // Rebroadcast plot definitions on their own even if no new samples arrived.
    uint32_t now = g_port->tick_ms ? g_port->tick_ms() : 0;
    for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
        if (g_plots[i].used) {
            plot_rebroadcast(&g_plots[i], now);
        }
    }
}
