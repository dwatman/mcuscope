// monitor.c - core of the portable UART debug monitor.
//
// Assembles lines from the RX byte stream, tokenizes in place, dispatches one
// command per poll (handlers live in monitor_cmds.c), formats the response,
// drains the CAN RX queue into "!can" events, emits async events and typed plot
// samples, and rebroadcasts plot definitions every 5 s.
//
// No HAL/LL/CMSIS here, no floating point, no dynamic allocation. snprintf is
// used only on cold paths (responses and events); the plot hot path hand-rolls
// hex with a nibble table.

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

// TX lines rejected by uart_write (a line that does not fit is dropped and counted).
static uint32_t g_tx_dropped;

// Poll counter driving the clockless !pd rebroadcast (see MON_PLOT_PD_POLLS).
static uint32_t g_pd_polls;

static const char HEX[] = "0123456789ABCDEF";

// --- typed plot registry --------------------------------------------------------------

#define MON_PLOT_MAX_STREAMS 4
#define MON_PLOT_MAX_FIELDS  16
#define MON_PLOT_PD_PERIOD_MS 5000u
// Clockless-port fallback: with no tick_ms the 5 s timer can never fire, so
// rebroadcast every this many monitor_poll() calls instead.
#define MON_PLOT_PD_POLLS 10000u

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
static uint16_t g_plot_rejected;   // bit per sid digit: "!e plot <sid> badarg" sent once

// The plot hot path in monitor_plot() writes into g_out with no per-byte bounds
// check, so prove the worst case fits at compile time (negative array size stands
// in for _Static_assert in C99). This breaks the build if a limit above is ever
// raised past what the line buffer can hold.
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
	// Enforce 7-bit printable ASCII on the way out: an application string reaching a
	// %s in monitor_eventf() or an OK payload could otherwise put a bare LF on the
	// wire and forge a second protocol line. Everything is emitted through here, so
	// one pass covers every path. The final byte is the line's own LF, left alone.
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
			g_tx_dropped++;   // a rejected line is dropped and counted
		}
	}
}

// --- response formatting ------------------------------------------------------------

static void emit_err(uint32_t seq, int code) {
	// Map anything outside the 1..9 table onto "internal": a handler wrapping a
	// driver that answers -EIO or -1 must not put an off-grammar code on the wire.
	if (code < MONITOR_ERR_BADCMD || code > MONITOR_ERR_INTERNAL) {
		code = MONITOR_ERR_INTERNAL;
	}
	int n = snprintf(g_out, sizeof g_out, "<%lu ERR %d %s\n",
					 (unsigned long)seq, code, err_name(code));
	if (n > 0) {
		write_line(g_out, (size_t)n);
	}
}

static void emit_ok(uint32_t seq, const char *resp) {
	int n;
	if (resp && resp[0]) {
		// Bound the read at the buffer size: a handler may leave resp unterminated
		// despite the contract in monitor.h.
		n = snprintf(g_out, sizeof g_out, "<%lu OK %.*s\n", (unsigned long)seq,
					 (int)sizeof g_resp, resp);
	} else {
		n = snprintf(g_out, sizeof g_out, "<%lu OK\n", (unsigned long)seq);
	}
	if (n > 0) {
		if ((size_t)n >= sizeof g_out) {
			// The OK payload would blow the line limit. Never send a truncated
			// payload (it could cut a hex pair in half); answer overflow.
			emit_err(seq, MONITOR_ERR_OVERFLOW);
			return;
		}
		write_line(g_out, (size_t)n);
	}
}

// --- plot streams -------------------------------------------------------------------

static bool is_dec_digit(char c) {
	return c >= '0' && c <= '9';
}

// Advance past one or more digits within [s, end); NULL if there are none.
static const char *skip_digits(const char *s, const char *end) {
	const char *first = s;
	while (s < end && is_dec_digit(*s)) {
		s++;
	}
	return (s == first) ? NULL : s;
}

// Channel/lane name over [s, end): [A-Za-z_][A-Za-z0-9_.]*, 1 to 16 chars.
static bool valid_plot_name(const char *s, const char *end) {
	size_t n = (size_t)(end - s);
	if (n < 1 || n > 16) {
		return false;
	}
	for (size_t i = 0; i < n; i++) {
		char c = s[i];
		bool head = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
		if (!head && !(i > 0 && (is_dec_digit(c) || c == '.'))) {
			return false;
		}
	}
	return true;
}

// Enum label: 1 to 16 chars of [A-Za-z0-9_.].
static bool valid_enum_label(const char *s, const char *end) {
	size_t n = (size_t)(end - s);
	if (n < 1 || n > 16) {
		return false;
	}
	for (size_t i = 0; i < n; i++) {
		char c = s[i];
		if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || is_dec_digit(c)
			  || c == '_' || c == '.')) {
			return false;
		}
	}
	return true;
}

// "*<scale>": -?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?. Grammar only; a literal that
// overflows to infinity ("1e999") is not worth float code here.
static bool valid_plot_scale(const char *s, const char *end) {
	if (s < end && *s == '-') {
		s++;
	}
	s = skip_digits(s, end);
	if (s == NULL) {
		return false;
	}
	if (s < end && *s == '.') {
		s = skip_digits(s + 1, end);
		if (s == NULL) {
			return false;
		}
	}
	if (s < end && (*s == 'e' || *s == 'E')) {
		s++;
		if (s < end && (*s == '+' || *s == '-')) {
			s++;
		}
		s = skip_digits(s, end);
		if (s == NULL) {
			return false;
		}
	}
	return s == end;
}

// Enum body after the '=' sigil: "<v>=<label>[,<v>=<label>...]". A negative
// value needs a signed type.
static bool valid_enum_body(const char *s, const char *end, bool signed_type) {
	for (;;) {
		const char *item_end = s;
		while (item_end < end && *item_end != ',') {
			item_end++;
		}
		const char *eq = s;
		while (eq < item_end && *eq != '=') {
			eq++;
		}
		if (eq == item_end) {
			return false;
		}
		const char *v = s;
		if (v < eq && *v == '-') {
			if (!signed_type) {
				return false;
			}
			v++;
		}
		if (eq - v > 20 || skip_digits(v, eq) != eq) {   // decimal bounded at 20 digits
			return false;
		}
		if (!valid_enum_label(eq + 1, item_end)) {
			return false;
		}
		if (item_end == end) {
			return true;
		}
		s = item_end + 1;
	}
}

// Packed-bits body after the '/' sigil: lane names LSB-first, an empty name
// skips that bit; at most 8*width lanes, at least one named.
static bool valid_bits_body(const char *s, const char *end, unsigned width) {
	unsigned lanes = 0, named = 0;
	for (;;) {
		const char *item_end = s;
		while (item_end < end && *item_end != ',') {
			item_end++;
		}
		if (item_end > s) {
			if (!valid_plot_name(s, item_end)) {
				return false;
			}
			named++;
		}
		lanes++;
		if (item_end == end) {
			break;
		}
		s = item_end + 1;
	}
	return named > 0 && lanes <= 8u * width;
}

// Validate one field past its type: optional "*<scale>", then an optional ":<unit>"
// whose slot may instead carry an enum ('=') or packed-bits ('/') sigil.
static bool valid_field_tail(const char *q, const char *fend, char t0, unsigned width) {
	bool has_scale = false;
	if (q < fend && *q == '*') {
		const char *se = q + 1;
		while (se < fend && *se != ':') {
			se++;
		}
		if (!valid_plot_scale(q + 1, se)) {
			return false;
		}
		has_scale = true;
		q = se;
	}
	if (q == fend) {
		return true;
	}
	if (*q != ':') {
		return false;   // junk between the type and the unit slot
	}
	const char *u = q + 1;
	if (u == fend) {
		return false;   // empty unit
	}
	for (const char *r = u; r < fend; r++) {
		if (*r == ':') {
			return false;   // a field splits on ':' into at most three parts
		}
	}
	if (*u != '=' && *u != '/') {
		// Plain display unit: must be 7-bit printable ASCII, or write_line() would
		// rewrite it to '.' and put a definition on the wire that the application
		// never declared. Refuse it here instead of emitting the mangled form.
		for (const char *r = u; r < fend; r++) {
			unsigned char c = (unsigned char)*r;
			if (c < 0x20 || c > 0x7E) {
				return false;
			}
		}
		return true;
	}
	if (has_scale) {
		return false;   // a scale is meaningless on an enum/bits channel
	}
	if (*u == '=') {
		return t0 != 'f' && valid_enum_body(u + 1, fend, t0 == 's');
	}
	return t0 == 'u' && valid_bits_body(u + 1, fend, width);
}

// Walk the names a body declares, in order: each field's channel name, then every non-empty
// lane name of a packed-bits field. Stack state only, so the uniqueness scan below needs no
// storage proportional to the body.
typedef struct {
	const char *p;          // next byte of the body to scan
	const char *lane;       // next lane name inside a packed-bits list, NULL when outside
	const char *lane_end;   // end of that list
} plot_name_iter_t;

static void name_iter_init(plot_name_iter_t *it, const char *body) {
	it->p = body;
	it->lane = NULL;
	it->lane_end = NULL;
}

// Yield the next name as [*ns, *ne); false when the body is exhausted. Assumes the body
// already passed field validation, so a field always has its ':' and a two-char type.
static bool name_iter_next(plot_name_iter_t *it, const char **ns, const char **ne) {
	while (it->lane != NULL) {
		if (it->lane >= it->lane_end) {
			it->lane = NULL;
			break;
		}
		const char *s = it->lane;
		const char *e = s;
		while (e < it->lane_end && *e != ',') {
			e++;
		}
		it->lane = (e < it->lane_end) ? e + 1 : it->lane_end;
		if (e > s) {
			*ns = s;
			*ne = e;
			return true;   // an empty lane name is a skipped bit, not a name
		}
	}
	while (*it->p == ' ') {
		it->p++;
	}
	if (*it->p == '\0') {
		return false;
	}
	const char *p = it->p;
	const char *fend = p;
	while (*fend && *fend != ' ') {
		fend++;
	}
	it->p = fend;
	const char *colon = p;
	while (colon < fend && *colon != ':') {
		colon++;
	}
	const char *q = colon + 3;   // past ":<type>"
	if (q < fend && *q == '*') {
		while (q < fend && *q != ':') {
			q++;
		}
	}
	if (q < fend && *q == ':' && q + 1 < fend && q[1] == '/') {
		it->lane = q + 2;
		it->lane_end = fend;
	}
	*ns = p;
	*ne = colon;
	return true;
}

// Within one definition, names must be unique, channels and bit lanes together.
// O(n^2) over at most 16 fields plus their lanes, run once per stream at registration.
static bool plot_names_unique(const char *body) {
	plot_name_iter_t outer;
	name_iter_init(&outer, body);
	const char *as, *ae;
	unsigned seen = 0;
	while (name_iter_next(&outer, &as, &ae)) {
		plot_name_iter_t inner;
		name_iter_init(&inner, body);
		const char *bs, *be;
		for (unsigned i = 0; i < seen; i++) {
			if (!name_iter_next(&inner, &bs, &be)) {
				break;
			}
			if (ae - as == be - bs && memcmp(as, bs, (size_t)(ae - as)) == 0) {
				return false;
			}
		}
		seen++;
	}
	return true;
}

// Validate a "!pd" body against the full grammar and cache the field widths. A bad
// body must be refused here: it would be registered forever with nothing visible on
// the target but a 0 return, while its samples are undecodable.
static int parse_plot_body(const char *body, uint8_t *widths, uint8_t *nfields,
						   uint16_t *total) {
	// The "!pd <sid> <body>" line ("!pd X " is 6 chars) must fit the line limit,
	// or emit_pd could never send the definition and the stream would be undecodable.
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
		if (!valid_plot_name(p, colon)) {
			return -1;   // name: [A-Za-z_][A-Za-z0-9_.]*, 1 to 16 chars
		}
		// colon[1] is safe to read: worst case it is the field's trailing space or
		// the body's NUL, both of which fail type validation below. colon[2] is NOT
		// safe unless colon[1] was non-NUL: for a body ending at the separator
		// ("ax:") or after one type char ("a:u"), reading it runs past the string.
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
		if (!valid_field_tail(colon + 3, fend, t0, w)) {
			return -1;
		}
		widths[nf++] = w;
		tot = (uint16_t)(tot + w);
		p = fend;
	}
	if (nf == 0) {
		return -1;
	}
	if (!plot_names_unique(body)) {
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

// A rejected stream is otherwise invisible: applications ignore monitor_plot's return
// value and the stream simply never appears on the host. Say why, once per sid.
// sid '?' is the nameless case (no def, no body, or a sid outside '0'..'9'), latched on
// its own bit since there is no stream to key it by.
static int plot_reject(char sid, const char *why) {
	uint16_t bit = (sid >= '0' && sid <= '9') ? (uint16_t)(1u << (sid - '0')) : (uint16_t)(1u << 10);
	if (!(g_plot_rejected & bit)) {
		g_plot_rejected |= bit;
		monitor_eventf("e plot %c badarg %s", sid, why);
	}
	return MONITOR_ERR_BADARG;
}

static bool plot_table_full(void) {
	for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
		if (!g_plots[i].used) {
			return false;
		}
	}
	return true;
}

int monitor_plot(const mon_plot_def_t *def, uint32_t tick,
				 const void *data, size_t len) {
	if (!def || !def->sid || !def->body || def->sid < '0' || def->sid > '9') {
		return plot_reject('?', "sid");
	}
	uint32_t now = (g_port && g_port->tick_ms) ? g_port->tick_ms() : 0;
	plot_stream_t *s = plot_find(def->sid);
	bool is_new = false;
	if (s == NULL) {
		if (plot_table_full()) {
			return plot_reject(def->sid, "full");   // MON_PLOT_MAX_STREAMS already registered
		}
		s = plot_alloc(def, now);
		if (s == NULL) {
			return plot_reject(def->sid, "def");   // the body failed to parse
		}
		is_new = true;
	} else if (s->body != def->body && strcmp(s->body, def->body) != 0) {
		return plot_reject(def->sid, "body");   // sid already registered with a different body
	}
	if (len != s->total) {
		if (is_new) {
			s->used = false;   // roll back the reservation
		}
		return plot_reject(def->sid, "len");
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

// True if the text's first space-delimited token is exactly "@<digits>", the shape
// parse_marker reads back as a tick.
static bool starts_with_tick_sigil(const char *text) {
	while (*text == ' ') {
		text++;
	}
	if (*text != '@' || !is_dec_digit(text[1])) {
		return false;
	}
	const char *p = text + 1;
	while (is_dec_digit(*p)) {
		p++;
	}
	return *p == '\0' || *p == ' ';
}

int monitor_mark(const char *text) {
	if (!text || !*text) {
		return MONITOR_ERR_BADARG;   // do not spend a line on an empty marker
	}
	const monitor_port_t *port = monitor_active_port();
	// The '@' sigil makes the tick unambiguous against marker text that happens to
	// start with a number; with no clock, omit it.
	if (port && port->tick_ms) {
		monitor_eventf("m @%lu %s", (unsigned long)port->tick_ms(), text);
		return 0;
	}
	if (starts_with_tick_sigil(text)) {
		// No tick of our own to lead with, so text starting with a tick sigil would
		// read back as a tick nobody set; a forged timestamp is worse than no marker.
		return MONITOR_ERR_BADARG;
	}
	monitor_eventf("m %s", text);
	return 0;
}

// --- CAN RX drain -------------------------------------------------------------------

static void emit_can_event(const mon_can_frame_t *f) {
	char *o = g_out;
	*o++ = '!'; *o++ = 'c'; *o++ = 'a'; *o++ = 'n';
	if (f->bus >= 2) {
		*o++ = (char)('0' + f->bus);   // bus 1 is unmarked on the wire
	}
	*o++ = ' ';
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
	// Mask the id to the width the flags declare (like the dlc clamp below): the
	// shim owns id validity, this only keeps a driver slip from emitting an
	// undecodable event.
	o = emit_hex_u32(o, f->id & (f->ext ? 0x1FFFFFFFu : 0x7FFu));
	*o++ = ' ';
	if (f->rtr) {
		// RTR: DLC as a single decimal digit; clamp out-of-range values.
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
	for (;;) {
		// Zero before every pop: a shim is only obliged to set the fields it has,
		// and anything it leaves alone must read as zero, not stack residue.
		memset(&f, 0, sizeof f);
		if (guard >= 64 || !mon_can_rx_pop(&f)) {   // bound work per poll
			break;
		}
		guard++;
		if (f.bus == 0) {
			f.bus = 1;   // a shim that never sets the field is a single-bus shim
		}
		if (f.bus > MON_CAN_BUSES) {
			continue;    // a bus this target did not declare: dropped, never emitted
		}
		if (monitor_can_filter_pass(f.bus, f.id, f.ext)) {
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

	// Commands are 7-bit ASCII. An embedded NUL or a byte with the high bit set
	// would silently truncate or corrupt tokens; reject the whole line (with
	// badarg if a seq is parseable) instead.
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
	g_plot_rejected = 0;
	g_line_len = 0;
	g_overflow = false;
	g_stage_len = 0;
	g_stage_pos = 0;
	g_tx_dropped = 0;
	g_pd_polls = 0;
	for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
		g_plots[i].used = false;
	}
}

void monitor_poll(void) {
	if (g_port == NULL) {
		return;
	}
	// One uart_read per poll; if the previous poll left staged bytes, consume those
	// first. At most one command is dispatched per poll.
	if (g_stage_pos >= g_stage_len) {
		g_stage_len = g_port->uart_read ? g_port->uart_read(g_stage, sizeof g_stage) : 0;
		// Clamp what the port shim claims to have written: a bad return (avail
		// instead of copied, or -1 as SIZE_MAX) would walk assemble_one() off the
		// end of this buffer and feed adjacent SRAM into the command parser.
		if (g_stage_len > sizeof g_stage) {
			g_stage_len = sizeof g_stage;
		}
		g_stage_pos = 0;
	}
	assemble_one();

	drain_can();

	// Rebroadcast plot definitions on their own even if no new samples arrived.
	uint32_t now = g_port->tick_ms ? g_port->tick_ms() : 0;
	bool force_pd = false;
	if (g_port->tick_ms == NULL && ++g_pd_polls >= MON_PLOT_PD_POLLS) {
		g_pd_polls = 0;
		force_pd = true;   // clockless port: count polls, since `now` is stuck at 0
	}
	for (int i = 0; i < MON_PLOT_MAX_STREAMS; i++) {
		if (g_plots[i].used) {
			if (force_pd) {
				emit_pd(&g_plots[i]);
			} else {
				plot_rebroadcast(&g_plots[i], now);
			}
		}
	}
}
