# Host-compiled unit tests for the portable monitor module (SPEC 5 acceptance).
#
# `make` builds and is ready to run; `make run` (or the pytest wrapper) executes the
# binary. The monitor core must compile clean under -Wall -Wextra -Werror.
#
# Cross-compile sanity: the same sources must also compile for the target. We do not
# require an ARM toolchain to be installed, but if it is you can syntax-check with:
#
#   make arm-check    # needs arm-none-eabi-gcc on PATH; compile-only, no link
#
# The ARM flags below mirror a Cortex-M4F build (adjust -mcpu/-mfpu for your part).

CC      ?= gcc
CSTD    := -std=c99
WARN    := -Wall -Wextra -Werror
CFLAGS  := $(CSTD) $(WARN) -O2 -g -I..

MONITOR_SRC := ../monitor/monitor.c ../monitor/monitor_cmds.c
TEST_SRC    := fake_shims.c test_monitor.c
SRC         := $(MONITOR_SRC) $(TEST_SRC)

BIN := test_monitor

ARM_CC    := arm-none-eabi-gcc
ARM_FLAGS := $(CSTD) $(WARN) -O2 -mcpu=cortex-m4 -mthumb \
             -mfloat-abi=hard -mfpu=fpv4-sp-d16 -ffreestanding -I..

.PHONY: all run arm-check clean

all: $(BIN)

$(BIN): $(SRC) ../monitor/monitor.h
	$(CC) $(CFLAGS) -o $@ $(SRC)

run: $(BIN)
	./$(BIN)

# Compile-only check that the portable core builds for the target (no linking, no libc).
arm-check:
	$(ARM_CC) $(ARM_FLAGS) -c $(MONITOR_SRC)
	@rm -f monitor.o monitor_cmds.o
	@echo "arm-none-eabi compile check passed"

clean:
	rm -f $(BIN) *.o
