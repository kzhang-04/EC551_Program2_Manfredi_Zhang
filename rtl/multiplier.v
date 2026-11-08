// Combinational WIDTH-bit multiplier.
// Only the low WIDTH bits of the product are kept, so results wrap modulo
// 2^WIDTH like the adder and subtractor. Vivado maps a 16x16 multiply onto
// one DSP48 slice.
module multiplier #(
    parameter WIDTH = 16
) (
    input  wire [WIDTH-1:0] a,
    input  wire [WIDTH-1:0] b,
    output wire [WIDTH-1:0] y
);
    // The expression takes the width of y, so the upper bits are discarded.
    assign y = a * b;
endmodule
