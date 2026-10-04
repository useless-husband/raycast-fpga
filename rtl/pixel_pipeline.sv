`timescale 1ns / 1ps
`default_nettype none

// Pixel pipeline: turns (hcount, vcount) into an RGB888 pixel by reading the
// column table, the texture ROM and the palette ROM.  Nothing is stored per
// pixel; the whole 1280x720 image is regenerated on the fly every frame.
//
//   stage 0  column-table address = front buffer base + hcount
//   stage 1  table entry arrives; wall/background test; texture row offset
//   stage 2  tex_y = ((y - H/2 + lh_half) * step) >> 16;  texture address
//   stage 3  palette index arrives from the texture ROM
//   stage 4  RGB arrives from the palette ROM; background colour ready
//   stage 5  shade and select -> rgb_out (registered)
//
// hs/vs/de travel through the same five registers, so they stay aligned
// with the pixel they belong to (LATENCY = 5).
//
// Golden reference: model/raycast_model.py: pixel() and background().
module pixel_pipeline #(
    parameter int W = 1280,
    parameter int H = 720,
    parameter int HW = 11,
    parameter int VW = 10,
    parameter TEX_FILE = "rtl/mem/tex.hex",
    parameter PAL_FILE = "rtl/mem/palette.hex",
    parameter int TAW = $clog2(2 * W)
) (
    input wire clk_in,
    input wire rst_in,
    input wire [HW-1:0] hcount_in,
    input wire [VW-1:0] vcount_in,
    input wire ad_in,
    input wire hs_in,
    input wire vs_in,
    input wire front_in,
    input wire valid_in,
    output logic [TAW-1:0] table_addr_out,
    input wire [69:0] table_data_in,
    output logic [23:0] rgb_out,
    output logic de_out,
    output logic hs_out,
    output logic vs_out
);
  localparam int HALF = H / 2;
  localparam logic [9:0] HALF10 = 10'(HALF);
  localparam logic [31:0] ROW_MUL = 32'((8 << 16) / HALF);
  localparam logic [23:0] CEIL_RGB = 24'h384058;
  localparam logic [23:0] FLOOR_RGB = 24'h584C40;

  function automatic logic [23:0] shade_rgb(input logic [23:0] c, input logic [2:0] s);
    logic [11:0] k;
    logic [7:0] r, g, b;
    k = 12'd8 - 12'(s);
    r = 8'((12'(c[23:16]) * k) >> 3);
    g = 8'((12'(c[15:8]) * k) >> 3);
    b = 8'((12'(c[7:0]) * k) >> 3);
    shade_rgb = {r, g, b};
  endfunction

  // ---- stage 0 -------------------------------------------------------------
  logic [TAW-1:0] col;
  always_comb begin
    col = (hcount_in < HW'(W)) ? TAW'(hcount_in) : '0;
    table_addr_out = front_in ? col + TAW'(W) : col;
  end

  // ---- stage 1 -------------------------------------------------------------
  logic [9:0] y1;
  logic ad1, hs1, vs1;
  logic [9:0] ds1, de1;
  logic [2:0] tid1, sh1;
  logic [5:0] tx1;
  logic [14:0] lhh1;
  logic [22:0] st1;
  logic wall1, ceil1;
  logic [21:0] dy1;
  logic [9:0] d1;
  logic [31:0] lvl1;

  always_comb begin
    {ds1, de1, tid1, tx1, sh1, lhh1, st1} = table_data_in;
    wall1 = (y1 >= ds1) && (y1 <= de1);
    // y - H/2 + lh_half; only bits 21:0 matter for the texture row (mod 2^22)
    dy1 = 22'(y1) - 22'(HALF10) + 22'(lhh1);
    ceil1 = y1 < HALF10;
    d1 = ceil1 ? HALF10 - 10'd1 - y1 : y1 - HALF10;   // rows away from the horizon
    lvl1 = 32'(d1) * ROW_MUL;
  end

  // ---- stage 2 -------------------------------------------------------------
  logic [21:0] prod2;
  logic [2:0] tid2, sh2;
  logic [5:0] tx2;
  logic wall2, ceil2, ad2, hs2, vs2;
  logic [2:0] bgs2;
  logic [5:0] ty2;
  logic [14:0] tex_addr2;

  always_comb begin
    ty2 = prod2[21:16];
    tex_addr2 = {tid2, ty2, tx2};
  end

  // ---- stage 3 -------------------------------------------------------------
  logic [7:0] pal_idx3;
  logic [2:0] sh3, bgs3;
  logic wall3, ceil3, ad3, hs3, vs3;

  rom_1p #(
      .WIDTH(8),
      .DEPTH(8 * 64 * 64),
      .INIT_FILE(TEX_FILE)
  ) tex_rom (
      .clk_in(clk_in),
      .addr_in(tex_addr2),
      .data_out(pal_idx3)
  );

  // ---- stage 4 -------------------------------------------------------------
  logic [23:0] rgb4, bg4;
  logic [2:0] sh4;
  logic wall4, ad4, hs4, vs4;

  rom_1p #(
      .WIDTH(24),
      .DEPTH(256),
      .INIT_FILE(PAL_FILE)
  ) palette_rom (
      .clk_in(clk_in),
      .addr_in(pal_idx3),
      .data_out(rgb4)
  );

  logic unused;  // st1[22] only matters mod 2^22; low product bits are fractions
  assign unused = ^{st1[22], lvl1[15:0], prod2[15:0]};

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      y1 <= '0;
      {ad1, hs1, vs1} <= '0;
      prod2 <= '0;
      {tid2, sh2, tx2, bgs2} <= '0;
      {wall2, ceil2, ad2, hs2, vs2} <= '0;
      {sh3, bgs3} <= '0;
      {wall3, ceil3, ad3, hs3, vs3} <= '0;
      bg4 <= '0;
      sh4 <= '0;
      {wall4, ad4, hs4, vs4} <= '0;
      rgb_out <= '0;
      {de_out, hs_out, vs_out} <= '0;
    end else begin
      // stage 1
      y1 <= 10'(vcount_in);
      {ad1, hs1, vs1} <= {ad_in, hs_in, vs_in};
      // stage 2
      prod2 <= dy1 * 22'(st1);
      {tid2, sh2, tx2} <= {tid1, sh1, tx1};
      {wall2, ceil2, ad2, hs2, vs2} <= {wall1, ceil1, ad1, hs1, vs1};
      bgs2 <= 3'd7 - ((lvl1[31:16] > 16'd7) ? 3'd7 : lvl1[18:16]);
      // stage 3
      {sh3, bgs3} <= {sh2, bgs2};
      {wall3, ceil3, ad3, hs3, vs3} <= {wall2, ceil2, ad2, hs2, vs2};
      // stage 4
      bg4 <= shade_rgb(ceil3 ? CEIL_RGB : FLOOR_RGB, bgs3);
      sh4 <= sh3;
      {wall4, ad4, hs4, vs4} <= {wall3, ad3, hs3, vs3};
      // stage 5
      if (!ad4 || !valid_in) rgb_out <= 24'd0;
      else rgb_out <= wall4 ? shade_rgb(rgb4, sh4) : bg4;
      {de_out, hs_out, vs_out} <= {ad4, hs4, vs4};
    end
  end
endmodule

`default_nettype wire
