# ===============================================================
# cocotb + Verilator Makefile — feed_arbiter
# Run from feed_arbiter/verification/:
#     make clean && make
# Sweep the hiccup threshold:
#     make clean && make HICCUP_CYCLES=8
# Supported values: HICCUP_CYCLES >= 3 (the RTL's own compile-time
# guard will $error out below that).
# ===============================================================
SIM ?= verilator
TOPLEVEL_LANG ?= verilog

# threshold under test — passed to both the DUT (as a real Verilog
# parameter override) and to the Python side (via env var), so the
# two can never drift apart
HICCUP_CYCLES ?= 4
export HICCUP_CYCLES

# RTL lives one level up in rtl/; the testbench wrapper and the
# cocotb Python tests live here in verification/.
VERILOG_SOURCES += $(PWD)/../rtl/feed_arbiter.sv
VERILOG_SOURCES += $(PWD)/feed_arbiter_tb_wrap.sv

TOPLEVEL = feed_arbiter_tb_wrap

# override the wrapper's HICCUP_CYCLES parameter at elaboration time
EXTRA_ARGS += -GHICCUP_CYCLES=$(HICCUP_CYCLES)

export PYTHONPATH := $(PWD):$(PYTHONPATH)

# full suite — directed + random golden model
MODULE = test_mainstream,test_backpressure,test_fix,test_hiccup,test_fix_hiccup,test_hiccup_threshold,test_random

# EXTRA_ARGS += --trace --trace-structs   # disabled: caused a segfault
                                          # on the long random test (3000
                                          # cycles of dense trace data on
                                          # top of 30 prior tests in one
                                          # process). Re-enable only when
                                          # running a single test in
                                          # isolation for waveform debug.
COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

include $(shell cocotb-config --makefiles)/Makefile.sim
