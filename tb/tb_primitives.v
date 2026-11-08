// Self-checking testbench for the adder, subtractor and multiplier.
//
//   iverilog -o build/tb_primitives.vvp tb/tb_primitives.v rtl/adder.v rtl/subtractor.v rtl/multiplier.v
//   vvp build/tb_primitives.vvp
//
// Prints one FAIL line per mismatch, then a final PASS or FAIL summary.
`timescale 1ns/1ps

module tb_primitives;
    localparam W = 16;

    // 16-bit units, the width the datapath uses.
    reg  [W-1:0] a, b;
    wire [W-1:0] sum, diff, prod;

    adder      #(.WIDTH(W)) u_add (.a(a), .b(b), .y(sum));
    subtractor #(.WIDTH(W)) u_sub (.a(a), .b(b), .y(diff));
    multiplier #(.WIDTH(W)) u_mul (.a(a), .b(b), .y(prod));

    // 8-bit units, to check that WIDTH really changes the wraparound point.
    reg  [7:0] a8, b8;
    wire [7:0] sum8, diff8, prod8;

    adder      #(.WIDTH(8)) u_add8 (.a(a8), .b(b8), .y(sum8));
    subtractor #(.WIDTH(8)) u_sub8 (.a(a8), .b(b8), .y(diff8));
    multiplier #(.WIDTH(8)) u_mul8 (.a(a8), .b(b8), .y(prod8));

    integer errors = 0;
    integer checks = 0;
    integer i, j;
    reg [31:0] ref_sum, ref_diff, ref_prod;

    task check(input [8*3-1:0] op, input [31:0] x, input [31:0] y,
               input [31:0] got, input [31:0] want);
        begin
            checks = checks + 1;
            if (got !== want) begin
                errors = errors + 1;
                $display("FAIL %0s: a=%0d b=%0d got %0d, want %0d", op, x, y, got, want);
            end
        end
    endtask

    // Hand-picked cases with the expected answers written out.
    task directed(input [W-1:0] x, input [W-1:0] y,
                  input [W-1:0] want_sum, input [W-1:0] want_diff, input [W-1:0] want_prod);
        begin
            a = x; b = y; #1;
            check("add", x, y, sum,  want_sum);
            check("sub", x, y, diff, want_diff);
            check("mul", x, y, prod, want_prod);
        end
    endtask

    initial begin
        //        a        b        a+b      a-b      a*b
        directed(16'd0,    16'd0,   16'd0,   16'd0,   16'd0);
        directed(16'd3,    16'd2,   16'd5,   16'd1,   16'd6);
        directed(16'd255,  16'd255, 16'd510, 16'd0,   16'd65025);  // largest switch inputs
        directed(16'd1234, 16'd4321, 16'd5555, 16'd62449, 16'd23698);
        directed(16'hFFFF, 16'd1,   16'd0,   16'hFFFE, 16'hFFFF);  // add wraps to 0
        directed(16'd0,    16'd1,   16'd1,   16'hFFFF, 16'd0);     // sub wraps below 0
        directed(16'd300,  16'd300, 16'd600, 16'd0,   16'd24464);  // 90000 mod 65536
        directed(16'hFFFF, 16'hFFFF, 16'hFFFE, 16'd0, 16'd1);      // low bits of 0xFFFE0001

        // Random 16-bit operands, checked against 32-bit arithmetic.
        for (i = 0; i < 2000; i = i + 1) begin
            a = $random; b = $random; #1;
            ref_sum  = {16'd0, a} + {16'd0, b};
            ref_diff = {16'd0, a} - {16'd0, b};
            ref_prod = {16'd0, a} * {16'd0, b};
            check("add", a, b, sum,  ref_sum[W-1:0]);
            check("sub", a, b, diff, ref_diff[W-1:0]);
            check("mul", a, b, prod, ref_prod[W-1:0]);
        end

        // Every pair of 8-bit operands.
        for (i = 0; i < 256; i = i + 1) begin
            for (j = 0; j < 256; j = j + 1) begin
                a8 = i; b8 = j; #1;
                check("add", i, j, sum8,  (i + j) % 256);
                check("sub", i, j, diff8, (i - j + 256) % 256);
                check("mul", i, j, prod8, (i * j) % 256);
            end
        end

        if (errors == 0)
            $display("PASS: tb_primitives, %0d checks", checks);
        else
            $display("FAIL: tb_primitives, %0d of %0d checks failed", errors, checks);
        $finish;
    end
endmodule
