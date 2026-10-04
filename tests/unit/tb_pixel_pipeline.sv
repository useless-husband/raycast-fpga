`timescale 1ns / 1ps
`default_nettype none

// Test wrapper: a small video timing generator drives pixel_pipeline, whose
// column table is a memory the cocotb test writes directly (table_mem[i]).
module tb_pixel_pipeline #(
    parameter int W = 64,
    parameter int H = 48
) (
    input wire clk_in,
    input wire rst_in,
    input wire front_in,
    input wire valid_in,
    output logic [23:0] rgb_out,
    output logic de_out,
    output logic hs_out,
    output logic vs_out,
    output logic [6:0] hcount,
    output logic [5:0] vcount,
    output logic ad,
    output logic hs,
    output logic vs
);
  logic nf;
  logic [5:0] fc;
  video_sig_gen #(
      .ACTIVE_H_PIXELS(W), .H_FRONT_PORCH(4), .H_SYNC_WIDTH(4), .H_BACK_PORCH(8),
      .ACTIVE_LINES(H), .V_FRONT_PORCH(2), .V_SYNC_WIDTH(2), .V_BACK_PORCH(3)
  ) timing (
      .pixel_clk_in(clk_in), .rst_in(rst_in), .hcount_out(hcount), .vcount_out(vcount),
      .vs_out(vs), .hs_out(hs), .ad_out(ad), .nf_out(nf), .fc_out(fc)
  );

  logic [69:0] table_mem[2 * W];
  logic [6:0] table_addr;
  logic [69:0] table_data;
  always_ff @(posedge clk_in) table_data <= table_mem[table_addr];

  pixel_pipeline #(.W(W), .H(H), .HW(7), .VW(6)) dut (
      .clk_in, .rst_in, .hcount_in(hcount), .vcount_in(vcount), .ad_in(ad), .hs_in(hs),
      .vs_in(vs), .front_in, .valid_in, .table_addr_out(table_addr), .table_data_in(table_data),
      .rgb_out, .de_out, .hs_out, .vs_out
  );
endmodule

`default_nettype wire
