# =============================================================================
# dedup_egress STA
#
#   vivado -mode batch -source run.tcl
#
# Synthesises dedup_egress_sta_harness, which flops every port of dedup_egress
# so both ends of the block become real register to register paths. The flops
# belong to the measurement, not the design.
# =============================================================================

set part      xc7k160tffg676-3
set period_ns 3.077
set top       dedup_egress_sta_harness

file mkdir reports

read_verilog -sv ../rtl/dedup_egress.sv
read_verilog -sv dedup_egress_sta_harness.sv

synth_design -top $top -part $part

create_clock -name clk -period $period_ns [get_ports clk]
set_false_path -from [get_ports rst_n]

report_timing_summary -file reports/timing_summary.rpt
report_timing -delay_type max -max_paths 10 -nworst 10 -sort_by group \
              -input_pins -file reports/timing_worst.rpt
report_utilization -file reports/utilization.rpt

set wns [get_property SLACK [get_timing_paths -delay_type max]]
puts "=========================================="
puts "WNS = $wns ns   (target [expr {1000.0/$period_ns}] MHz)"
puts "=========================================="
