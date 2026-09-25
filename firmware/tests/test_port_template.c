// test_port_template.c - the shipped port template, built with every shim block enabled
// (MON_TEMPLATE_ALL) and linked against the monitor, and the same checks over the weak
// defaults alone (Makefile `port-template`). A template signature that drifts from
// monitor.h fails to compile, and a shim the header no longer declares fails
// -Wmissing-prototypes. Every bus command must answer ERR 7 nosup either way.

#include "../monitor/monitor.h"

#include <stdio.h>
#include <string.h>

#ifdef MON_TEMPLATE_ALL
void monitor_port_init(void);
#endif

static int g_total;
static int g_fail;

static void expect_rc(const char *line, int want) {
	char buf[64], resp[MONITOR_LINE_MAX + 1];
	char *argv[8];
	int argc = 0;
	strncpy(buf, line, sizeof buf - 1);
	buf[sizeof buf - 1] = '\0';
	for (char *t = strtok(buf, " "); t && argc < 8; t = strtok(NULL, " ")) {
		argv[argc++] = t;
	}
	int rc = monitor_dispatch(argc, argv, resp, sizeof resp);
	g_total++;
	if (rc != want) {
		g_fail++;
		printf("FAIL %s  want: %d  got: %d\n", line, want, rc);
	} else {
		printf("ok   %s\n", line);
	}
}

int main(void) {
#ifdef MON_TEMPLATE_ALL
	monitor_port_init();
#endif
	expect_rc("ping", 0);
	expect_rc("info", 0);
	expect_rc("can tx 100 -", MONITOR_ERR_NOSUP);
	expect_rc("can stat", MONITOR_ERR_NOSUP);
	expect_rc("i2c scan", MONITOR_ERR_NOSUP);
	expect_rc("i2c rd 48 1", MONITOR_ERR_NOSUP);
	expect_rc("spi xfer imu 00", MONITOR_ERR_NOSUP);
	expect_rc("gpio set led 1", MONITOR_ERR_NOSUP);
	expect_rc("gpio get led", MONITOR_ERR_NOSUP);
	expect_rc("adc read vref", MONITOR_ERR_NOSUP);
	printf("\n%d/%d checks passed\n", g_total - g_fail, g_total);
	return g_fail == 0 ? 0 : 1;
}
