// Drives the Nexys A7's eight 7-segment digits as hex, one digit at a time.
// Each digit is lit for 2^REFRESH_BITS clocks, fast enough that all eight
// look steady. Segments, decimal points and anodes are all active low.
module seven_seg #(
    parameter REFRESH_BITS = 17   // 1.3 ms per digit at 100 MHz
) (
    input  wire        clk,
    input  wire        rst,
    input  wire [31:0] value,   // digit 7 (leftmost) is value[31:28]
    input  wire [7:0]  dots,    // decimal point per digit, 1 = on
    output reg  [6:0]  seg,     // {g, f, e, d, c, b, a}
    output reg         dp,
    output reg  [7:0]  an
);
    reg [REFRESH_BITS+2:0] count;
    wire [2:0] digit = count[REFRESH_BITS+2:REFRESH_BITS];
    wire [3:0] nibble = value[digit*4 +: 4];

    always @(posedge clk) begin
        if (rst)
            count <= 0;
        else
            count <= count + 1'b1;
    end

    always @* begin
        an = ~(8'b1 << digit);
        dp = ~dots[digit];
        case (nibble)
            4'h0: seg = 7'b1000000;
            4'h1: seg = 7'b1111001;
            4'h2: seg = 7'b0100100;
            4'h3: seg = 7'b0110000;
            4'h4: seg = 7'b0011001;
            4'h5: seg = 7'b0010010;
            4'h6: seg = 7'b0000010;
            4'h7: seg = 7'b1111000;
            4'h8: seg = 7'b0000000;
            4'h9: seg = 7'b0010000;
            4'hA: seg = 7'b0001000;
            4'hB: seg = 7'b0000011;
            4'hC: seg = 7'b1000110;
            4'hD: seg = 7'b0100001;
            4'hE: seg = 7'b0000110;
            default: seg = 7'b0001110;   // F
        endcase
    end
endmodule
