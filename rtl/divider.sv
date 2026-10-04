`timescale 1ns / 1ps
`default_nettype none

// Sequential radix-2 restoring divider: one quotient bit per clock.
//
// Pulse data_valid_in with the operands; WIDTH+1 cycles later
// data_valid_out pulses with quotient_out = dividend / divisor and
// remainder_out = dividend % divisor (unsigned, floor).  Division by zero
// sets error_out and returns an all-ones quotient, which callers treat as
// "saturate".  busy_out is high while a division is in flight; a new
// request while busy is ignored.
//
// Why not a combinational '/'?  A 32-bit array divider is a long chain of
// 32 subtract-and-select rows: hundreds of LUTs and far too slow for a
// 74.25 MHz clock.  The ray engine has ~960 cycles per screen column, so
// spending 33 of them per division costs nothing and keeps the design small.
module divider #(
    parameter int WIDTH = 32
) (
    input wire clk_in,
    input wire rst_in,
    input wire [WIDTH-1:0] dividend_in,
    input wire [WIDTH-1:0] divisor_in,
    input wire data_valid_in,
    output logic [WIDTH-1:0] quotient_out,
    output logic [WIDTH-1:0] remainder_out,
    output logic data_valid_out,
    output logic error_out,
    output logic busy_out
);
  localparam int CW = $clog2(WIDTH + 1);

  logic [WIDTH-1:0] divisor;
  logic [WIDTH-1:0] quotient;   // shifts the dividend out, the quotient in
  logic [WIDTH-1:0] rem;        // always < divisor
  logic [CW-1:0] count;
  logic [WIDTH:0] shifted;
  logic [WIDTH:0] trial;

  always_comb begin
    shifted = {rem, quotient[WIDTH-1]};  // 2*rem + next bit: needs WIDTH+1 bits
    trial = shifted - {1'b0, divisor};
  end

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      busy_out <= 1'b0;
      data_valid_out <= 1'b0;
      error_out <= 1'b0;
      quotient_out <= '0;
      remainder_out <= '0;
      count <= '0;
      rem <= '0;
      quotient <= '0;
      divisor <= '0;
    end else begin
      data_valid_out <= 1'b0;
      if (!busy_out) begin
        if (data_valid_in) begin
          if (divisor_in == '0) begin
            quotient_out <= '1;
            remainder_out <= dividend_in;
            error_out <= 1'b1;
            data_valid_out <= 1'b1;
          end else begin
            busy_out <= 1'b1;
            error_out <= 1'b0;
            divisor <= divisor_in;
            quotient <= dividend_in;
            rem <= '0;
            count <= CW'(WIDTH);
          end
        end
      end else begin
        // restoring step: shift in the next dividend bit, subtract if it fits
        if (!trial[WIDTH]) begin
          rem <= trial[WIDTH-1:0];
          quotient <= {quotient[WIDTH-2:0], 1'b1};
        end else begin
          rem <= shifted[WIDTH-1:0];
          quotient <= {quotient[WIDTH-2:0], 1'b0};
        end
        count <= count - 1'b1;
        if (count == CW'(1)) begin
          busy_out <= 1'b0;
          data_valid_out <= 1'b1;
          quotient_out <= !trial[WIDTH] ? {quotient[WIDTH-2:0], 1'b1} : {quotient[WIDTH-2:0], 1'b0};
          remainder_out <= !trial[WIDTH] ? trial[WIDTH-1:0] : shifted[WIDTH-1:0];
        end
      end
    end
  end
endmodule

`default_nettype wire
