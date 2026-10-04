`timescale 1ns / 1ps
`default_nettype none

// Single-port ROM with a registered output (1 cycle latency), initialised by
// $readmemh.  Synthesis maps it to block RAM (or LUTs when tiny).
module rom_1p #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 256,
    parameter INIT_FILE = "",
    parameter int AW = $clog2(DEPTH)
) (
    input wire clk_in,
    input wire [AW-1:0] addr_in,
    output logic [WIDTH-1:0] data_out
);
  logic [WIDTH-1:0] mem [DEPTH];
  initial $readmemh(INIT_FILE, mem);

  always_ff @(posedge clk_in) data_out <= mem[addr_in];
endmodule

`default_nettype wire
