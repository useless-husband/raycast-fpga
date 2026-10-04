`timescale 1ns / 1ps
`default_nettype none

// Player state machine: once per frame (update_in pulse) it turns, then moves
// along x, then along y, checking the map for walls in between.  Checking the
// two axes separately is what makes the player slide along a wall instead of
// sticking to it.  A move is accepted only if a probe point RADIUS ahead of
// the new position (in the direction of motion) is an empty cell, so the
// player never enters a wall and keeps a quarter cell from walls it walks at.
//
// Formats: position Q5.14 (19 bits), angle 10 bits (1024 per turn),
// dir/plane Q1.14.  dir = (cos a, sin a); plane = PLANE_K * (-sin a, cos a),
// i.e. the camera plane points to the player's right, which is +y when
// facing +x because map rows grow downwards.
//
// load_in (with update_in) teleports the player instead of moving it; the
// system tests use it to place the camera at random positions and angles.
//
// Golden reference: model/raycast_model.py: player_update() and view_of().
module player #(
    parameter int SPEED = 1024,      // Q0.14 cells per frame
    parameter int TURN = 4,          // angle units per frame
    parameter int RADIUS = 4096,     // Q0.14 collision probe distance
    parameter int PLANE_K = 10813,   // Q1.14, tan(FOV/2) = 0.66
    parameter TRIG_FILE = "rtl/mem/trig.hex"
) (
    input wire clk_in,
    input wire rst_in,
    input wire update_in,
    input wire [5:0] btn_in,          // {strafe_r, strafe_l, right, left, back, fwd}
    input wire load_in,
    input wire [18:0] load_x_in,
    input wire [18:0] load_y_in,
    input wire [9:0] load_angle_in,
    output logic [9:0] map_addr_out,  // {y[4:0], x[4:0]}
    input wire [3:0] map_data_in,     // 1 cycle after map_addr_out
    output logic [18:0] pos_x_out,
    output logic [18:0] pos_y_out,
    output logic [9:0] angle_out,
    output logic signed [15:0] dir_x_out,
    output logic signed [15:0] dir_y_out,
    output logic signed [15:0] plane_x_out,
    output logic signed [15:0] plane_y_out,
    output logic done_out
);
`include "map_start.svh"

  typedef enum logic [2:0] {
    IDLE,
    TRIG_ADDR,
    TRIG_DATA,
    X_ADDR,
    X_CHECK,
    Y_ADDR,
    Y_CHECK,
    PLANE
  } state_t;
  state_t state;

  // ---- trig ROM: port A = cos(angle), port B = cos(angle - 90deg) = sin(angle)
  logic [15:0] cos_raw, sin_raw;
  rom_2p #(
      .WIDTH(16),
      .DEPTH(1024),
      .INIT_FILE(TRIG_FILE)
  ) trig_rom (
      .clk_in(clk_in),
      .addra_in(angle_out),
      .addrb_in(angle_out - 10'd256),
      .douta_out(cos_raw),
      .doutb_out(sin_raw)
  );

  logic [3:0] mv;                      // latched {strafe_r, strafe_l, back, fwd}
  logic loading;
  logic signed [19:0] dx, dy;          // Q.14 displacement this frame

  // ---- movement vector: forward * (c, s) + strafe * (-s, c), scaled by SPEED
  logic signed [17:0] c18, s18, mx, my;
  logic signed [35:0] mx_scaled, my_scaled;
  always_comb begin
    c18 = 18'($signed(cos_raw));
    s18 = 18'($signed(sin_raw));
    mx = 18'sd0;
    my = 18'sd0;
    if (mv[0] && !mv[1]) begin mx = mx + c18; my = my + s18; end  // forward
    if (mv[1] && !mv[0]) begin mx = mx - c18; my = my - s18; end  // back
    if (mv[3] && !mv[2]) begin mx = mx - s18; my = my + c18; end  // strafe right
    if (mv[2] && !mv[3]) begin mx = mx + s18; my = my - c18; end  // strafe left
    mx_scaled = 36'(mx) * 36'(SPEED);
    my_scaled = 36'(my) * 36'(SPEED);
  end

  // ---- collision probes (combinational from registers that are stable
  //      across the *_ADDR / *_CHECK pair)
  localparam logic signed [20:0] RAD = 21'(RADIUS);
  localparam logic signed [20:0] MAP_END = 21'sd524288;  // 32 << 14
  logic signed [20:0] nx, ny, probe_x, probe_y;
  logic x_ok, y_ok;
  always_comb begin
    nx = $signed({2'b00, pos_x_out}) + 21'(dx);
    ny = $signed({2'b00, pos_y_out}) + 21'(dy);
    probe_x = (dx > 0) ? nx + RAD : nx - RAD;
    probe_y = (dy > 0) ? ny + RAD : ny - RAD;
    x_ok = (probe_x >= 0) && (probe_x < MAP_END);  // inside the 32x32 map
    y_ok = (probe_y >= 0) && (probe_y < MAP_END);
  end

  always_comb begin
    case (state)
      X_ADDR:  map_addr_out = {pos_y_out[18:14], probe_x[18:14]};
      Y_ADDR:  map_addr_out = {probe_y[18:14], pos_x_out[18:14]};
      default: map_addr_out = 10'd0;
    endcase
  end

  // ---- plane = K * (-sin, cos); products are Q2.28, keep Q1.14 (floor)
  logic signed [16:0] neg_s;
  logic signed [32:0] plane_x_full, plane_y_full;
  always_comb begin
    neg_s = -(17'(dir_y_out));
    plane_x_full = 33'(neg_s) * 33'(PLANE_K);
    plane_y_full = 33'(dir_x_out) * 33'(PLANE_K);
  end

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      state <= IDLE;
      pos_x_out <= START_X;
      pos_y_out <= START_Y;
      angle_out <= START_ANGLE;
      dir_x_out <= 16'sd0;
      dir_y_out <= 16'sd0;
      plane_x_out <= 16'sd0;
      plane_y_out <= 16'sd0;
      mv <= 4'd0;
      loading <= 1'b0;
      dx <= 20'sd0;
      dy <= 20'sd0;
      done_out <= 1'b0;
    end else begin
      done_out <= 1'b0;
      case (state)
        IDLE: begin
          if (update_in) begin
            loading <= load_in;
            if (load_in) begin
              pos_x_out <= load_x_in;
              pos_y_out <= load_y_in;
              angle_out <= load_angle_in;
              mv <= 4'd0;
            end else begin
              mv <= {btn_in[5:4], btn_in[1:0]};
              if (btn_in[3] && !btn_in[2]) angle_out <= angle_out + 10'(TURN);
              if (btn_in[2] && !btn_in[3]) angle_out <= angle_out - 10'(TURN);
            end
            state <= TRIG_ADDR;
          end
        end
        TRIG_ADDR: state <= TRIG_DATA;  // ROM reads the new angle this cycle
        TRIG_DATA: begin
          dir_x_out <= $signed(cos_raw);
          dir_y_out <= $signed(sin_raw);
          dx <= 20'(mx_scaled >>> 14);
          dy <= 20'(my_scaled >>> 14);
          if (loading) state <= PLANE;
          else state <= X_ADDR;
        end
        X_ADDR: state <= X_CHECK;
        X_CHECK: begin
          if ((dx != 0) && x_ok && (map_data_in == 4'd0)) pos_x_out <= 19'(nx);
          state <= Y_ADDR;
        end
        Y_ADDR: state <= Y_CHECK;
        Y_CHECK: begin
          if ((dy != 0) && y_ok && (map_data_in == 4'd0)) pos_y_out <= 19'(ny);
          state <= PLANE;
        end
        PLANE: begin
          plane_x_out <= 16'(plane_x_full >>> 14);
          plane_y_out <= 16'(plane_y_full >>> 14);
          done_out <= 1'b1;
          state <= IDLE;
        end
        default: state <= IDLE;
      endcase
    end
  end
endmodule

`default_nettype wire
