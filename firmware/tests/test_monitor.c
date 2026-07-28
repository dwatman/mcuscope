// test_monitor.c - host-compiled unit tests for the monitor core (SPEC 5 acceptance).
//
// Mirrors the host protocol suite: ping/info, every bus command, badcmd/badarg/nosup,
// overflow discard, tokenizer edge cases, a registered custom command, CAN event
// emission with filtering, and monitor_plot typed-stream encoding plus the 5 s !pd
// rebroadcast. Feeds bytes through the fake UART, polls the monitor, and compares the
// captured TX bytes to the exact expected wire output. Exits non-zero on any mismatch.
//
// Also covers the code-review follow-ups: parser overflow rejection (hex/dec), the
// exact 255-content-byte/256-total-byte line-length boundary (SPEC 2.1), monitor_eventf
// truncation, monitor_register duplicate/table-full rejection, the >12-token tokenizer
// clamp, and the CAN drain's dlc>8 clamp.

#include "../monitor/monitor.h"

#include <stdio.h>
#include <string.h>

// Fakes and control hooks from fake_shims.c.
size_t   fake_uart_read(uint8_t *buf, size_t max);
bool     fake_uart_write(const uint8_t *buf, size_t len);
uint32_t fake_tick_ms(void);
void     fake_reset(void);
void     fake_tx_reset(void);
void     fake_feed(const char *s);
void     fake_feed_raw(const void *p, size_t n);
void     fake_tx_set_reject(bool reject);
void     fake_i2c_set_all_ack(bool all_ack);
const char *fake_tx(void);
void     fake_set_tick(uint32_t t);
void     fake_can_reset(void);
void     fake_can_push(const mon_can_frame_t *f);
const mon_can_frame_t *fake_can_last_tx(void);

static const monitor_port_t g_port = {
    .uart_read  = fake_uart_read,
    .uart_write = fake_uart_write,
    .tick_ms    = fake_tick_ms,
    .name       = "testmon",
};

static int g_total;
static int g_fail;

#define POLLS 200

static void run(void) {
    for (int i = 0; i < POLLS; i++) {
        monitor_poll();
    }
}

// Reset the fakes and the monitor between cases. Registered commands and the CAN filter
// persist across monitor_init, so tests that care set them explicitly.
static void reset_all(void) {
    fake_reset();
    fake_can_reset();
    monitor_init(&g_port);
}

static void check(const char *label, const char *got, const char *want) {
    g_total++;
    if (strcmp(got, want) != 0) {
        g_fail++;
        printf("FAIL %s\n  want: [%s]\n  got:  [%s]\n", label, want, got);
    } else {
        printf("ok   %s\n", label);
    }
}

static void check_int(const char *label, long got, long want) {
    g_total++;
    if (got != want) {
        g_fail++;
        printf("FAIL %s\n  want: %ld\n  got:  %ld\n", label, want, got);
    } else {
        printf("ok   %s\n", label);
    }
}

// Feed one command line, poll, and compare the captured response.
static void expect_cmd(const char *label, const char *line, const char *want) {
    reset_all();
    fake_feed(line);
    run();
    check(label, fake_tx(), want);
}

// A registered application command: `sensor cal` -> OK cal ok.
static int h_sensor(int argc, char **argv, char *resp, size_t resp_max) {
    if (argc >= 2 && strcmp(argv[1], "cal") == 0) {
        snprintf(resp, resp_max, "cal ok");
        return 0;
    }
    return MONITOR_ERR_BADARG;
}

// A registered command with a caller-chosen payload size: `longresp <n>` writes n 'x'
// chars into resp, to drive emit_ok up to and past the line-length limit.
static int h_longresp(int argc, char **argv, char *resp, size_t resp_max) {
    uint32_t n;
    if (argc < 2 || mon_parse_dec_u32(argv[1], &n) != 0 || n >= resp_max) {
        return MONITOR_ERR_BADARG;
    }
    memset(resp, 'x', n);
    resp[n] = '\0';
    return 0;
}

static void test_basic(void) {
    fake_set_tick(1234);
    expect_cmd("ping", ">1 ping\n", "<1 OK monitor 1 testmon\n");
    fake_set_tick(1234);
    expect_cmd("info", ">2 info\n", "<2 OK up=1234\n");
    expect_cmd("badcmd", ">3 frobnicate\n", "<3 ERR 1 badcmd\n");
    expect_cmd("badcmd-family", ">4 can wobble\n", "<4 ERR 1 badcmd\n");
    expect_cmd("badarg-argcount", ">5 i2c rd 48\n", "<5 ERR 2 badarg\n");
    expect_cmd("nosup-weak-default", ">6 spi xfer imu AABB\n", "<6 ERR 7 nosup\n");
    expect_cmd("seq-only", ">7\n", "<7 ERR 1 badcmd\n");
}

static void test_i2c(void) {
    expect_cmd("i2c scan", ">1 i2c scan\n", "<1 OK 48 50\n");
    expect_cmd("i2c rd", ">2 i2c rd 48 2\n", "<2 OK 0642\n");
    expect_cmd("i2c wr", ">3 i2c wr 50 00AABB\n", "<3 OK\n");
    expect_cmd("i2c wrrd", ">4 i2c wrrd 50 00 2\n", "<4 OK A0A1\n");
    expect_cmd("i2c rd nack", ">5 i2c rd 60 2\n", "<5 ERR 5 nack\n");
    expect_cmd("i2c rd bad-n", ">6 i2c rd 48 0\n", "<6 ERR 2 badarg\n");
}

static void test_gpio_adc(void) {
    expect_cmd("gpio set", ">1 gpio set led 1\n", "<1 OK\n");
    expect_cmd("gpio get", ">2 gpio get led\n", "<2 OK 0\n");   // fresh state after reset
    expect_cmd("gpio bad name", ">3 gpio get nope\n", "<3 ERR 2 badarg\n");
    expect_cmd("gpio bad level", ">4 gpio set led 2\n", "<4 ERR 2 badarg\n");
    expect_cmd("adc read", ">5 adc read vref\n", "<5 OK raw=2048 mv=3300\n");
    expect_cmd("adc bad name", ">6 adc read nope\n", "<6 ERR 2 badarg\n");
}

static void test_can_cmds(void) {
    reset_all();
    fake_feed(">1 can tx 100 DEADBEEF\n");
    run();
    check("can tx ok", fake_tx(), "<1 OK\n");
    const mon_can_frame_t *tx = fake_can_last_tx();
    check_int("can tx id", tx ? (long)tx->id : -1, 0x100);
    check_int("can tx dlc", tx ? (long)tx->dlc : -1, 4);
    check_int("can tx rtr", tx ? (long)tx->rtr : -1, 0);
    check_int("can tx byte0", tx ? (long)tx->data[0] : -1, 0xDE);

    reset_all();
    fake_feed(">2 can tx 1A3 4 r\n");
    run();
    check("can tx rtr", fake_tx(), "<2 OK\n");
    tx = fake_can_last_tx();
    check_int("can tx rtr flag", tx ? (long)tx->rtr : -1, 1);
    check_int("can tx rtr dlc", tx ? (long)tx->dlc : -1, 4);

    expect_cmd("can tx badarg", ">3 can tx\n", "<3 ERR 2 badarg\n");
    expect_cmd("can tx bad hex", ">4 can tx 100 ZZ\n", "<4 ERR 2 badarg\n");
    expect_cmd("can tx zero-len", ">5 can tx 100 -\n", "<5 OK\n");
    expect_cmd("can stat", ">6 can stat\n", "<6 OK rx=10 tx=3 err=0 state=active\n");
}

static void test_registered(void) {
    expect_cmd("registered cmd", ">1 sensor cal\n", "<1 OK cal ok\n");
    expect_cmd("registered badarg", ">2 sensor bogus\n", "<2 ERR 2 badarg\n");
}

static void test_tokenizer(void) {
    expect_cmd("extra spaces", ">1   ping\n", "<1 OK monitor 1 testmon\n");
    expect_cmd("trailing spaces", ">2 ping  \n", "<2 OK monitor 1 testmon\n");
    expect_cmd("ignore non-cmd", "hello world\n", "");
    expect_cmd("crlf tolerated", ">3 ping\r\n", "<3 OK monitor 1 testmon\n");
}

static void test_overflow(void) {
    char line[400];
    // Over-length line that starts with a parseable seq: expect ERR 8 overflow.
    int n = snprintf(line, sizeof line, ">42 ");
    memset(line + n, 'A', 320);
    line[n + 320] = '\n';
    line[n + 321] = '\0';
    reset_all();
    fake_feed(line);
    run();
    check("overflow with seq", fake_tx(), "<42 ERR 8 overflow\n");

    // Over-length line not starting with '>': silent.
    memset(line, 'A', 320);
    line[320] = '\n';
    line[321] = '\0';
    reset_all();
    fake_feed(line);
    run();
    check("overflow no seq silent", fake_tx(), "");
}

static void push_frame(uint32_t id, uint8_t dlc, const uint8_t *data,
                       bool ext, bool rtr, uint32_t tick) {
    mon_can_frame_t f;
    memset(&f, 0, sizeof f);
    f.id = id;
    f.dlc = dlc;
    f.ext = ext;
    f.rtr = rtr;
    f.tick_ms = tick;
    if (data && !rtr) {
        memcpy(f.data, data, dlc);
    }
    fake_can_push(&f);
}

static void test_can_events(void) {
    // Normalize the filter to "all" before pushing frames.
    reset_all();
    fake_feed(">1 can filter all\n");
    run();

    fake_tx_reset();
    const uint8_t d4[4] = {0x00, 0x00, 0x00, 0x01};
    push_frame(0x100, 4, d4, false, false, 50);
    monitor_poll();
    check("can event normal", fake_tx(), "!can 50 - 100 00000001\n");

    fake_tx_reset();
    const uint8_t d1[1] = {0xAA};
    push_frame(0x1ABCDE, 1, d1, true, false, 7);
    monitor_poll();
    check("can event ext", fake_tx(), "!can 7 x 1ABCDE AA\n");

    fake_tx_reset();
    push_frame(0x200, 3, NULL, false, true, 9);
    monitor_poll();
    check("can event rtr", fake_tx(), "!can 9 r 200 3\n");

    fake_tx_reset();
    push_frame(0x55, 0, NULL, false, false, 1);
    monitor_poll();
    check("can event zero-len", fake_tx(), "!can 1 - 55 -\n");
}

static void test_can_filter(void) {
    reset_all();
    fake_feed(">1 can filter 100 700\n");
    run();
    check("can filter set", fake_tx(), "<1 OK\n");

    fake_tx_reset();
    const uint8_t d1[1] = {0x07};
    push_frame(0x100, 1, d1, false, false, 3);   // (100 & 700)==(100 & 700): pass
    const uint8_t d2[1] = {0x09};
    push_frame(0x200, 1, d2, false, false, 4);   // (200 & 700)!=(100 & 700): drop
    monitor_poll();
    check("can filter mask", fake_tx(), "!can 3 - 100 07\n");

    reset_all();
    fake_feed(">1 can filter none\n");
    run();
    fake_tx_reset();
    push_frame(0x100, 1, d1, false, false, 3);
    monitor_poll();
    check("can filter none", fake_tx(), "");
}

static void test_plot(void) {
    // Three s2 fields: negative value FC01, plus 0200 and 4000. Little-endian input.
    reset_all();
    fake_set_tick(0);
    mon_plot_def_t d0 = {.sid = '0', .body = "ax:s2 ay:s2 az:s2"};
    const uint8_t data0[6] = {0x01, 0xFC, 0x00, 0x02, 0x00, 0x40};
    int rc = monitor_plot(&d0, 0x12D687, data0, sizeof data0);
    check_int("plot s2 rc", rc, 0);
    check("plot s2", fake_tx(),
          "!pd 0 ax:s2 ay:s2 az:s2\n!ps 0 12D687 FC01,0200,4000\n");

    // f4 bit pattern: IEEE754 1.0 -> 0x3F800000.
    reset_all();
    fake_set_tick(0);
    mon_plot_def_t d1 = {.sid = '1', .body = "v:f4"};
    float one = 1.0f;
    uint8_t dataf[4];
    memcpy(dataf, &one, 4);   // little-endian struct bytes on the host
    rc = monitor_plot(&d1, 0x10, dataf, sizeof dataf);
    check_int("plot f4 rc", rc, 0);
    check("plot f4", fake_tx(), "!pd 1 v:f4\n!ps 1 10 3F800000\n");

    // Length mismatch is rejected and rolled back (no !pd emitted).
    reset_all();
    fake_set_tick(0);
    mon_plot_def_t d2 = {.sid = '2', .body = "a:s2"};
    const uint8_t bad[3] = {0, 0, 0};
    rc = monitor_plot(&d2, 0, bad, sizeof bad);
    check_int("plot len mismatch rc", rc, MONITOR_ERR_BADARG);
    check("plot len mismatch silent", fake_tx(), "");

    // Bad definition is rejected.
    reset_all();
    mon_plot_def_t d3 = {.sid = '3', .body = "a:z9"};
    const uint8_t two[2] = {0, 0};
    rc = monitor_plot(&d3, 0, two, sizeof two);
    check_int("plot bad def rc", rc, MONITOR_ERR_BADARG);

    // Enum/bits metadata rides through the body untouched: parse_plot_body reads only
    // the ":type" token for width and never looks past it, so the trailing "=..."/"/..."
    // spec text is carried verbatim into the emitted !pd line.
    reset_all();
    mon_plot_def_t de = {.sid = '4', .body = "state:u1:=0=IDLE,1=ARMED"};
    uint8_t es = 1;
    int rce = monitor_plot(&de, 0x20, &es, sizeof es);
    check_int("plot enum rc", rce, 0);
    check("plot enum", fake_tx(), "!pd 4 state:u1:=0=IDLE,1=ARMED\n!ps 4 20 01\n");

    reset_all();
    mon_plot_def_t db = {.sid = '5', .body = "gpio:u1:/led,irq,pwm_en"};
    uint8_t bs = 0x05;
    int rcb = monitor_plot(&db, 0x21, &bs, sizeof bs);
    check_int("plot bits rc", rcb, 0);
    check("plot bits", fake_tx(), "!pd 5 gpio:u1:/led,irq,pwm_en\n!ps 5 21 05\n");
}

static void test_plot_rebroadcast(void) {
    reset_all();
    fake_set_tick(0);
    mon_plot_def_t d = {.sid = '3', .body = "a:u2"};
    const uint8_t data[2] = {0x34, 0x12};   // little-endian 0x1234 -> BE 1234
    monitor_plot(&d, 0, data, sizeof data);
    check("plot initial", fake_tx(), "!pd 3 a:u2\n!ps 3 0 1234\n");

    // Advancing the fake clock past 5 s makes monitor_poll re-emit the definition.
    fake_tx_reset();
    fake_set_tick(5000);
    monitor_poll();
    check("plot 5s rebroadcast", fake_tx(), "!pd 3 a:u2\n");
}

// --- parser overflow rejection -------------------------------------------------------

static void test_parser_overflow(void) {
    // Hex address that would silently wrap to 0x48 (a valid, present address) without
    // the overflow guard in mon_parse_hex_u32. With the guard the parse itself fails,
    // so cmd_i2c_rd reports badarg rather than treating it as address 0x48.
    expect_cmd("hex overflow rejected", ">1 i2c rd 100000048 2\n", "<1 ERR 2 badarg\n");

    // Decimal seq that would silently wrap to 1 (2^32 + 1) without the overflow guard
    // in mon_parse_dec_u32. With the guard the seq parse fails, so process_line stays
    // silent rather than accepting the wrapped value as a valid seq.
    expect_cmd("seq overflow silent", ">4294967297 ping\n", "");
}

// --- exact line-length boundary (SPEC 2.1: 255 content bytes + LF = 256 wire bytes) --

static void test_line_length_boundary(void) {
    char line[400];

    // 254 content bytes ('>1 ping' followed by padding spaces): well under the limit.
    int n = snprintf(line, sizeof line, ">1 ping");
    memset(line + n, ' ', (size_t)(254 - n));
    line[254] = '\n';
    line[255] = '\0';
    reset_all();
    fake_feed(line);
    run();
    check("254 content bytes accepted", fake_tx(), "<1 OK monitor 1 testmon\n");

    // 255 content bytes: exactly at the limit, still accepted (no overflow).
    n = snprintf(line, sizeof line, ">1 ping");
    memset(line + n, ' ', (size_t)(255 - n));
    line[255] = '\n';
    line[256] = '\0';
    reset_all();
    fake_feed(line);
    run();
    check("255 content bytes accepted", fake_tx(), "<1 OK monitor 1 testmon\n");

    // 256 content bytes: one over the limit, discarded with ERR 8 overflow.
    n = snprintf(line, sizeof line, ">1 ping");
    memset(line + n, ' ', (size_t)(256 - n));
    line[256] = '\n';
    line[257] = '\0';
    reset_all();
    fake_feed(line);
    run();
    check("256 content bytes overflow", fake_tx(), "<1 ERR 8 overflow\n");
}

// --- monitor_eventf: normal formatting and the truncation branch --------------------

static void test_eventf(void) {
    reset_all();
    monitor_eventf("hello %d", 42);
    check("eventf normal", fake_tx(), "!hello 42\n");

    // A 300-char payload cannot fit; the event line is truncated to exactly the
    // 255-content-byte framing limit (254 formatted chars + the leading '!'), plus LF.
    reset_all();
    char big[400];
    memset(big, 'x', sizeof big);
    big[300] = '\0';
    monitor_eventf("%s", big);
    char want[260];
    want[0] = '!';
    memset(want + 1, 'x', 254);
    want[255] = '\n';
    want[256] = '\0';
    check("eventf truncated", fake_tx(), want);
}

// --- monitor_mark: tick sigil, sanitizing, and the empty-text guard -----------------

static void test_mark(void) {
    reset_all();
    fake_set_tick(12345);
    monitor_mark("calibration start");
    check("mark carries an @tick", fake_tx(), "!m @12345 calibration start\n");

    // Text starting with a bare number keeps its first word: the '@' is what makes the
    // tick unambiguous, so the host cannot mistake "12" for one (SPEC 2.5).
    reset_all();
    fake_set_tick(7);
    monitor_mark("12 cells balanced");
    check("mark keeps a leading number", fake_tx(), "!m @7 12 cells balanced\n");

    // Marker text is free-form and often built at runtime, so an embedded newline must
    // not be able to forge a second protocol line; write_line() flattens it.
    reset_all();
    monitor_mark("two\nlines");
    check("mark cannot forge a line", fake_tx(), "!m @7 two.lines\n");

    // Nothing to say, nothing on the wire.
    reset_all();
    monitor_mark("");
    check("empty mark emits nothing", fake_tx(), "");
    reset_all();
    monitor_mark(NULL);
    check("null mark emits nothing", fake_tx(), "");
}

// --- monitor_register: duplicate name and table-full rejection ----------------------

static int h_extra(int argc, char **argv, char *resp, size_t resp_max) {
    (void)argc; (void)argv; (void)resp; (void)resp_max;
    return 0;
}

static void test_registry_limits(void) {
    // "sensor" was already registered once in main(); registering it again must fail.
    check_int("duplicate name rejected", monitor_register("sensor", h_sensor), 0);

    // MON_REG_SLOTS is 8; "sensor" and "longresp" already occupy two. Fill the rest.
    static const char *names[] = {"e1", "e2", "e3", "e4", "e5", "e6"};
    for (size_t i = 0; i < sizeof names / sizeof names[0]; i++) {
        check_int(names[i], monitor_register(names[i], h_extra), 1);
    }
    // The table is now full (8/8): the 9th registration must be rejected.
    check_int("table full rejected", monitor_register("e7", h_extra), 0);
}

// --- tokenizer limit: a >12-token line is rejected, never silently truncated ----------

static void test_token_clamp(void) {
    // seq + 14 words = 15 tokens, well past the 12-token cap: badarg rather than
    // executing a truncated argv.
    expect_cmd("over-12-tokens badarg",
              ">1 zz a b c d e f g h i j k l m\n",
              "<1 ERR 2 badarg\n");
    // Exactly 12 tokens (seq + 11 words) still dispatches normally.
    expect_cmd("exactly-12-tokens dispatched",
              ">2 zz a b c d e f g h i j\n",
              "<2 ERR 1 badcmd\n");
}

// --- SPEC 2.1 byte validation: NUL and non-7-bit-ASCII bytes are rejected -------------

static void test_bad_bytes(void) {
    // Embedded NUL: without validation the tokenizer would silently truncate at it.
    static const char nul_line[] = ">1 pi\0ng\n";
    reset_all();
    fake_feed_raw(nul_line, sizeof nul_line - 1);
    run();
    check("NUL byte badarg", fake_tx(), "<1 ERR 2 badarg\n");

    // High-bit byte: not 7-bit ASCII.
    expect_cmd("non-ascii badarg", ">2 ping\x80\n", "<2 ERR 2 badarg\n");

    // Bad byte with no parseable seq: stay silent, like other unaddressable garbage.
    expect_cmd("non-ascii no seq silent", ">x pi\x80ng\n", "");
}

// --- CAN drain: dlc>8 clamp must not read past the 8-byte data array ----------------

static void test_can_dlc_clamp(void) {
    reset_all();
    fake_feed(">1 can filter all\n");
    run();
    fake_tx_reset();

    mon_can_frame_t f;
    memset(&f, 0, sizeof f);
    f.id = 0x300;
    f.dlc = 12;   // out-of-spec value; emit_can_event must clamp its data read to 8
    for (int i = 0; i < 8; i++) {
        f.data[i] = (uint8_t)i;
    }
    f.tick_ms = 11;
    fake_can_push(&f);
    monitor_poll();
    check("can dlc clamp", fake_tx(), "!can 11 - 300 0001020304050607\n");

    // RTR path: hardware can report dlc up to 15; the emitted DLC token must stay a
    // single decimal digit (SPEC 2.5), so it is clamped to 8 like the data path.
    fake_tx_reset();
    push_frame(0x200, 12, NULL, false, true, 21);
    monitor_poll();
    check("can rtr dlc clamp", fake_tx(), "!can 21 r 200 8\n");
}

// --- can tx id range: 11-bit standard, 29-bit extended --------------------------------

static void test_can_tx_id_range(void) {
    expect_cmd("can tx std id max", ">1 can tx 7FF -\n", "<1 OK\n");
    expect_cmd("can tx std id over", ">2 can tx 800 -\n", "<2 ERR 2 badarg\n");
    expect_cmd("can tx ext id ok", ">3 can tx 800 - x\n", "<3 OK\n");
    expect_cmd("can tx ext id max", ">4 can tx 1FFFFFFF - x\n", "<4 OK\n");
    expect_cmd("can tx ext id over", ">5 can tx 20000000 - x\n", "<5 ERR 2 badarg\n");
}

// --- TX drop counter: a line uart_write rejects is dropped and counted (SPEC 5.2) ----

static void test_tx_drop_counter(void) {
    reset_all();
    check_int("tx dropped starts 0", (long)monitor_tx_dropped(), 0);

    fake_tx_set_reject(true);
    fake_feed(">1 ping\n");
    run();
    check("rejected line not sent", fake_tx(), "");
    check_int("tx dropped counts reject", (long)monitor_tx_dropped(), 1);

    fake_tx_set_reject(false);
    fake_feed(">2 ping\n");
    run();
    check("tx resumes after reject", fake_tx(), "<2 OK monitor 1 testmon\n");
    check_int("tx dropped unchanged", (long)monitor_tx_dropped(), 1);
}

// --- emit_ok overflow: an over-long OK payload answers ERR 8, never a truncated OK ---

// A stuck-low SDA makes every one of the 112 sweep addresses ACK. The full list would
// not fit the SPEC 2.1 line limit once emit_ok prepends "<SEQ OK ", and cmd_i2c_scan used
// to clamp against its buffer rather than the wire budget - so the emitter answered
// `ERR 8 overflow` carrying no address information at all, for exactly the fault the
// command exists to diagnose. It must return a well-formed (shorter) OK list instead.
static void test_i2c_scan_bus_shorted(void) {
    reset_all();
    fake_i2c_set_all_ack(true);
    fake_feed(">65535 i2c scan\n");
    monitor_poll();
    const char *tx = fake_tx();
    size_t len = strlen(tx);
    check_int("shorted-bus scan is OK not overflow", strstr(tx, " ERR ") == NULL, 1);
    check_int("shorted-bus scan starts with OK", strncmp(tx, "<65535 OK 08 09 0A", 18) == 0, 1);
    check_int("shorted-bus scan within line limit", len <= MONITOR_LINE_MAX + 1, 1);
    check_int("shorted-bus scan ends with LF", len > 0 && tx[len - 1] == '\n', 1);
    // Truncation must land on a whole token, never half a hex pair.
    check_int("shorted-bus scan token-aligned", len >= 4 && tx[len - 4] == 0x20, 1);
    fake_i2c_set_all_ack(false);
}

static void test_ok_overflow(void) {
    // "<1 OK " is 6 chars; a 249-char payload makes exactly 255 content bytes: fits.
    char want[300];
    int n = snprintf(want, sizeof want, "<1 OK ");
    memset(want + n, 'x', 249);
    want[n + 249] = '\n';
    want[n + 250] = '\0';
    expect_cmd("ok payload exactly fits", ">1 longresp 249\n", want);

    // One more char would make a 256-content-byte line: overflow, not a chopped OK.
    expect_cmd("ok payload overflow", ">2 longresp 250\n", "<2 ERR 8 overflow\n");
}

// --- plot registration guards: name length and !pd line length -----------------------

static void test_plot_registration_guards(void) {
    uint8_t b1 = 0;

    // Channel name longer than 16 chars is rejected (SPEC 2.5).
    reset_all();
    mon_plot_def_t dn = {.sid = '0', .body = "a_very_long_name_x:u1"};
    check_int("plot name >16 rejected", monitor_plot(&dn, 0, &b1, 1),
              MONITOR_ERR_BADARG);
    check("plot name >16 silent", fake_tx(), "");

    // A 16-char name is still accepted.
    reset_all();
    mon_plot_def_t dok = {.sid = '0', .body = "sixteen_chars_ab:u1"};
    check_int("plot name 16 ok", monitor_plot(&dok, 0, &b1, 1), 0);
    check("plot name 16 emits", fake_tx(),
          "!pd 0 sixteen_chars_ab:u1\n!ps 0 0 00\n");

    // A missing name is rejected.
    reset_all();
    mon_plot_def_t dm = {.sid = '0', .body = ":u1"};
    check_int("plot empty name rejected", monitor_plot(&dm, 0, &b1, 1),
              MONITOR_ERR_BADARG);

    // A body truncated at (or just after) the type separator must be rejected without
    // reading past the end of the string. parse_plot_body used to load colon[2]
    // unconditionally, which is one byte past the terminator for these bodies; under
    // ASAN that is a heap-buffer-overflow, and on a target it silently read whatever
    // followed the .rodata literal - accepting the body outright if that byte was
    // '1', '2' or '4'. Run `make asan` to exercise the bounds.
    reset_all();
    mon_plot_def_t dt0 = {.sid = '0', .body = "ax:"};
    check_int("plot body ends at colon rejected", monitor_plot(&dt0, 0, &b1, 1),
              MONITOR_ERR_BADARG);
    reset_all();
    mon_plot_def_t dt1 = {.sid = '0', .body = "a:u"};
    check_int("plot body truncated type rejected", monitor_plot(&dt1, 0, &b1, 1),
              MONITOR_ERR_BADARG);
    reset_all();
    mon_plot_def_t dt2 = {.sid = '0', .body = "ax:s2 ay:"};
    check_int("plot last field truncated rejected", monitor_plot(&dt2, 0, &b1, 1),
              MONITOR_ERR_BADARG);

    // A body whose "!pd" line would exceed the 255-byte content limit must fail at
    // registration instead of registering a stream emit_pd can never announce.
    reset_all();
    char big[260];
    memset(big, 'x', 250);
    big[250] = '\0';
    mon_plot_def_t dbig = {.sid = '0', .body = big};
    check_int("plot pd too long rejected", monitor_plot(&dbig, 0, &b1, 1),
              MONITOR_ERR_BADARG);
    check("plot pd too long silent", fake_tx(), "");

    // The longest body that still fits (249 chars: 6-char "!pd X " prefix + 249 = 255)
    // registers fine; the unit tail after the type rides through unparsed.
    reset_all();
    static char okb[260];
    int m = snprintf(okb, sizeof okb, "a:u1:");
    memset(okb + m, 'u', (size_t)(249 - m));
    okb[249] = '\0';
    mon_plot_def_t dmax = {.sid = '0', .body = okb};
    check_int("plot pd max len ok", monitor_plot(&dmax, 0, &b1, 1), 0);
}

// --- plot sid reuse: conflicting redefinition is an error, identical body a no-op ----

static void test_plot_sid_conflict(void) {
    reset_all();
    fake_set_tick(0);
    mon_plot_def_t a = {.sid = '7', .body = "a:u2"};
    const uint8_t d2[2] = {0x34, 0x12};
    check_int("plot sid first", monitor_plot(&a, 0, d2, sizeof d2), 0);

    // Same sid, different body: rejected, the old definition stays in force.
    fake_tx_reset();
    mon_plot_def_t b = {.sid = '7', .body = "b:u2"};
    check_int("plot sid conflict rc", monitor_plot(&b, 1, d2, sizeof d2),
              MONITOR_ERR_BADARG);
    check("plot sid conflict silent", fake_tx(), "");

    // Same sid, identical body via a distinct pointer: no-op success.
    fake_tx_reset();
    static const char same_body[] = "a:u2";
    mon_plot_def_t c = {.sid = '7', .body = same_body};
    check_int("plot sid same body rc", monitor_plot(&c, 2, d2, sizeof d2), 0);
    check("plot sid same body sample", fake_tx(), "!ps 7 2 1234\n");
}

// --- plot registry exhaustion: the 5th concurrent stream is rejected -----------------

static void test_plot_registry_full(void) {
    reset_all();
    uint8_t b = 0;
    static const mon_plot_def_t defs[4] = {
        {.sid = '0', .body = "a:u1"},
        {.sid = '1', .body = "b:u1"},
        {.sid = '2', .body = "c:u1"},
        {.sid = '3', .body = "d:u1"},
    };
    for (int i = 0; i < 4; i++) {
        check_int("plot slot fills", monitor_plot(&defs[i], 0, &b, 1), 0);
    }
    mon_plot_def_t d5 = {.sid = '4', .body = "e:u1"};
    check_int("plot 5th stream rejected", monitor_plot(&d5, 0, &b, 1),
              MONITOR_ERR_BADARG);
}

// --- plot field limit: 16 fields fit, a 17th is rejected ------------------------------

static void test_plot_field_limit(void) {
    reset_all();
    static const char b16[] =
        "f01:u1 f02:u1 f03:u1 f04:u1 f05:u1 f06:u1 f07:u1 f08:u1 "
        "f09:u1 f10:u1 f11:u1 f12:u1 f13:u1 f14:u1 f15:u1 f16:u1";
    mon_plot_def_t d16 = {.sid = '8', .body = b16};
    uint8_t data16[16] = {0};
    check_int("plot 16 fields ok", monitor_plot(&d16, 0, data16, sizeof data16), 0);

    reset_all();
    static const char b17[] =
        "f01:u1 f02:u1 f03:u1 f04:u1 f05:u1 f06:u1 f07:u1 f08:u1 "
        "f09:u1 f10:u1 f11:u1 f12:u1 f13:u1 f14:u1 f15:u1 f16:u1 f17:u1";
    mon_plot_def_t d17 = {.sid = '9', .body = b17};
    uint8_t data17[17] = {0};
    check_int("plot 17th field rejected",
              monitor_plot(&d17, 0, data17, sizeof data17), MONITOR_ERR_BADARG);
}

// --- overflow state reset: a valid command right after an over-length line ------------

static void test_overflow_then_valid(void) {
    char line[400];
    int n = snprintf(line, sizeof line, ">1 ");
    memset(line + n, 'A', 300);
    n += 300;
    n += snprintf(line + n, sizeof line - (size_t)n, "\n>2 ping\n");
    reset_all();
    fake_feed(line);
    run();
    check("overflow then valid", fake_tx(),
          "<1 ERR 8 overflow\n<2 OK monitor 1 testmon\n");
}

// --- emit_hex_resp clamp: a tiny resp buffer never gets a dangling nibble -------------

static void test_hex_resp_clamp(void) {
    // Unreachable over the wire (the command line itself would overflow first), but
    // monitor_dispatch is callable directly with an arbitrarily small resp buffer.
    reset_all();
    char t0[] = "i2c", t1[] = "rd", t2[] = "48", t3[] = "4";
    char *argv[] = {t0, t1, t2, t3};
    char resp[5];   // room for 2 bytes of hex + NUL; the 4 read bytes must clamp to 2
    int rc = monitor_dispatch(4, argv, resp, sizeof resp);
    check_int("hex clamp rc", rc, 0);
    check("hex clamp whole bytes", resp, "0642");
}

// --- fake CAN queue bounds: pushing past capacity must not overflow the array ---------

static void test_can_queue_bounds(void) {
    reset_all();
    fake_feed(">1 can filter all\n");
    run();
    fake_tx_reset();
    for (int i = 0; i < 40; i++) {
        push_frame(0x123, 0, NULL, false, false, (uint32_t)i);
    }
    monitor_poll();
    int lines = 0;
    for (const char *p = fake_tx(); (p = strchr(p, '\n')) != NULL; p++) {
        lines++;
    }
    check_int("can queue capped at 32", lines, 32);
}

int main(void) {
    monitor_init(&g_port);
    monitor_register("sensor", h_sensor);
    monitor_register("longresp", h_longresp);

    test_basic();
    test_i2c();
    test_gpio_adc();
    test_can_cmds();
    test_registered();
    test_tokenizer();
    test_overflow();
    test_can_events();
    test_can_filter();
    test_plot();
    test_plot_rebroadcast();
    test_parser_overflow();
    test_line_length_boundary();
    test_eventf();
    test_mark();
    test_token_clamp();
    test_bad_bytes();
    test_can_dlc_clamp();
    test_can_tx_id_range();
    test_tx_drop_counter();
    test_i2c_scan_bus_shorted();
    test_ok_overflow();
    test_plot_registration_guards();
    test_plot_sid_conflict();
    test_plot_registry_full();
    test_plot_field_limit();
    test_overflow_then_valid();
    test_hex_resp_clamp();
    test_can_queue_bounds();
    test_registry_limits();   // last: permanently fills the registry table

    printf("\n%d/%d checks passed\n", g_total - g_fail, g_total);
    return g_fail == 0 ? 0 : 1;
}
