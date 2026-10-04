`timescale 1ns / 1ps
`default_nettype none
// Unconnected primitive/debug outputs are intentional here.
/* verilator lint_off PINCONNECTEMPTY */

// Board top for the Real Digital Urbana (Spartan-7 XC7S50), the board used by
// MIT 6.205.  NOT TESTED ON HARDWARE: it is synthesised with Yosys only.
//
//   clk_100mhz -> MMCM -> 74.25 MHz pixel clock + 371.25 MHz (5x) serial clock
//   btn[0]      reset;  btn[1] forward (backward while sw[0] is on)
//   btn[2]/[3]  turn left/right (strafe while sw[1] is on)
//   led[0]      MMCM locked;  led[1] heartbeat;  led[15:8] dropped frames
//   HDMI        three TMDS data lanes + a TMDS clock lane (10'b0000011111)
module top_level (
    input wire clk_100mhz,
    input wire [3:0] btn,
    input wire [15:0] sw,
    output logic [15:0] led,
    output logic [2:0] hdmi_tx_p,
    output logic [2:0] hdmi_tx_n,
    output logic hdmi_clk_p,
    output logic hdmi_clk_n
);
  // ---- clocks: VCO = 100 MHz * 37.125 / 5 = 742.5 MHz ----------------------
  logic clk_fb, clk_fb_buf, clk_pixel_raw, clk_5x_raw, clk_pixel, clk_5x, locked;

  MMCME2_BASE #(
      .BANDWIDTH("OPTIMIZED"),
      .CLKIN1_PERIOD(10.0),
      .DIVCLK_DIVIDE(5),
      .CLKFBOUT_MULT_F(37.125),
      .CLKOUT0_DIVIDE_F(10.0),  // 74.25 MHz
      .CLKOUT1_DIVIDE(2),       // 371.25 MHz
      .STARTUP_WAIT("FALSE")
  ) mmcm (
      .CLKIN1(clk_100mhz),
      .CLKFBIN(clk_fb_buf),
      .CLKFBOUT(clk_fb),
      .CLKFBOUTB(),
      .CLKOUT0(clk_pixel_raw),
      .CLKOUT0B(),
      .CLKOUT1(clk_5x_raw),
      .CLKOUT1B(),
      .CLKOUT2(),
      .CLKOUT2B(),
      .CLKOUT3(),
      .CLKOUT3B(),
      .CLKOUT4(),
      .CLKOUT5(),
      .CLKOUT6(),
      .LOCKED(locked),
      .PWRDWN(1'b0),
      .RST(1'b0)
  );
  BUFG fb_buf (.I(clk_fb), .O(clk_fb_buf));
  BUFG pixel_buf (.I(clk_pixel_raw), .O(clk_pixel));
  BUFG fast_buf (.I(clk_5x_raw), .O(clk_5x));

  // ---- reset: held until the MMCM locks, released synchronously -----------
  logic [2:0] rst_sync;
  always_ff @(posedge clk_pixel or negedge locked) begin
    if (!locked) rst_sync <= 3'b111;
    else rst_sync <= {rst_sync[1:0], btn[0]};
  end
  logic rst;
  assign rst = rst_sync[2];

  // ---- the design -----------------------------------------------------------
  logic [5:0] buttons;  // {strafe_r, strafe_l, right, left, back, fwd}
  assign buttons = {btn[3] & sw[1], btn[2] & sw[1], btn[3] & ~sw[1], btn[2] & ~sw[1],
                    btn[1] & sw[0], btn[1] & ~sw[0]};

  logic [9:0] tmds_red, tmds_green, tmds_blue;
  logic [15:0] drops;

  raycast_system system (
      .clk_in(clk_pixel),
      .rst_in(rst),
      .btn_in(buttons),
      .tmds_red_out(tmds_red),
      .tmds_green_out(tmds_green),
      .tmds_blue_out(tmds_blue),
      .dbg_load_in(1'b0),
      .dbg_x_in(19'd0),
      .dbg_y_in(19'd0),
      .dbg_angle_in(10'd0),
      .dbg_rgb_out(),
      .dbg_de_out(),
      .dbg_hs_out(),
      .dbg_vs_out(),
      .dbg_col_we_out(),
      .dbg_col_x_out(),
      .dbg_col_data_out(),
      .dbg_engine_start_out(),
      .dbg_engine_done_out(),
      .dbg_swap_out(),
      .dbg_pos_x_out(),
      .dbg_pos_y_out(),
      .dbg_angle_out(),
      .dbg_engine_cycles_out(),
      .dbg_drops_out(drops)
  );

  logic [25:0] heartbeat;
  always_ff @(posedge clk_pixel) heartbeat <= heartbeat + 1'b1;
  assign led = {drops[7:0], 6'd0, heartbeat[25], locked};

  logic unused;
  assign unused = ^{sw[15:2], drops[15:8]};

  // ---- serialisers and differential outputs ---------------------------------
  logic [2:0] lane;
  logic clk_lane;
  tmds_serializer ser_blue (.clk_pixel_in(clk_pixel), .clk_5x_in(clk_5x), .rst_in(rst),
                            .tmds_in(tmds_blue), .tmds_out(lane[0]));
  tmds_serializer ser_green (.clk_pixel_in(clk_pixel), .clk_5x_in(clk_5x), .rst_in(rst),
                             .tmds_in(tmds_green), .tmds_out(lane[1]));
  tmds_serializer ser_red (.clk_pixel_in(clk_pixel), .clk_5x_in(clk_5x), .rst_in(rst),
                           .tmds_in(tmds_red), .tmds_out(lane[2]));
  tmds_serializer ser_clk (.clk_pixel_in(clk_pixel), .clk_5x_in(clk_5x), .rst_in(rst),
                           .tmds_in(10'b0000011111), .tmds_out(clk_lane));

  OBUFDS obuf0 (.I(lane[0]), .O(hdmi_tx_p[0]), .OB(hdmi_tx_n[0]));
  OBUFDS obuf1 (.I(lane[1]), .O(hdmi_tx_p[1]), .OB(hdmi_tx_n[1]));
  OBUFDS obuf2 (.I(lane[2]), .O(hdmi_tx_p[2]), .OB(hdmi_tx_n[2]));
  OBUFDS obufc (.I(clk_lane), .O(hdmi_clk_p), .OB(hdmi_clk_n));
endmodule

`default_nettype wire
