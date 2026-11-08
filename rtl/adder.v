// Combinational WIDTH-bit adder.
// The carry out is dropped, so results wrap modulo 2^WIDTH, matching the
// tool's Python model.
module adder #(
    parameter WIDTH = 16
) (
    input  wire [WIDTH-1:0] a,
    input  wire [WIDTH-1:0] b,
    output wire [WIDTH-1:0] y
);
    assign y = a + b;
endmodule
