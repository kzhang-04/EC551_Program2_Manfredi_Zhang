// Push-button conditioner: synchronizes the button to clk, debounces it,
// and emits a one-cycle pulse when the button is pressed (not released).
//
// The button must read the same for CYCLES consecutive clocks before the
// debounced level changes, so contact bounce shorter than that is ignored.
module debouncer #(
    parameter CYCLES = 1000000   // 10 ms at 100 MHz
) (
    input  wire clk,
    input  wire rst,
    input  wire btn,
    output reg  pulse
);
    localparam CW = $clog2(CYCLES + 1);

    reg sync0, sync1;   // two flip-flops guard against metastability
    reg stable;         // debounced level
    reg [CW-1:0] count;

    always @(posedge clk) begin
        if (rst) begin
            sync0  <= 1'b0;
            sync1  <= 1'b0;
            stable <= 1'b0;
            count  <= {CW{1'b0}};
            pulse  <= 1'b0;
        end else begin
            sync0 <= btn;
            sync1 <= sync0;
            pulse <= 1'b0;
            if (sync1 == stable)
                count <= {CW{1'b0}};
            else if (count == CYCLES - 1) begin
                stable <= sync1;
                count  <= {CW{1'b0}};
                pulse  <= sync1;   // pulse on the press only
            end else
                count <= count + 1'b1;
        end
    end
endmodule
