# =============================================================================
# feed_buffer STA
#
#   vivado -mode batch -source run.tcl
#
# Synthesises feed_buffer directly. Its outputs are registered, so it contains
# real register to register paths on its own and needs no harness.

# =============================================================================

set part      xc7k160tffg676-3
set period_ns 3.077
set top       feed_buffer

file mkdir reports

read_verilog -sv ../rtl/sync_fifo.sv
read_verilog -sv ../rtl/feed_buffer.sv


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
