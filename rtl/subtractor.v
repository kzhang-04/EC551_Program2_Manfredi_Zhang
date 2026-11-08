// Combinational WIDTH-bit subtractor, y = a - b.
// The assignment doesn't require negative results, so a result below zero
// simply wraps modulo 2^WIDTH (0 - 1 gives all ones).
module subtractor #(
    parameter WIDTH = 16
) (
    input  wire [WIDTH-1:0] a,
    input  wire [WIDTH-1:0] b,
    output wire [WIDTH-1:0] y
);
    assign y = a - b;
endmodule
