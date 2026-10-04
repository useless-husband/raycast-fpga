`timescale 1ns / 1ps
`default_nettype none

// Portable top: everything except the board's clocking and serialisers.
// One clock domain (the pixel clock), so there is no clock-domain crossing.
//
// Frame protocol (double-buffered column table):
//
//   nf pulse (end of the visible frame)
//     if the engine has finished the back half: swap halves, then
//     UPDATE  player moves once from the debounced buttons
//     RENDER  ray engine fills the (new) back half for the next frame
//     READY   wait for the next nf
//   If nf arrives while still RENDER, the frame is shown again and the
//   drop counter increments (never happens at 720p; see DESIGN.md).
//
// The dbg_* ports exist for the testbenches (teleport the player, tap the
// column-table writes, see pixels before TMDS).  On the board the inputs
// are tied low and the outputs left open, and synthesis removes them.
module raycast_system #(
    parameter int ACTIVE_H = 1280,
    parameter int H_FP = 110,
    parameter int H_SYNC = 40,
    parameter int H_BP = 220,
    parameter int ACTIVE_V = 720,
    parameter int V_FP = 5,
    parameter int V_SYNC = 5,
    parameter int V_BP = 20,
    parameter int DEBOUNCE_CYCLES = 371250,  // 5 ms at 74.25 MHz
    parameter MAP_FILE = "rtl/mem/map.hex",
    parameter TRIG_FILE = "rtl/mem/trig.hex",
    parameter TEX_FILE = "rtl/mem/tex.hex",
    parameter PAL_FILE = "rtl/mem/palette.hex",
    parameter int XW = $clog2(ACTIVE_H)
) (
    input wire clk_in,
    input wire rst_in,
    input wire [5:0] btn_in,  // raw {strafe_r, strafe_l, right, left, back, fwd}
    output logic [9:0] tmds_red_out,
    output logic [9:0] tmds_green_out,
    output logic [9:0] tmds_blue_out,

    input wire dbg_load_in,
    input wire [18:0] dbg_x_in,
    input wire [18:0] dbg_y_in,
    input wire [9:0] dbg_angle_in,
    output logic [23:0] dbg_rgb_out,
    output logic dbg_de_out,
    output logic dbg_hs_out,
    output logic dbg_vs_out,
    output logic dbg_col_we_out,
    output logic [XW-1:0] dbg_col_x_out,
    output logic [69:0] dbg_col_data_out,
    output logic dbg_engine_start_out,
    output logic dbg_engine_done_out,
    output logic dbg_swap_out,
    output logic [18:0] dbg_pos_x_out,
    output logic [18:0] dbg_pos_y_out,
    output logic [9:0] dbg_angle_out,
    output logic [23:0] dbg_engine_cycles_out,
    output logic [15:0] dbg_drops_out
);
  localparam int HW = $clog2(ACTIVE_H + H_FP + H_SYNC + H_BP);
  localparam int VW = $clog2(ACTIVE_V + V_FP + V_SYNC + V_BP);
  localparam int TAW = $clog2(2 * ACTIVE_H);

  // ---- video timing ---------------------------------------------------------
  logic [HW-1:0] hcount;
  logic [VW-1:0] vcount;
  logic hs, vs, ad, nf;
  logic [5:0] fc;

  video_sig_gen #(
      .ACTIVE_H_PIXELS(ACTIVE_H),
      .H_FRONT_PORCH(H_FP),
      .H_SYNC_WIDTH(H_SYNC),
      .H_BACK_PORCH(H_BP),
      .ACTIVE_LINES(ACTIVE_V),
      .V_FRONT_PORCH(V_FP),
      .V_SYNC_WIDTH(V_SYNC),
      .V_BACK_PORCH(V_BP)
  ) timing (
      .pixel_clk_in(clk_in),
      .rst_in(rst_in),
      .hcount_out(hcount),
      .vcount_out(vcount),
      .vs_out(vs),
      .hs_out(hs),
      .ad_out(ad),
      .nf_out(nf),
      .fc_out(fc)
  );

  // ---- buttons -------------------------------------------------------------
  logic [5:0] btn_clean;
  for (genvar i = 0; i < 6; i++) begin : g_debounce
    debouncer #(
        .STABLE_CYCLES(DEBOUNCE_CYCLES)
    ) db (
        .clk_in(clk_in),
        .rst_in(rst_in),
        .dirty_in(btn_in[i]),
        .clean_out(btn_clean[i])
    );
  end

  // ---- frame control -------------------------------------------------------
  typedef enum logic [1:0] {
    C_UPDATE,
    C_PLAYER,
    C_RENDER,
    C_READY
  } ctrl_t;
  ctrl_t ctrl;
  logic front;       // half of the column table being displayed
  logic valid;       // at least one complete table has been swapped in
  logic player_done, engine_start, engine_done;
  logic [15:0] drops;

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      ctrl <= C_UPDATE;
      front <= 1'b0;
      valid <= 1'b0;
      drops <= '0;
      engine_start <= 1'b0;
      dbg_swap_out <= 1'b0;
    end else begin
      engine_start <= 1'b0;
      dbg_swap_out <= 1'b0;
      case (ctrl)
        C_UPDATE: ctrl <= C_PLAYER;
        C_PLAYER: if (player_done) begin
          engine_start <= 1'b1;
          ctrl <= C_RENDER;
        end
        C_RENDER: begin
          if (nf) drops <= drops + 1'b1;
          else if (engine_done) ctrl <= C_READY;
        end
        C_READY: if (nf) begin
          front <= ~front;
          valid <= 1'b1;
          dbg_swap_out <= 1'b1;
          ctrl <= C_UPDATE;
        end
        default: ctrl <= C_UPDATE;
      endcase
    end
  end

  // ---- map ROM: port A for the ray engine, port B for the player -----------
  logic [9:0] engine_map_addr, player_map_addr;
  logic [3:0] engine_map_data, player_map_data;

  rom_2p #(
      .WIDTH(4),
      .DEPTH(1024),
      .INIT_FILE(MAP_FILE)
  ) map_rom (
      .clk_in(clk_in),
      .addra_in(engine_map_addr),
      .addrb_in(player_map_addr),
      .douta_out(engine_map_data),
      .doutb_out(player_map_data)
  );

  // ---- player --------------------------------------------------------------
  logic [18:0] pos_x, pos_y;
  logic [9:0] angle;
  logic signed [15:0] dir_x, dir_y, plane_x, plane_y;

  player #(
      .TRIG_FILE(TRIG_FILE)
  ) player_fsm (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .update_in(ctrl == C_UPDATE),
      .btn_in(btn_clean),
      .load_in(dbg_load_in),
      .load_x_in(dbg_x_in),
      .load_y_in(dbg_y_in),
      .load_angle_in(dbg_angle_in),
      .map_addr_out(player_map_addr),
      .map_data_in(player_map_data),
      .pos_x_out(pos_x),
      .pos_y_out(pos_y),
      .angle_out(angle),
      .dir_x_out(dir_x),
      .dir_y_out(dir_y),
      .plane_x_out(plane_x),
      .plane_y_out(plane_y),
      .done_out(player_done)
  );

  // ---- ray engine ------------------------------------------------------------
  logic col_we;
  logic [XW-1:0] col_x;
  logic [69:0] col_data;
  logic engine_busy;
  logic [23:0] engine_cycles;

  ray_engine #(
      .W(ACTIVE_H),
      .H(ACTIVE_V)
  ) engine (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .start_in(engine_start),
      .pos_x_in(pos_x),
      .pos_y_in(pos_y),
      .dir_x_in(dir_x),
      .dir_y_in(dir_y),
      .plane_x_in(plane_x),
      .plane_y_in(plane_y),
      .map_addr_out(engine_map_addr),
      .map_data_in(engine_map_data),
      .col_we_out(col_we),
      .col_x_out(col_x),
      .col_data_out(col_data),
      .busy_out(engine_busy),
      .done_out(engine_done),
      .cycles_out(engine_cycles)
  );

  // ---- column table: two halves of ACTIVE_H entries ------------------------
  logic [TAW-1:0] table_waddr, table_raddr;
  logic [69:0] table_rdata;
  assign table_waddr = front ? TAW'(col_x) : TAW'(col_x) + TAW'(ACTIVE_H);

  ram_sdp #(
      .WIDTH(70),
      .DEPTH(2 * ACTIVE_H)
  ) column_table (
      .clk_in(clk_in),
      .we_in(col_we),
      .waddr_in(table_waddr),
      .wdata_in(col_data),
      .raddr_in(table_raddr),
      .rdata_out(table_rdata)
  );

  // ---- pixels ----------------------------------------------------------------
  logic [23:0] rgb;
  logic de_p, hs_p, vs_p;

  pixel_pipeline #(
      .W(ACTIVE_H),
      .H(ACTIVE_V),
      .HW(HW),
      .VW(VW),
      .TEX_FILE(TEX_FILE),
      .PAL_FILE(PAL_FILE)
  ) pixels (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .hcount_in(hcount),
      .vcount_in(vcount),
      .ad_in(ad),
      .hs_in(hs),
      .vs_in(vs),
      .front_in(front),
      .valid_in(valid),
      .table_addr_out(table_raddr),
      .table_data_in(table_rdata),
      .rgb_out(rgb),
      .de_out(de_p),
      .hs_out(hs_p),
      .vs_out(vs_p)
  );

  // ---- TMDS (blue carries the syncs: C0 = hsync, C1 = vsync) ---------------
  tmds_encoder enc_red (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .data_in(rgb[23:16]),
      .control_in(2'b00),
      .ve_in(de_p),
      .tmds_out(tmds_red_out)
  );
  tmds_encoder enc_green (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .data_in(rgb[15:8]),
      .control_in(2'b00),
      .ve_in(de_p),
      .tmds_out(tmds_green_out)
  );
  tmds_encoder enc_blue (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .data_in(rgb[7:0]),
      .control_in({vs_p, hs_p}),
      .ve_in(de_p),
      .tmds_out(tmds_blue_out)
  );

  // ---- debug taps ------------------------------------------------------------
  assign dbg_rgb_out = rgb;
  assign dbg_de_out = de_p;
  assign dbg_hs_out = hs_p;
  assign dbg_vs_out = vs_p;
  assign dbg_col_we_out = col_we;
  assign dbg_col_x_out = col_x;
  assign dbg_col_data_out = col_data;
  assign dbg_engine_start_out = engine_start;
  assign dbg_engine_done_out = engine_done;
  assign dbg_pos_x_out = pos_x;
  assign dbg_pos_y_out = pos_y;
  assign dbg_angle_out = angle;
  assign dbg_engine_cycles_out = engine_cycles;
  assign dbg_drops_out = drops;

  logic unused;
  assign unused = ^{fc, engine_busy};
endmodule

`default_nettype wire
