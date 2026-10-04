`timescale 1ns / 1ps
`default_nettype none

// Video timing generator.  The defaults are CEA-861 1280x720@60 (VIC 4):
// 74.25 MHz pixel clock, 1650 x 750 total, positive hsync and vsync.
//
//   hcount_out  0 .. H_TOTAL-1   (active pixels are 0 .. ACTIVE_H_PIXELS-1)
//   vcount_out  0 .. V_TOTAL-1   (active lines  are 0 .. ACTIVE_LINES-1)
//   hs_out/vs_out  sync pulses (active high)
//   ad_out      "active draw": the current (hcount, vcount) is a visible pixel
//   nf_out      one-cycle pulse at (ACTIVE_H_PIXELS, ACTIVE_LINES): the first
//               cycle after the last visible pixel of a frame
//   fc_out      frame counter, counts nf pulses modulo FPS
//
// Every output is registered and describes the hcount/vcount on the same cycle.
// During reset the counters sit on the last blanking pixel (all flags low).
module video_sig_gen #(
    parameter int ACTIVE_H_PIXELS = 1280,
    parameter int H_FRONT_PORCH = 110,
    parameter int H_SYNC_WIDTH = 40,
    parameter int H_BACK_PORCH = 220,
    parameter int ACTIVE_LINES = 720,
    parameter int V_FRONT_PORCH = 5,
    parameter int V_SYNC_WIDTH = 5,
    parameter int V_BACK_PORCH = 20,
    parameter int FPS = 60,
    // counter widths (derived; do not override)
    parameter int HW = $clog2(ACTIVE_H_PIXELS + H_FRONT_PORCH + H_SYNC_WIDTH + H_BACK_PORCH),
    parameter int VW = $clog2(ACTIVE_LINES + V_FRONT_PORCH + V_SYNC_WIDTH + V_BACK_PORCH)
) (
    input wire pixel_clk_in,
    input wire rst_in,
    output logic [HW-1:0] hcount_out,
    output logic [VW-1:0] vcount_out,
    output logic vs_out,
    output logic hs_out,
    output logic ad_out,
    output logic nf_out,
    output logic [5:0] fc_out
);
  localparam int H_TOTAL = ACTIVE_H_PIXELS + H_FRONT_PORCH + H_SYNC_WIDTH + H_BACK_PORCH;
  localparam int V_TOTAL = ACTIVE_LINES + V_FRONT_PORCH + V_SYNC_WIDTH + V_BACK_PORCH;
  localparam logic [HW-1:0] H_LAST = HW'(H_TOTAL - 1);
  localparam logic [VW-1:0] V_LAST = VW'(V_TOTAL - 1);
  localparam logic [HW-1:0] H_ACT = HW'(ACTIVE_H_PIXELS);
  localparam logic [VW-1:0] V_ACT = VW'(ACTIVE_LINES);
  localparam logic [HW-1:0] HS_START = HW'(ACTIVE_H_PIXELS + H_FRONT_PORCH);
  localparam logic [HW-1:0] HS_END = HW'(ACTIVE_H_PIXELS + H_FRONT_PORCH + H_SYNC_WIDTH);
  localparam logic [VW-1:0] VS_START = VW'(ACTIVE_LINES + V_FRONT_PORCH);
  localparam logic [VW-1:0] VS_END = VW'(ACTIVE_LINES + V_FRONT_PORCH + V_SYNC_WIDTH);
  localparam logic [5:0] FC_LAST = 6'(FPS - 1);

  logic [HW-1:0] h_next;
  logic [VW-1:0] v_next;

  always_comb begin
    if (hcount_out == H_LAST) begin
      h_next = '0;
      v_next = (vcount_out == V_LAST) ? '0 : vcount_out + 1'b1;
    end else begin
      h_next = hcount_out + 1'b1;
      v_next = vcount_out;
    end
  end

  always_ff @(posedge pixel_clk_in) begin
    if (rst_in) begin
      // park on the last (blanking) pixel so the first clock after reset
      // is pixel (0, 0) of a complete frame
      hcount_out <= H_LAST;
      vcount_out <= V_LAST;
      hs_out <= 1'b0;
      vs_out <= 1'b0;
      ad_out <= 1'b0;
      nf_out <= 1'b0;
      fc_out <= '0;
    end else begin
      hcount_out <= h_next;
      vcount_out <= v_next;
      hs_out <= (h_next >= HS_START) && (h_next < HS_END);
      vs_out <= (v_next >= VS_START) && (v_next < VS_END);
      ad_out <= (h_next < H_ACT) && (v_next < V_ACT);
      nf_out <= (h_next == H_ACT) && (v_next == V_ACT);
      if ((h_next == H_ACT) && (v_next == V_ACT)) begin
        fc_out <= (fc_out == FC_LAST) ? '0 : fc_out + 1'b1;
      end
    end
  end
endmodule

`default_nettype wire
