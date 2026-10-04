`timescale 1ns / 1ps
`default_nettype none

// Push-button debouncer.  A two-flop synchroniser removes metastability
// (buttons are asynchronous to the pixel clock); the counter then only lets
// the output follow the input once it has held a new value for
// STABLE_CYCLES consecutive clocks.  Mechanical bounce shorter than that
// never reaches the player logic.  On the board STABLE_CYCLES is ~5 ms
// (371,250 cycles at 74.25 MHz); simulations use a smaller value.
module debouncer #(
    parameter int STABLE_CYCLES = 371250
) (
    input wire clk_in,
    input wire rst_in,
    input wire dirty_in,
    output logic clean_out
);
  localparam int CW = $clog2(STABLE_CYCLES + 1);
  localparam logic [CW-1:0] LAST = CW'(STABLE_CYCLES - 1);

  logic [1:0] sync;
  logic [CW-1:0] count;

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      sync <= 2'b00;
      count <= '0;
      clean_out <= 1'b0;
    end else begin
      sync <= {sync[0], dirty_in};
      if (sync[1] == clean_out) begin
        count <= '0;
      end else if (count == LAST) begin
        clean_out <= sync[1];
        count <= '0;
      end else begin
        count <= count + 1'b1;
      end
    end
  end
endmodule

`default_nettype wire
