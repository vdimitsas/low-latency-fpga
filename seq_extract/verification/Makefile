# -----------------------------------------------------------------------------
# seq_extract verification
#
#   make fit        SEQ_OFFSET = 28, field inside one beat
#   make span       SEQ_OFFSET = 30, field across two beats
#   make all_suites both
#
#   make MODULE=test_wiring SEQ_OFFSET=28        one run
#   make TRACE=1 MODULE=test_wiring SEQ_OFFSET=30
#
# This suite only checks the wiring: that feed f's beat, sequence number and
# ready all belong to feed f. The lane's own behaviour is verified against a
# cycle accurate model in seq_extract/lane/verification, so it is not repeated
# here. The two offsets are run because the lane elaborates differently for
# each, and a wiring fault could hide in one of them.
#
# Tracing is off by default. --trace-structs segfaults Verilator 5.036 on long
# runs, so turn it on only for short targeted runs.
# -----------------------------------------------------------------------------

TOPLEVEL_LANG = verilog
SIM          ?= verilator
TOPLEVEL      = seq_extract_tb_wrap

VERILOG_SOURCES  = $(PWD)/../lane/rtl/seq_extract_lane.sv
VERILOG_SOURCES += $(PWD)/../rtl/seq_extract.sv
VERILOG_SOURCES += $(PWD)/seq_extract_tb_wrap.sv

N_FEEDS       ?= 4
DATA_W        ?= 64
SEQ_W         ?= 32
SEQ_OFFSET    ?= 28
CLK_PERIOD_NS ?= 10

export N_FEEDS DATA_W SEQ_W SEQ_OFFSET CLK_PERIOD_NS

COMPILE_ARGS += -GN_FEEDS=$(N_FEEDS)
COMPILE_ARGS += -GDATA_W=$(DATA_W)
COMPILE_ARGS += -GSEQ_W=$(SEQ_W)
COMPILE_ARGS += -GSEQ_OFFSET=$(SEQ_OFFSET)

TRACE ?= 0
ifeq ($(TRACE),1)
    EXTRA_ARGS += --trace --trace-structs
endif

# One build directory per offset, so elaborations never collide.
SIM_BUILD ?= sim_build_$(SEQ_OFFSET)

MODULE ?= test_wiring

include $(shell cocotb-config --makefiles)/Makefile.sim

# -----------------------------------------------------------------------------
# grouped targets
#
# Every target cleans its own build directory first. Verilator only rebuilds
# when a source file changes, and SEQ_OFFSET is a parameter, not a source, so
# without the clean the second offset would silently run the first one's
# design.
# -----------------------------------------------------------------------------

.PHONY: fit span all_suites

fit:
	$(MAKE) clean SEQ_OFFSET=28
	$(MAKE) sim MODULE=test_wiring SEQ_OFFSET=28

span:
	$(MAKE) clean SEQ_OFFSET=30
	$(MAKE) sim MODULE=test_wiring SEQ_OFFSET=30

all_suites:
	@$(MAKE) fit  || { echo "=== FAILED: fit, SEQ_OFFSET=28 ==="; exit 1; }
	@$(MAKE) span || { echo "=== FAILED: span, SEQ_OFFSET=30 ==="; exit 1; }
	@echo "======================================================"
	@echo " ALL SUITES PASSED"
	@echo " offsets: 28 fit, 30 span"
	@echo "======================================================"
