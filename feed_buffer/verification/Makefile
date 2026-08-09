# =============================================================================
# feed_buffer verification
#
#   make                          run every test
#   make MODULE=test_bypass       run one file
#   make TRACE=1 MODULE=test_bypass   run one file with a waveform dump
# =============================================================================

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog

PWD := $(shell pwd)

VERILOG_SOURCES += $(PWD)/../rtl/sync_fifo.sv
VERILOG_SOURCES += $(PWD)/../rtl/feed_buffer.sv
VERILOG_SOURCES += $(PWD)/feed_buffer_tb_wrap.sv

TOPLEVEL = feed_buffer_tb_wrap

# Own build dir so the two suites do not collide.
SIM_BUILD = sim_build

MODULE ?= test_passthrough,test_bypass,test_backpressure,test_invalidate,test_random

EXTRA_ARGS += -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

ifeq ($(TRACE),1)
EXTRA_ARGS += --trace --trace-structs
endif

include $(shell cocotb-config --makefiles)/Makefile.sim

.PHONY: clean_all
clean_all: clean
	rm -rf sim_build sim_build_fifo results.xml dump.vcd __pycache__
