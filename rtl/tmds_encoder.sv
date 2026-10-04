`timescale 1ns / 1ps
`default_nettype none

// TMDS 8b/10b encoder, DVI 1.0 section 3.3.3.
//
// Stage 1 (transition minimisation) picks XOR or XNOR chaining so the 9-bit
// word q_m has as few 0<->1 transitions as possible.  Stage 2 (DC balance)
// optionally inverts q_m[7:0] so the running disparity `cnt` (ones minus
// zeros sent so far) is pulled back towards zero.  During blanking the four
// control tokens carry {C1, C0}; for the blue channel that is {vsync, hsync}.
//
// One cycle latency.  cnt is reset to 0 whenever ve_in is low, as the spec
// requires; the reachable values are the even numbers -8..+8, so 5 bits
// (signed) are enough (tests/unit/test_tmds_encoder.py proves reachability).
module tmds_encoder (
    input wire clk_in,
    input wire rst_in,
    input wire [7:0] data_in,     // video data (red, green or blue)
    input wire [1:0] control_in,  // {C1, C0}
    input wire ve_in,             // video data enable
    output logic [9:0] tmds_out
);
  function automatic logic [3:0] popcount8(input logic [7:0] v);
    logic [3:0] n;
    n = 4'd0;
    for (int i = 0; i < 8; i++) n = n + {3'b000, v[i]};
    popcount8 = n;
  endfunction

  // ---- stage 1: transition-minimised q_m ---------------------------------
  function automatic logic [8:0] transition_min(input logic [7:0] d);
    logic [3:0] n1;
    logic use_xnor;
    logic [8:0] q;
    n1 = popcount8(d);
    use_xnor = (n1 > 4'd4) || ((n1 == 4'd4) && !d[0]);
    q[0] = d[0];
    for (int i = 1; i < 8; i++) begin
      q[i] = use_xnor ? ~(q[i-1] ^ d[i]) : (q[i-1] ^ d[i]);
    end
    q[8] = ~use_xnor;
    transition_min = q;
  endfunction

  logic [8:0] q_m;
  assign q_m = transition_min(data_in);

  // ---- stage 2: DC balance -------------------------------------------------
  logic signed [4:0] cnt;
  logic [3:0] n1_q;
  // All disparity arithmetic is 5-bit two's complement.  Intermediate sums
  // may leave -16..15, but every reachable result is in -8..+8, so the
  // wrap-around of modular arithmetic gives the exact value.
  logic signed [4:0] diff;     // N1(q_m[7:0]) - N0(q_m[7:0]) = 2*N1 - 8
  logic signed [4:0] cnt_next;
  logic [9:0] sym;

  always_comb begin
    n1_q = popcount8(q_m[7:0]);
    diff = $signed({n1_q, 1'b0}) - 5'sd8;
    if ((cnt == 5'sd0) || (n1_q == 4'd4)) begin
      sym = {~q_m[8], q_m[8], q_m[8] ? q_m[7:0] : ~q_m[7:0]};
      cnt_next = q_m[8] ? (cnt + diff) : (cnt - diff);
    end else if ((!cnt[4] && (n1_q > 4'd4)) || (cnt[4] && (n1_q < 4'd4))) begin
      // cnt > 0 and more ones, or cnt < 0 and more zeros: invert
      sym = {1'b1, q_m[8], ~q_m[7:0]};
      cnt_next = cnt + (q_m[8] ? 5'sd2 : 5'sd0) - diff;
    end else begin
      sym = {1'b0, q_m[8], q_m[7:0]};
      cnt_next = cnt - (q_m[8] ? 5'sd0 : 5'sd2) + diff;
    end
  end

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      tmds_out <= 10'd0;
      cnt <= 5'sd0;
    end else if (!ve_in) begin
      cnt <= 5'sd0;
      case (control_in)
        2'b00: tmds_out <= 10'b1101010100;
        2'b01: tmds_out <= 10'b0010101011;
        2'b10: tmds_out <= 10'b0101010100;
        default: tmds_out <= 10'b1010101011;
      endcase
    end else begin
      tmds_out <= sym;
      cnt <= cnt_next;
    end
  end
endmodule

`default_nettype wire
