// test_families.c - the opt-in MON_NO_<FAMILY> build flags (monitor.h).
//
// Built once per flag and once with all five (Makefile `families`). A dropped family
// answers ERR 7 nosup to every spelling of its commands; every other family must still
// answer as the default build does, so a flag cannot silently take a neighbour with it.

#include "../monitor/monitor.h"

#include <stdio.h>
#include <string.h>

size_t   fake_uart_read(uint8_t *buf, size_t max);
bool     fake_uart_write(const uint8_t *buf, size_t len);
uint32_t fake_tick_ms(void);
void     fake_reset(void);
void     fake_feed(const char *s);
void     fake_spi_set_mode(int mode);
const char *fake_tx(void);
void     fake_can_reset(void);
void     fake_can_push(const mon_can_frame_t *f);

static const monitor_port_t g_port = {
	.uart_read  = fake_uart_read,
	.uart_write = fake_uart_write,
	.tick_ms    = fake_tick_ms,
	.name       = "fam",
};

static int g_total;
static int g_fail;

static void expect(const char *line, const char *want) {
	fake_reset();
	fake_can_reset();
	fake_spi_set_mode(1);
	monitor_init(&g_port);
	fake_feed(line);
	for (int i = 0; i < 50; i++) {
		monitor_poll();
	}
	g_total++;
	if (strcmp(fake_tx(), want) != 0) {
		g_fail++;
		printf("FAIL %s  want: [%s]  got: [%s]\n", line, want, fake_tx());
	} else {
		printf("ok   %s", line);
	}
}

#define NOSUP "<1 ERR 7 nosup\n"

#ifdef MON_NO_CAN
#define CAN_OFF 1
#else
#define CAN_OFF 0
#endif
#ifdef MON_NO_I2C
#define I2C_OFF 1
#else
#define I2C_OFF 0
#endif
#ifdef MON_NO_SPI
#define SPI_OFF 1
#else
#define SPI_OFF 0
#endif
#ifdef MON_NO_GPIO
#define GPIO_OFF 1
#else
#define GPIO_OFF 0
#endif
#ifdef MON_NO_ADC
#define ADC_OFF 1
#else
#define ADC_OFF 0
#endif

int main(void) {
	expect(">1 ping\n", "<1 OK monitor 1 fam\n");

	// One working command per family: the default answer, or nosup where dropped.
	expect(">1 can stat\n", CAN_OFF ? NOSUP : "<1 OK rx=10 tx=3 err=0 state=active\n");
	expect(">1 i2c scan\n", I2C_OFF ? NOSUP : "<1 OK 48 50\n");
	expect(">1 spi xfer imu A5\n", SPI_OFF ? NOSUP : "<1 OK 5A\n");
	expect(">1 gpio get led\n", GPIO_OFF ? NOSUP : "<1 OK 0\n");
	expect(">1 adc read vref\n", ADC_OFF ? NOSUP : "<1 OK raw=2048 mv=3300\n");

	// Every other spelling of a dropped family is nosup too, never badcmd or badarg.
	if (CAN_OFF) {
		expect(">1 can2 tx 100 -\n", NOSUP);
		expect(">1 can0 stat\n", NOSUP);
		expect(">1 can wobble\n", NOSUP);
		expect(">1 can\n", NOSUP);
		// The RX drain is compiled out: a frame the shim hands over is never emitted.
		fake_reset();
		fake_can_reset();
		monitor_init(&g_port);
		mon_can_frame_t f;
		memset(&f, 0, sizeof f);
		f.id = 0x123;
		fake_can_push(&f);
		monitor_poll();
		g_total++;
		if (fake_tx()[0] != '\0') {
			g_fail++;
			printf("FAIL no CAN drain  got: [%s]\n", fake_tx());
		} else {
			printf("ok   no CAN drain\n");
		}
	} else {
		expect(">1 can wobble\n", "<1 ERR 1 badcmd\n");
	}
	if (I2C_OFF) {
		expect(">1 i2c rd 48 2\n", NOSUP);
		expect(">1 i2c\n", NOSUP);
	}
	if (SPI_OFF) {
		expect(">1 spi\n", NOSUP);
		expect(">1 spi foo\n", NOSUP);
	}
	if (GPIO_OFF) {
		expect(">1 gpio set led 1\n", NOSUP);
	}
	if (ADC_OFF) {
		expect(">1 adc\n", NOSUP);
	}
	// Not a family at all is still badcmd.
	expect(">1 can22 tx 100 -\n", "<1 ERR 1 badcmd\n");

	printf("\n%d/%d checks passed\n", g_total - g_fail, g_total);
	return g_fail == 0 ? 0 : 1;
}
