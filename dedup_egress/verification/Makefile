# =============================================================================
# dedup_egress verification
#
#   make                        run every test
#   make MODULE=test_cpt        run one file
#   make TRACE=1 MODULE=test_cpt   run one file with a waveform dump
#
# Tracing is off by default. Verilator 5.036 segfaults when tracing the long
# constrained random tests, so the dump is opt-in and meant for short runs.
#
# There is no wrapper file. dedup_egress has no packed two dimensional ports,
# so cocotb drives it directly and it is the toplevel.
# =============================================================================

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog

PWD := $(shell pwd)

VERILOG_SOURCES += $(PWD)/../rtl/dedup_egress.sv

TOPLEVEL = dedup_egress

MODULE ?= test_passthrough,test_dedup_egress_core,test_cpt,test_midpacket,test_backpressure,test_random

EXTRA_ARGS += -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

ifeq ($(TRACE),1)
EXTRA_ARGS += --trace --trace-structs
endif

include $(shell cocotb-config --makefiles)/Makefile.sim

.PHONY: clean_all
clean_all: clean
	rm -rf sim_build results.xml dump.vcd __pycache__
