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

// TX lines rejected by uart_write (SPEC 5.2: a line that does not fit is dropped
// and counted).
static uint32_t g_tx_dropped;

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

// The plot hot path in monitor_plot() writes into g_out with no per-byte bounds check,
// so prove the worst case fits at compile time instead: "!ps " + sid + ' ' + up to 8 tick
// hex digits + ' ' + every field as two hex chars per byte (4 bytes max, widths validated
// in parse_plot_body) + one comma between fields + '\n'. C99 has no _Static_assert, so a
// negative array size is the portable stand-in; this breaks the build if a limit above is
// ever raised past what the line buffer can hold.
#define MON_PLOT_WORST_LINE (4 + 1 + 1 + 8 + 1                 \
                             + MON_PLOT_MAX_FIELDS * 8         \
                             + (MON_PLOT_MAX_FIELDS - 1) + 1)
typedef char mon_plot_line_fits[(MON_PLOT_WORST_LINE <= MONITOR_LINE_MAX) ? 1 : -1];

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
        if (v > (UINT32_MAX >> 4)) {
            return -1;   // next shift would overflow 32 bits
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
        uint32_t d = (uint32_t)(*s - '0');
        if (v > (UINT32_MAX - d) / 10) {
            return -1;   // next multiply-add would overflow 32 bits
        }
        v = v * 10 + d;
    }
    *out = v;
    return 0;
}

const monitor_port_t *monitor_active_port(void) {
    return g_port;
}

uint32_t monitor_tx_dropped(void) {
    return g_tx_dropped;
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

static void write_line(char *buf, size_t len) {
    // Enforce SPEC 2.1's "7-bit printable ASCII, both directions" on the way out. The
    // input side already rejects such bytes; the output side did not, so an application
    // string reaching a %s in monitor_eventf() or an OK payload could put a bare LF on
    // the wire and forge a second protocol line (a payload starting '<' or '!' forges a
    // response or an event). Everything is emitted through here, so one pass covers every
    // path. The final byte is the line's own LF terminator and is left alone.
    if (len > 0) {
        for (size_t i = 0; i + 1 < len; i++) {
            unsigned char c = (unsigned char)buf[i];
            if (c < 0x20 || c > 0x7E) {
                buf[i] = '.';
            }
        }
    }
    if (g_port && g_port->uart_write) {
        if (!g_port->uart_write((const uint8_t *)buf, len)) {
            g_tx_dropped++;   // SPEC 5.2: a rejected line is dropped and counted
        }
    }
}

// --- response formatting ------------------------------------------------------------

static void emit_err(uint32_t seq, int code) {
    int n = snprintf(g_out, sizeof g_out, "<%lu ERR %d %s\n",
                     (unsigned long)seq, code, err_name(code));
    if (n > 0) {
        write_line(g_out, (size_t)n);
    }
}

static void emit_ok(uint32_t seq, const char *resp) {
    int n;
    if (resp && resp[0]) {
        n = snprintf(g_out, sizeof g_out, "<%lu OK %s\n", (unsigned long)seq, resp);
    } else {
        n = snprintf(g_out, sizeof g_out, "<%lu OK\n", (unsigned long)seq);
    }
    if (n > 0) {
        if ((size_t)n >= sizeof g_out) {
            // The OK payload would blow the SPEC 2.1 line limit. Never send a
            // truncated payload (it could cut a hex pair in half); answer overflow.
            emit_err(seq, MONITOR_ERR_OVERFLOW);
            return;
        }
        write_line(g_out, (size_t)n);
    }
}

// --- plot streams -------------------------------------------------------------------

static int parse_plot_body(const char *body, uint8_t *widths, uint8_t *nfields,
                           uint16_t *total) {
    // SPEC 2.1/2.5: the "!pd <sid> <body>" line ("!pd X " is 6 chars) must fit the
    // 255-byte content limit, or emit_pd could never send the definition and the
    // stream would be undecodable. Reject such a body at registration time.
    if (strlen(body) > MONITOR_LINE_MAX - 6) {
        return -1;
    }
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
        if (colon == p || (size_t)(colon - p) > 16) {
            return -1;   // SPEC 2.5: channel name is 1 to 16 chars
        }
        // colon[1] is safe to read: worst case it is the field's trailing space or the
        // body's NUL terminator, both of which fail type validation below. colon[2] is
        // NOT safe unless colon[1] was non-NUL - for a body ending at the separator
        // ("ax:") or after one type char ("a:u"), reading it runs one byte past the
        // string. On a target `body` is a .rodata literal so this does not fault, it
        // silently reads the neighbouring literal: if that byte happened to be '1', '2'
        // or '4' the malformed body was accepted with a bogus field width and the stream
        // registered with a layout the host can never decode.
        char t0 = colon[1];
        char t1 = t0 ? colon[2] : '\0';
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
    if (!def || !def->sid || !def->body) {
        return MONITOR_ERR_BADARG;
    }
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
    } else if (s->body != def->body && strcmp(s->body, def->body) != 0) {
        return MONITOR_ERR_BADARG;   // sid already registered with a different body
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
        // RTR: DLC as a single decimal digit (SPEC 2.5); clamp out-of-spec values.
        o = emit_dec_u32(o, f->dlc > 8 ? 8 : f->dlc);
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
// tok[1..] are the command argv. Returns the token count (max 12), or 13 when more
// than 12 tokens are present so the caller can reject the line instead of executing
// a silently truncated argv.
static int tokenize(char **tok) {
    int ntok = 0;
    size_t i = 1;   // skip the leading '>'
    while (i < g_line_len) {
        while (i < g_line_len && g_line[i] == ' ') {
            i++;
        }
        if (i >= g_line_len) {
            break;
        }
        if (ntok == 12) {
            return 13;   // a 13th token exists: too many
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

// Best-effort seq recovery for a line that will be rejected whole (overflow or a
// forbidden byte): parse g_line's first token as a seq so the error can still be
// addressed. g_line must already be NUL-terminated. Returns false if no valid seq.
static bool recover_seq(uint32_t *seq) {
    char *end = (char *)&g_line[1];
    while (*end && *end != ' ') {
        end++;
    }
    char saved = *end;
    *end = '\0';
    bool ok = (mon_parse_dec_u32((char *)&g_line[1], seq) == 0 &&
               *seq >= 1 && *seq <= 65535);
    *end = saved;
    return ok;
}

static void process_line(void) {
    if (g_overflow) {
        // Discarded an over-length line. Respond only if a seq was parseable.
        if (g_line_len >= 2 && g_line[0] == '>') {
            g_line[g_line_len] = '\0';
            uint32_t seq;
            if (recover_seq(&seq)) {
                emit_err(seq, MONITOR_ERR_OVERFLOW);
            }
        }
        return;
    }
    if (g_line_len == 0 || g_line[0] != '>') {
        return;   // ignore blank lines and anything not starting with '>'
    }
    g_line[g_line_len] = '\0';

    // SPEC 2.1: commands are 7-bit ASCII. An embedded NUL or a byte with the high
    // bit set would silently truncate or corrupt tokens; reject the whole line
    // (with badarg if a seq is parseable) instead.
    for (size_t i = 0; i < g_line_len; i++) {
        if (g_line[i] == '\0' || g_line[i] > 0x7F) {
            uint32_t bseq;
            if (recover_seq(&bseq)) {
                emit_err(bseq, MONITOR_ERR_BADARG);
            }
            return;
        }
    }

    char *tok[12];
    int ntok = tokenize(tok);
    if (ntok == 0) {
        return;   // just a '>' with nothing after it
    }
    uint32_t seq;
    if (mon_parse_dec_u32(tok[0], &seq) != 0 || seq < 1 || seq > 65535) {
        return;   // no valid seq to echo: stay silent
    }
    if (ntok > 12) {
        emit_err(seq, MONITOR_ERR_BADARG);   // more than 12 tokens: reject, do not truncate
        return;
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
    g_tx_dropped = 0;
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
        // Clamp what the port shim claims to have written. Two common shim slips - a ring
        // buffer that copies min(max, avail) but returns avail, and an int-returning driver
        // whose -1 error becomes SIZE_MAX - would otherwise walk assemble_one() off the end
        // of this 64-byte static and feed adjacent SRAM into the command parser.
        if (g_stage_len > sizeof g_stage) {
            g_stage_len = sizeof g_stage;
        }
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
