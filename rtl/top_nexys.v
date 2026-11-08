// Nexys A7 top level. Hand-written and the same for every design: only the
// generated hls_core underneath changes. Port names match Digilent's master
// XDC file.
//
//   SW[15:14]  view: 00 FU outputs, 01 outputs, 10 registers, 11 inputs
//   SW[13:8]   index within the view
//   SW[7:0]    value to load into an input
//   BTNC       step: one press = one clock enable = one FSM state
//   BTNU       load SW[7:0] into the selected input (input view only)
//   BTNL       restart: back to IDLE, inputs kept
//   CPU_RESETN clear everything
//
//   Digits 7-6 FSM state, 5-4 index, 3-0 selected value (hex)
//   LED15 done, LED13-12 view, LED11-0 which functional units are busy
//
// The core always runs on the 100 MHz clock. A button press doesn't drive
// a clock net (that would bounce and break Vivado's clock routing); it
// becomes a one-cycle enable, which steps the design exactly once.
module top_nexys #(
    parameter DEBOUNCE_CYCLES = 1000000,   // 10 ms at 100 MHz
    parameter REFRESH_BITS = 17
) (
    input  wire        CLK100MHZ,
    input  wire        CPU_RESETN,
    input  wire        BTNC,
    input  wire        BTNU,
    input  wire        BTNL,
    input  wire [15:0] SW,
    output wire [15:0] LED,
    output wire        CA, CB, CC, CD, CE, CF, CG, DP,
    output wire [7:0]  AN
);
    wire clk = CLK100MHZ;

    // Reset is held at power-up and while CPU_RESETN is pressed.
    reg rst_meta = 1'b1, rst = 1'b1;
    always @(posedge clk) begin
        rst_meta <= ~CPU_RESETN;
        rst      <= rst_meta;
    end

    reg [15:0] sw_meta, sw;
    always @(posedge clk) begin
        sw_meta <= SW;
        sw      <= sw_meta;
    end

    wire step, load, restart;
    debouncer #(.CYCLES(DEBOUNCE_CYCLES)) db_step    (.clk(clk), .rst(rst), .btn(BTNC), .pulse(step));
    debouncer #(.CYCLES(DEBOUNCE_CYCLES)) db_load    (.clk(clk), .rst(rst), .btn(BTNU), .pulse(load));
    debouncer #(.CYCLES(DEBOUNCE_CYCLES)) db_restart (.clk(clk), .rst(rst), .btn(BTNL), .pulse(restart));

    wire [1:0]  view  = sw[15:14];
    wire [5:0]  index = sw[13:8];
    wire [15:0] dbg_value;
    wire [11:0] fu_active;
    wire [7:0]  state;
    wire        done;

    hls_core core (
        .clk(clk), .rst(rst), .en(step), .start(1'b1), .restart(restart),
        .in_we(load && view == 2'b11), .in_sel(index), .in_data({8'd0, sw[7:0]}),
        .view(view), .dbg_sel(index), .dbg_value(dbg_value),
        .fu_active(fu_active), .state(state), .done(done)
    );

    assign LED = {done, 1'b0, view, fu_active};

    wire [6:0] seg;
    seven_seg #(.REFRESH_BITS(REFRESH_BITS)) display (
        .clk(clk), .rst(rst),
        .value({state, 2'b00, index, dbg_value}),
        .dots(8'b0101_0000),   // dots after the state and index digits
        .seg(seg), .dp(DP), .an(AN)
    );
    assign {CG, CF, CE, CD, CC, CB, CA} = seg;
endmodule
