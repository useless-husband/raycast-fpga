`timescale 1ns / 1ps
`default_nettype none
/* verilator lint_off PINCONNECTEMPTY */

// 10:1 serialiser for one TMDS lane on a 7-series FPGA: two OSERDESE2 in
// master/slave cascade, DDR at 5x the pixel clock (371.25 MHz for 720p, so
// the lane runs at 742.5 Mbit/s).  Bit 0 leaves first, as DVI requires.
// Board-only: this file uses Xilinx primitives and is not simulated.
module tmds_serializer (
    input wire clk_pixel_in,
    input wire clk_5x_in,
    input wire rst_in,
    input wire [9:0] tmds_in,
    output logic tmds_out
);
  logic shift1, shift2;

  OSERDESE2 #(
      .DATA_RATE_OQ("DDR"),
      .DATA_RATE_TQ("SDR"),
      .DATA_WIDTH(10),
      .SERDES_MODE("MASTER"),
      .TRISTATE_WIDTH(1),
      .TBYTE_CTL("FALSE"),
      .TBYTE_SRC("FALSE")
  ) primary (
      .OQ(tmds_out),
      .OFB(),
      .TQ(),
      .TFB(),
      .SHIFTOUT1(),
      .SHIFTOUT2(),
      .TBYTEOUT(),
      .CLK(clk_5x_in),
      .CLKDIV(clk_pixel_in),
      .D1(tmds_in[0]),
      .D2(tmds_in[1]),
      .D3(tmds_in[2]),
      .D4(tmds_in[3]),
      .D5(tmds_in[4]),
      .D6(tmds_in[5]),
      .D7(tmds_in[6]),
      .D8(tmds_in[7]),
      .TCE(1'b0),
      .OCE(1'b1),
      .TBYTEIN(1'b0),
      .RST(rst_in),
      .SHIFTIN1(shift1),
      .SHIFTIN2(shift2),
      .T1(1'b0),
      .T2(1'b0),
      .T3(1'b0),
      .T4(1'b0)
  );

  OSERDESE2 #(
      .DATA_RATE_OQ("DDR"),
      .DATA_RATE_TQ("SDR"),
      .DATA_WIDTH(10),
      .SERDES_MODE("SLAVE"),
      .TRISTATE_WIDTH(1),
      .TBYTE_CTL("FALSE"),
      .TBYTE_SRC("FALSE")
  ) secondary (
      .OQ(),
      .OFB(),
      .TQ(),
      .TFB(),
      .SHIFTOUT1(shift1),
      .SHIFTOUT2(shift2),
      .TBYTEOUT(),
      .CLK(clk_5x_in),
      .CLKDIV(clk_pixel_in),
      .D1(1'b0),
      .D2(1'b0),
      .D3(tmds_in[8]),
      .D4(tmds_in[9]),
      .D5(1'b0),
      .D6(1'b0),
      .D7(1'b0),
      .D8(1'b0),
      .TCE(1'b0),
      .OCE(1'b1),
      .TBYTEIN(1'b0),
      .RST(rst_in),
      .SHIFTIN1(1'b0),
      .SHIFTIN2(1'b0),
      .T1(1'b0),
      .T2(1'b0),
      .T3(1'b0),
      .T4(1'b0)
  );
endmodule

`default_nettype wire
