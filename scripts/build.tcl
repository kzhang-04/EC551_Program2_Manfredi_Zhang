# Non-project Vivado flow: synthesize, place, route and (optionally) program
# the Nexys A7 with top_nexys plus one generated hls_core.
#
#   vivado -mode batch -nojournal -source scripts/build.tcl \
#          -tclargs <design_dir> [out_dir] [part] [program]
#
#   design_dir  folder written by `python -m hls` (holds hls_core.v etc.)
#   out_dir     where the bitstream and reports go (default build/<design name>)
#   part        FPGA part (default xc7a100tcsg324-1; use xc7a50ticsg324-1L for the A7-50T)
#   program     pass the word "program" to flash the board over JTAG afterwards
#
# scripts/build.ps1 wraps this so the usual call is a single command.

set root [file normalize [file join [file dirname [info script]] ..]]

if {[llength $argv] < 1} {
    puts "usage: vivado -mode batch -source scripts/build.tcl -tclargs <design_dir> \[out_dir\] \[part\] \[program\]"
    exit 1
}
set design_dir [file normalize [lindex $argv 0]]
set out_dir    [lindex $argv 1]
set part       [lindex $argv 2]
set program    [expr {[lindex $argv 3] eq "program"}]
if {$out_dir eq ""} { set out_dir [file join $root build [file tail $design_dir]] }
if {$part eq ""}    { set part xc7a100tcsg324-1 }
set out_dir [file normalize $out_dir]
file mkdir $out_dir

foreach f {hls_core.v hls_datapath.v hls_controller.v} {
    if {![file exists [file join $design_dir $f]]} {
        puts "ERROR: $design_dir has no $f; run `python -m hls <equations>` first"
        exit 1
    }
}

set_part $part

# Hand-written board RTL, the primitive functional units, then the generated core.
foreach f {adder.v subtractor.v multiplier.v debouncer.v seven_seg.v top_nexys.v} {
    read_verilog [file join $root rtl $f]
}
foreach f {hls_datapath.v hls_controller.v hls_core.v} {
    read_verilog [file join $design_dir $f]
}
read_xdc [file join $root constraints nexys_a7.xdc]

synth_design -top top_nexys -part $part
report_utilization -file [file join $out_dir utilization_synth.rpt]

opt_design
place_design
route_design

report_utilization    -file [file join $out_dir utilization.rpt]
report_timing_summary -file [file join $out_dir timing.rpt]
report_drc            -file [file join $out_dir drc.rpt]

# Stop if timing failed: a negative worst slack means the design may misbehave.
set wns [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
puts "INFO: worst setup slack = $wns ns"
if {$wns ne "" && $wns < 0} {
    puts "ERROR: timing not met (WNS $wns ns), see [file join $out_dir timing.rpt]"
    exit 1
}

set bit [file join $out_dir top_nexys.bit]
write_bitstream -force $bit
puts "INFO: bitstream written to $bit"

if {$program} {
    open_hw_manager
    connect_hw_server -allow_non_jtag
    open_hw_target
    set dev [lindex [get_hw_devices xc7a*] 0]
    if {$dev eq ""} {
        puts "ERROR: no Artix-7 found on the JTAG chain; is the board plugged in and on?"
        exit 1
    }
    current_hw_device $dev
    set_property PROGRAM.FILE $bit $dev
    program_hw_devices $dev
    close_hw_target
    puts "INFO: programmed $dev"
}
