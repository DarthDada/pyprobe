PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
PYTHON       ?= $(PROJECT_ROOT)/.venv/bin/python3
PYTHON_INCLUDE ?= $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_path('include'))")
CFLAGS  = -O2 -Wall -Wno-unused -I$(PYTHON_INCLUDE) -DPy_BUILD_CORE -DPy_BUILD_CORE_BUILTIN
LDLIBS  = -ldw -lelf -lz
BUILD_DIR = $(PROJECT_ROOT)/build
TARGET  = pyprobe

.PHONY: all clean check-aarch64

all: $(BUILD_DIR)/$(TARGET)

$(BUILD_DIR)/$(TARGET): py_stack_dump.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

# Cross-compile check for aarch64 (requires gcc-aarch64-linux-gnu)
check-aarch64:
	aarch64-linux-gnu-gcc $(CFLAGS) -o /tmp/pyprobe-aarch64-check py_stack_dump.c -lm

$(BUILD_DIR):
	mkdir -p $@

clean:
	rm -rf $(BUILD_DIR)
