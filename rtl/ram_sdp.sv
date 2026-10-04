`timescale 1ns / 1ps
`default_nettype none

// Simple dual-port RAM: one write port, one registered read port, same clock.
// Used for the double-buffered column table.
module ram_sdp #(
    parameter int WIDTH = 70,
    parameter int DEPTH = 2560,
    parameter int AW = $clog2(DEPTH)
) (
    input wire clk_in,
    input wire we_in,
    input wire [AW-1:0] waddr_in,
    input wire [WIDTH-1:0] wdata_in,
    input wire [AW-1:0] raddr_in,
    output logic [WIDTH-1:0] rdata_out
);
  logic [WIDTH-1:0] mem [DEPTH];

  always_ff @(posedge clk_in) begin
    if (we_in) mem[waddr_in] <= wdata_in;
    rdata_out <= mem[raddr_in];
  end
endmodule

`default_nettype wire
