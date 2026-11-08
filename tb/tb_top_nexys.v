// Board-level testbench: drives top_nexys through its pins the way a person
// would, with bouncing buttons, to check the debouncers, input loading,
// stepping, view selection, LEDs and display.
//
// It expects the core generated from examples/pdf_example.txt in asap mode
// with every variable as an output (the default):
//
//   python -m hls examples/pdf_example.txt --out build/pdf_asap
//   iverilog -o build/tb_top.vvp tb/tb_top_nexys.v rtl/*.v build/pdf_asap/hls_*.v
//   vvp build/tb_top.vvp
//
// The debounce time and display refresh are shortened so it runs quickly.
`timescale 1ns/1ps

module tb_top_nexys;
    localparam DEBOUNCE = 4;

    reg clk = 1'b0;
    reg resetn = 1'b0;
    reg btnc = 1'b0, btnu = 1'b0, btnl = 1'b0;
    reg [15:0] sw = 16'd0;
    wire [15:0] led;
    wire ca, cb, cc, cd, ce, cf, cg, dp;
    wire [7:0] an;

    top_nexys #(.DEBOUNCE_CYCLES(DEBOUNCE), .REFRESH_BITS(2)) dut (
        .CLK100MHZ(clk), .CPU_RESETN(resetn),
        .BTNC(btnc), .BTNU(btnu), .BTNL(btnl), .SW(sw), .LED(led),
        .CA(ca), .CB(cb), .CC(cc), .CD(cd), .CE(ce), .CF(cf), .CG(cg), .DP(dp), .AN(an)
    );

    always #5 clk = ~clk;

    integer errors = 0, checks = 0;

    localparam BTN_STEP = 0, BTN_LOAD = 1, BTN_RESTART = 2;

    task set_button(input integer which, input level);
        case (which)
            BTN_STEP:    btnc = level;
            BTN_LOAD:    btnu = level;
            BTN_RESTART: btnl = level;
        endcase
    endtask

    // A press that bounces a few times on the way down and up. Each bounce
    // is far shorter than the debounce time, so it must count once.
    task press(input integer which);
        integer k;
        begin
            for (k = 0; k < 3; k = k + 1) begin
                set_button(which, 1'b1); #7;
                set_button(which, 1'b0); #7;
            end
            set_button(which, 1'b1);
            repeat (DEBOUNCE + 10) @(posedge clk);
            for (k = 0; k < 3; k = k + 1) begin
                set_button(which, 1'b0); #7;
                set_button(which, 1'b1); #7;
            end
            set_button(which, 1'b0);
            repeat (DEBOUNCE + 10) @(posedge clk);
        end
    endtask

    // Switches go through two synchronizer flip-flops before the core sees them.
    task set_switches(input [1:0] view, input [5:0] index, input [7:0] value);
        begin
            sw = {view, index, value};
            repeat (3) @(posedge clk);
            #1;
        end
    endtask

    task expect_value(input [15:0] want, input [8*32-1:0] what);
        begin
            checks = checks + 1;
            if (dut.dbg_value !== want) begin
                errors = errors + 1;
                $display("FAIL %0s: got %0d, want %0d", what, dut.dbg_value, want);
            end
        end
    endtask

    task expect_state(input [7:0] want);
        begin
            checks = checks + 1;
            if (dut.state !== want) begin
                errors = errors + 1;
                $display("FAIL state: got %0d, want %0d", dut.state, want);
            end
        end
    endtask

    // Wait until the scan reaches a digit, then check its segments.
    task expect_digit(input [2:0] digit, input [6:0] want, input [8*32-1:0] what);
        begin
            wait (an == ~(8'b1 << digit));
            #1;
            checks = checks + 1;
            if ({cg, cf, ce, cd, cc, cb, ca} !== want) begin
                errors = errors + 1;
                $display("FAIL display %0s: segments %b, want %b", what,
                         {cg, cf, ce, cd, cc, cb, ca}, want);
            end
            @(posedge clk);
        end
    endtask

    initial begin
        repeat (10) @(posedge clk);
        resetn = 1'b1;
        repeat (5) @(posedge clk);
        expect_state(8'd0);

        // Load A=3 and B=2 in the input view.
        set_switches(2'b11, 6'd0, 8'd3);
        press(BTN_LOAD);
        expect_value(16'd3, "input A after load");
        set_switches(2'b11, 6'd1, 8'd2);
        press(BTN_LOAD);
        expect_value(16'd2, "input B after load");

        // BTNU outside the input view must not change anything.
        set_switches(2'b00, 6'd0, 8'd99);
        press(BTN_LOAD);
        set_switches(2'b11, 6'd0, 8'd99);
        expect_value(16'd3, "input A after load in FU view");

        // Each bouncing press is exactly one step: IDLE -> 1 -> 2 -> 3 -> DONE.
        set_switches(2'b00, 6'd0, 8'd0);
        press(BTN_STEP);
        expect_state(8'd1);
        expect_value(16'd5, "add0 in step 1 (X = 3 + 2)");
        press(BTN_STEP);
        expect_state(8'd2);
        press(BTN_STEP);
        expect_state(8'd3);
        press(BTN_STEP);
        expect_state(8'd4);
        checks = checks + 1;
        if (led[15] !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL LED15 should be on at DONE");
        end

        // Outputs X, Y, Z, Q are view 01, indexes 0-3.
        set_switches(2'b01, 6'd0, 8'd0);  expect_value(16'd5, "output X");
        set_switches(2'b01, 6'd1, 8'd0);  expect_value(16'd8, "output Y");
        set_switches(2'b01, 6'd2, 8'd0);  expect_value(16'd3, "output Z");
        set_switches(2'b01, 6'd3, 8'd0);  expect_value(16'd6, "output Q");

        // Display shows 04.03.0006 for output Q at DONE.
        expect_digit(3'd0, 7'b0000010, "digit 0 = 6");
        expect_digit(3'd1, 7'b1000000, "digit 1 = 0");
        expect_digit(3'd4, 7'b0110000, "digit 4 = index 3");
        expect_digit(3'd6, 7'b0011001, "digit 6 = state 4");
        expect_digit(3'd7, 7'b1000000, "digit 7 = 0");

        // Restart goes back to IDLE but keeps the inputs.
        press(BTN_RESTART);
        expect_state(8'd0);
        set_switches(2'b11, 6'd1, 8'd0);
        expect_value(16'd2, "input B after restart");

        if (errors == 0)
            $display("PASS: tb_top_nexys, %0d checks", checks);
        else
            $display("FAIL: tb_top_nexys, %0d of %0d checks failed", errors, checks);
        $finish;
    end
endmodule
