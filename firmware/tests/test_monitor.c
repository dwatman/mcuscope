// test_monitor.c - host-compiled unit tests for the monitor core (SPEC 5 acceptance).
//
// Mirrors the host protocol suite: ping/info, every bus command, badcmd/badarg/nosup,
// overflow discard, tokenizer edge cases, a registered custom command, CAN event
// emission with filtering, and monitor_plot typed-stream encoding plus the 5 s !pd
// rebroadcast. Feeds bytes through the fake UART, polls the monitor, and compares the
// captured TX bytes to the exact expected wire output. Exits non-zero on any mismatch.

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

int main(void) {
    monitor_init(&g_port);
    monitor_register("sensor", h_sensor);

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

    printf("\n%d/%d checks passed\n", g_total - g_fail, g_total);
    return g_fail == 0 ? 0 : 1;
}
