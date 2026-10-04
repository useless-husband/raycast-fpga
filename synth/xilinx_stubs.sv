`timescale 1ns / 1ps
// Behavioural stand-ins for the four Xilinx primitives used by rtl/board/,
// only so `make lint` can run Verilator over the board top.  Never used for
// synthesis (Yosys has the real cell library) or for simulation.
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off UNUSEDPARAM */
/* verilator lint_off DECLFILENAME */
/* verilator lint_off MULTITOP */
module BUFG (input wire I, output wire O);
  assign O = I;
endmodule

module OBUFDS (input wire I, output wire O, output wire OB);
  assign O = I;
  assign OB = ~I;
endmodule

module MMCME2_BASE #(
    parameter BANDWIDTH = "OPTIMIZED", parameter real CLKIN1_PERIOD = 10.0,
    parameter integer DIVCLK_DIVIDE = 1, parameter real CLKFBOUT_MULT_F = 5.0,
    parameter real CLKOUT0_DIVIDE_F = 1.0, parameter integer CLKOUT1_DIVIDE = 1,
    parameter STARTUP_WAIT = "FALSE"
) (
    input wire CLKIN1, CLKFBIN, PWRDWN, RST,
    output wire CLKFBOUT, CLKFBOUTB, CLKOUT0, CLKOUT0B, CLKOUT1, CLKOUT1B, CLKOUT2, CLKOUT2B,
    output wire CLKOUT3, CLKOUT3B, CLKOUT4, CLKOUT5, CLKOUT6, LOCKED
);
  assign {CLKFBOUT, CLKFBOUTB, CLKOUT0, CLKOUT0B, CLKOUT1, CLKOUT1B, CLKOUT2, CLKOUT2B} = {8{CLKIN1}};
  assign {CLKOUT3, CLKOUT3B, CLKOUT4, CLKOUT5, CLKOUT6} = {5{CLKIN1}};
  assign LOCKED = 1'b1;
endmodule

module OSERDESE2 #(
    parameter DATA_RATE_OQ = "DDR", parameter DATA_RATE_TQ = "SDR", parameter integer DATA_WIDTH = 4,
    parameter SERDES_MODE = "MASTER", parameter integer TRISTATE_WIDTH = 4,
    parameter TBYTE_CTL = "FALSE", parameter TBYTE_SRC = "FALSE"
) (
    output wire OQ, OFB, TQ, TFB, SHIFTOUT1, SHIFTOUT2, TBYTEOUT,
    input wire CLK, CLKDIV, D1, D2, D3, D4, D5, D6, D7, D8, TCE, OCE, TBYTEIN, RST,
    input wire SHIFTIN1, SHIFTIN2, T1, T2, T3, T4
);
  assign {OQ, OFB, TQ, TFB, SHIFTOUT1, SHIFTOUT2, TBYTEOUT} = {7{D1}};
endmodule
