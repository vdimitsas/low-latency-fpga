# -----------------------------------------------------------------------------
# seq_extract_lane verification
#
#   make fit        the suites at SEQ_OFFSET = 28, field inside one beat
#   make span       the suites at SEQ_OFFSET = 30, field across two beats
#   make sweep      the random suite over further offsets
#   make all_suites all three, in that order
#
#   make MODULE=test_seq_fit SEQ_OFFSET=28        one suite
#   make TRACE=1 MODULE=test_seq_span SEQ_OFFSET=30
#
# SEQ_OFFSET is fixed at elaboration, so the block has to be built more than
# once. The targets are kept apart on purpose: fit and span are different
# elaborations of the block, so a failure in one and not the other says where
# the fault is.
#
# Tracing is off by default. --trace-structs segfaults Verilator 5.036 on long
# random runs, so turn it on only for short targeted runs.
# -----------------------------------------------------------------------------

TOPLEVEL_LANG = verilog
SIM          ?= verilator
TOPLEVEL      = seq_extract_lane
VERILOG_SOURCES = $(PWD)/../rtl/seq_extract_lane.sv

DATA_W     ?= 64
SEQ_W      ?= 32
SEQ_OFFSET ?= 28
BYTE_CNT_W ?= 3

export DATA_W SEQ_W SEQ_OFFSET BYTE_CNT_W

COMPILE_ARGS += -GDATA_W=$(DATA_W)
COMPILE_ARGS += -GSEQ_W=$(SEQ_W)
COMPILE_ARGS += -GSEQ_OFFSET=$(SEQ_OFFSET)
COMPILE_ARGS += -GBYTE_CNT_W=$(BYTE_CNT_W)

TRACE ?= 0
ifeq ($(TRACE),1)
    EXTRA_ARGS += --trace --trace-structs
endif

# One build directory per offset, so elaborations never collide.
SIM_BUILD ?= sim_build_$(SEQ_OFFSET)

MODULE ?= test_passthrough

include $(shell cocotb-config --makefiles)/Makefile.sim

# -----------------------------------------------------------------------------
# grouped targets
# -----------------------------------------------------------------------------

FIT_MODULES  = test_passthrough,test_seq_fit,test_backpressure,test_counter,test_random
SPAN_MODULES = test_passthrough,test_seq_span,test_backpressure,test_counter,test_random

SWEEP_OFFSETS = 33 38 41

.PHONY: fit span sweep all_suites

# Every target cleans its own build directory first. Verilator only rebuilds
# when a source file changes, and SEQ_OFFSET is a parameter, not a source, so
# without the clean a second offset would silently run the first one's design.

fit:
	$(MAKE) clean SEQ_OFFSET=28
	$(MAKE) sim MODULE=$(FIT_MODULES) SEQ_OFFSET=28

span:
	$(MAKE) clean SEQ_OFFSET=30
	$(MAKE) sim MODULE=$(SPAN_MODULES) SEQ_OFFSET=30

sweep:
	@for off in $(SWEEP_OFFSETS); do \
	    echo "=== SEQ_OFFSET=$$off ==="; \
	    $(MAKE) clean SEQ_OFFSET=$$off; \
	    $(MAKE) sim MODULE=test_random SEQ_OFFSET=$$off || exit 1; \
	done

# cocotb exits non zero when a test fails, so a failing suite stops the run
# here and names itself. The banner at the end only prints if all three got
# through, so there is no need to scroll back through the logs.
all_suites:
	@$(MAKE) fit   || { echo "=== FAILED: fit, SEQ_OFFSET=28 ==="; exit 1; }
	@$(MAKE) span  || { echo "=== FAILED: span, SEQ_OFFSET=30 ==="; exit 1; }
	@$(MAKE) sweep || { echo "=== FAILED: sweep ==="; exit 1; }
	@echo "======================================================"
	@echo " ALL SUITES PASSED"
	@echo " offsets: 28 fit, 30 span, $(SWEEP_OFFSETS) random"
	@echo "======================================================"
