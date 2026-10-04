`timescale 1ns / 1ps
`default_nettype none

// Test wrapper: ray_engine plus a map memory the cocotb test can overwrite
// directly (map_mem[i]), so every frame can use a different random map.
module tb_ray_engine #(
    parameter int W = 64,
    parameter int H = 48
) (
    input wire clk_in,
    input wire rst_in,
    input wire start_in,
    input wire [18:0] pos_x_in,
    input wire [18:0] pos_y_in,
    input wire signed [15:0] dir_x_in,
    input wire signed [15:0] dir_y_in,
    input wire signed [15:0] plane_x_in,
    input wire signed [15:0] plane_y_in,
    output logic col_we_out,
    output logic [$clog2(W)-1:0] col_x_out,
    output logic [69:0] col_data_out,
    output logic busy_out,
    output logic done_out,
    output logic [23:0] cycles_out
);
  logic [3:0] map_mem[1024];
  logic [9:0] map_addr;
  logic [3:0] map_data;
  always_ff @(posedge clk_in) map_data <= map_mem[map_addr];

  ray_engine #(.W(W), .H(H)) dut (
      .clk_in, .rst_in, .start_in, .pos_x_in, .pos_y_in, .dir_x_in, .dir_y_in,
      .plane_x_in, .plane_y_in,
      .map_addr_out(map_addr), .map_data_in(map_data),
      .col_we_out, .col_x_out, .col_data_out, .busy_out, .done_out, .cycles_out
  );
endmodule

`default_nettype wire
