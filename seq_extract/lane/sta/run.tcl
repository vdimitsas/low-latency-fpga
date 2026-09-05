# -----------------------------------------------------------------------------
# seq_extract_lane STA
#
# Synthesises seq_extract_lane_sta_wrap, which flops every port around the
# lane, and reports timing at 325 MHz.
#
# Run from this folder:
#   vivado -mode batch -source run.tcl
# -----------------------------------------------------------------------------

set part       xc7k160tffg676-3
set period_ns  3.077
set top        seq_extract_lane_sta_wrap

# The offset decides which version of the block is elaborated. 30 puts the
# sequence number deep in the packet and makes the field span two beats, which
# is the expensive case.
set seq_offset 30

file mkdir reports

read_verilog -sv ../rtl/seq_extract_lane.sv
read_verilog -sv ./seq_extract_lane_sta_wrap.sv

synth_design -top $top -part $part \
    -generic DATA_W=64 \
    -generic SEQ_W=32 \
    -generic SEQ_OFFSET=$seq_offset \
    -generic BYTE_CNT_W=3

create_clock -name clk -period $period_ns [get_ports clk]

report_timing_summary -file reports/timing_summary.rpt
report_utilization    -file reports/utilization.rpt
report_timing -delay_type max -max_paths 10 -nworst 10 -sort_by group \
    -input_pins -file reports/timing_worst.rpt

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
puts "=========================================="
puts "WNS = $wns ns   (target [expr {1000.0/$period_ns}] MHz)"
puts "SEQ_OFFSET = $seq_offset"
puts "=========================================="
