`timescale 1ns / 1ps
`default_nettype none

// Dual-port ROM (two independent read ports, registered, 1 cycle latency).
// Block RAM is true dual port, so the two readers never have to arbitrate.
module rom_2p #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 256,
    parameter INIT_FILE = "",
    parameter int AW = $clog2(DEPTH)
) (
    input wire clk_in,
    input wire [AW-1:0] addra_in,
    input wire [AW-1:0] addrb_in,
    output logic [WIDTH-1:0] douta_out,
    output logic [WIDTH-1:0] doutb_out
);
  logic [WIDTH-1:0] mem [DEPTH];
  initial $readmemh(INIT_FILE, mem);

  always_ff @(posedge clk_in) begin
    douta_out <= mem[addra_in];
    doutb_out <= mem[addrb_in];
  end
endmodule

`default_nettype wire
