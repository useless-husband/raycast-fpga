`timescale 1ns / 1ps
`default_nettype none

// Ray engine: casts one ray per screen column with a DDA grid walk and writes
// one 70-bit entry per column into the column table.
//
// For column x (all values fixed point, see docs/DESIGN.md):
//   camX   = (2x - W) / W                          Q1.14, -1 .. +1
//   ray    = dir + plane * camX                    Q1.14
//   delta  = 1 / |ray|  per axis (divider)          Q.14, "infinite" if ray = 0
//   side   = distance to the first x / y grid line Q.14
//   DDA: step whichever of side_x / side_y is smaller until a wall cell
//   perp   = side - delta of the last step         = distance along `dir`
//            (this is the fisheye correction: it is the distance to the
//             camera plane, not to the eye, because |dir| = 1 and
//             plane is perpendicular to dir)
//   lh_half = (H/2) / perp                         wall half height in pixels
//   step    = 64 / (2 * lh_half + 1)                texels per screen row
//   tex_x   = 6 MSBs of the hit point's fractional coordinate
//
// One shared sequential divider performs the four divisions per column.
// A frame takes ~200k cycles at 1280 columns, against 1.2375M cycles per
// 720p frame (see docs/DESIGN.md "Cycle budget").
//
// Column table entry, MSB first (70 bits):
//   draw_start[10] draw_end[10] tex_id[3] tex_x[6] shade[3] lh_half[15] step[23]
//
// Preconditions: dir has unit length and plane is perpendicular to it (as
// player.sv produces them from the sine table); the register widths rely on
// it (overflow argument in docs/DESIGN.md section 4).  H <= 1023 because
// draw_start/draw_end are 10 bits.
//
// Golden reference: model/raycast_model.py: cast_column().
module ray_engine #(
    parameter int W = 1280,
    parameter int H = 720,
    parameter int XW = $clog2(W)
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
    output logic [9:0] map_addr_out,     // {y[4:0], x[4:0]}
    input wire [3:0] map_data_in,        // 1 cycle after map_addr_out
    output logic col_we_out,
    output logic [XW-1:0] col_x_out,
    output logic [69:0] col_data_out,
    output logic busy_out,
    output logic done_out,
    output logic [23:0] cycles_out       // length of the last completed frame
);
  localparam int HALF = H / 2;
  localparam logic [43:0] CAMX_MUL = 44'((64'd1 << 30) / 64'(W));
  localparam logic [31:0] DELTA_NUM = 32'd1 << 28;
  localparam logic [31:0] LH_NUM = 32'(HALF) << 14;
  localparam logic [31:0] STEP_NUM = 32'd64 << 16;
  localparam logic [29:0] DELTA_INF = 30'h3FFF_FFFF;
  localparam logic [14:0] LHH_MAX = 15'h7FFF;
  localparam logic [XW-1:0] X_LAST = XW'(W - 1);
  localparam logic [9:0] HALF10 = 10'(HALF);
  localparam logic [9:0] H_LAST10 = 10'(H - 1);

  typedef enum logic [3:0] {
    IDLE,
    CAMX,
    RAYDIR,
    DX,
    DX_WAIT,
    DY,
    DY_WAIT,
    SIDE,
    DDA,
    DDA_CHECK,
    PERP,
    LH,
    LH_WAIT,
    STEP,
    STEP_WAIT,
    WRITE
  } state_t;
  state_t state;

  // ---- latched view -------------------------------------------------------
  logic [18:0] px, py;
  logic signed [15:0] dir_x, dir_y, plane_x, plane_y;

  // ---- per-column registers ------------------------------------------------
  logic [XW-1:0] x;
  logic signed [15:0] camx;
  logic signed [17:0] rdx, rdy;
  logic [29:0] delta_x, delta_y;
  logic [30:0] side_x, side_y;
  logic [5:0] map_x, map_y;       // one spare bit: 32..63 means "left the map"
  logic side;                     // 0: hit a wall face crossing x, 1: crossing y
  logic oob;
  logic [3:0] hit_cell;
  logic [30:0] perp;
  logic [14:0] lh_half;
  logic [22:0] step;
  logic [5:0] tex_x;
  logic [2:0] shade;
  logic [23:0] cycles;

  // ---- shared divider ------------------------------------------------------
  logic div_start;
  logic [31:0] div_num, div_den, div_q, div_r;
  logic div_valid, div_err, div_busy;

  divider #(.WIDTH(32)) div (
      .clk_in(clk_in),
      .rst_in(rst_in),
      .dividend_in(div_num),
      .divisor_in(div_den),
      .data_valid_in(div_start),
      .quotient_out(div_q),
      .remainder_out(div_r),
      .data_valid_out(div_valid),
      .error_out(div_err),
      .busy_out(div_busy)
  );

  // ---- combinational datapath ---------------------------------------------
  logic signed [43:0] two_x_minus_w, camx_full;
  logic flip;
  logic signed [31:0] pcx, pcy;
  logic [16:0] abs_rdx, abs_rdy;
  logic [14:0] frac_x_dist, frac_y_dist;   // distance to the first grid line, Q0.14 (<= 1.0)
  logic [44:0] side_x_full, side_y_full;
  logic [5:0] next_map_x, next_map_y;
  logic step_on_x;
  logic [15:0] span;
  logic signed [17:0] rd_wall;
  logic [13:0] pos_wall;
  logic [27:0] wall_prod;
  logic [13:0] wall_frac;
  logic [15:0] far;
  logic [15:0] shade_sum;

  always_comb begin
    two_x_minus_w = $signed(44'({x, 1'b0})) - $signed(44'(W));
    camx_full = two_x_minus_w * $signed(CAMX_MUL);

    pcx = 32'(plane_x) * 32'(camx);
    pcy = 32'(plane_y) * 32'(camx);

    abs_rdx = rdx[17] ? 17'(-rdx) : 17'(rdx);
    abs_rdy = rdy[17] ? 17'(-rdy) : 17'(rdy);

    // rd < 0: distance back to the cell's left/top edge = frac
    // rd > 0: distance forward to the right/bottom edge = 1 - frac
    frac_x_dist = rdx[17] ? {1'b0, px[13:0]} : 15'd16384 - {1'b0, px[13:0]};
    frac_y_dist = rdy[17] ? {1'b0, py[13:0]} : 15'd16384 - {1'b0, py[13:0]};
    side_x_full = 45'(frac_x_dist) * 45'(delta_x);
    side_y_full = 45'(frac_y_dist) * 45'(delta_y);

    step_on_x = side_x < side_y;    // ties step y, like the reference
    next_map_x = rdx[17] ? map_x - 6'd1 : map_x + 6'd1;
    next_map_y = rdy[17] ? map_y - 6'd1 : map_y + 6'd1;

    span = {lh_half, 1'b1};          // 2 * lh_half + 1

    // Only the fractional part of the hit point is needed, so the product is
    // kept modulo 2^28 (bits 27:14 are the fraction after the >>> 14).
    rd_wall = side ? rdx : rdy;
    pos_wall = side ? px[13:0] : py[13:0];
    wall_prod = 28'(perp) * 28'(rd_wall);
    wall_frac = pos_wall + wall_prod[27:14];

    flip = (!side && rdx[17]) || (side && !rdy[17] && (rdy != 0));

    far = 16'(perp >> 16);           // +1 shade level every 4 cells
    shade_sum = far + {14'd0, side, 1'b0};
  end

  // Lint ignores signals named unused*: these bits are
  // intentionally dropped (low product bits, divider status we never need).
  logic unused;
  assign unused = ^{div_r, div_err, div_busy, wall_prod[13:0], wall_frac[7:0]};

  // the map ROM sees the address of the cell we are about to step into
  always_comb begin
    if (state == DDA) begin
      map_addr_out = step_on_x ? {map_y[4:0], next_map_x[4:0]} : {next_map_y[4:0], map_x[4:0]};
    end else begin
      map_addr_out = 10'd0;
    end
  end

  // divider operands for the state that launches a division
  always_comb begin
    div_start = 1'b0;
    div_num = DELTA_NUM;
    div_den = 32'd1;
    case (state)
      DX: begin
        div_start = (rdx != 0);
        div_den = {15'd0, abs_rdx};
      end
      DY: begin
        div_start = (rdy != 0);
        div_den = {15'd0, abs_rdy};
      end
      LH: begin
        div_start = (perp != 0);
        div_num = LH_NUM;
        div_den = {1'b0, perp};
      end
      STEP: begin
        div_start = 1'b1;
        div_num = STEP_NUM;
        div_den = {16'd0, span};
      end
      default: ;
    endcase
  end

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      state <= IDLE;
      busy_out <= 1'b0;
      done_out <= 1'b0;
      col_we_out <= 1'b0;
      col_x_out <= '0;
      col_data_out <= '0;
      cycles_out <= '0;
      cycles <= '0;
      x <= '0;
      px <= '0;
      py <= '0;
      dir_x <= '0;
      dir_y <= '0;
      plane_x <= '0;
      plane_y <= '0;
      camx <= '0;
      rdx <= '0;
      rdy <= '0;
      delta_x <= '0;
      delta_y <= '0;
      side_x <= '0;
      side_y <= '0;
      map_x <= '0;
      map_y <= '0;
      side <= 1'b0;
      oob <= 1'b0;
      hit_cell <= '0;
      perp <= '0;
      lh_half <= '0;
      step <= '0;
      tex_x <= '0;
      shade <= '0;
    end else begin
      done_out <= 1'b0;
      col_we_out <= 1'b0;
      if (busy_out) cycles <= cycles + 1'b1;

      case (state)
        IDLE: begin
          if (start_in) begin
            px <= pos_x_in;
            py <= pos_y_in;
            dir_x <= dir_x_in;
            dir_y <= dir_y_in;
            plane_x <= plane_x_in;
            plane_y <= plane_y_in;
            x <= '0;
            busy_out <= 1'b1;
            cycles <= 24'd1;
            state <= CAMX;
          end
        end

        CAMX: begin
          camx <= 16'(camx_full >>> 16);
          state <= RAYDIR;
        end

        RAYDIR: begin
          rdx <= 18'(dir_x) + 18'(pcx >>> 14);
          rdy <= 18'(dir_y) + 18'(pcy >>> 14);
          map_x <= {1'b0, px[18:14]};
          map_y <= {1'b0, py[18:14]};
          state <= DX;
        end

        DX: begin
          if (rdx == 0) begin
            delta_x <= DELTA_INF;
            state <= DY;
          end else begin
            state <= DX_WAIT;
          end
        end
        DX_WAIT: if (div_valid) begin
          delta_x <= div_q[29:0];
          state <= DY;
        end

        DY: begin
          if (rdy == 0) begin
            delta_y <= DELTA_INF;
            state <= SIDE;
          end else begin
            state <= DY_WAIT;
          end
        end
        DY_WAIT: if (div_valid) begin
          delta_y <= div_q[29:0];
          state <= SIDE;
        end

        SIDE: begin
          side_x <= (rdx == 0) ? {1'b0, DELTA_INF} : 31'(side_x_full >> 14);
          side_y <= (rdy == 0) ? {1'b0, DELTA_INF} : 31'(side_y_full >> 14);
          state <= DDA;
        end

        // one grid step; the ROM is reading the new cell during this cycle
        DDA: begin
          if (step_on_x) begin
            side_x <= side_x + {1'b0, delta_x};
            map_x <= next_map_x;
            side <= 1'b0;
            oob <= next_map_x[5];
          end else begin
            side_y <= side_y + {1'b0, delta_y};
            map_y <= next_map_y;
            side <= 1'b1;
            oob <= next_map_y[5];
          end
          state <= DDA_CHECK;
        end

        DDA_CHECK: begin
          if (oob || (map_data_in != 4'd0)) begin
            hit_cell <= oob ? 4'd1 : map_data_in;
            state <= PERP;
          end else begin
            state <= DDA;
          end
        end

        PERP: begin
          perp <= side ? side_y - {1'b0, delta_y} : side_x - {1'b0, delta_x};
          state <= LH;
        end

        LH: begin
          // mirror so textures read left-to-right on every face
          tex_x <= flip ? ~wall_frac[13:8] : wall_frac[13:8];
          shade <= (shade_sum > 16'd7) ? 3'd7 : shade_sum[2:0];
          if (perp == 0) begin
            lh_half <= LHH_MAX;
            state <= STEP;
          end else begin
            state <= LH_WAIT;
          end
        end
        LH_WAIT: if (div_valid) begin
          lh_half <= (div_q > {17'd0, LHH_MAX}) ? LHH_MAX : div_q[14:0];
          state <= STEP;
        end

        STEP: state <= STEP_WAIT;
        STEP_WAIT: if (div_valid) begin
          step <= div_q[22:0];
          state <= WRITE;
        end

        WRITE: begin
          col_we_out <= 1'b1;
          col_x_out <= x;
          col_data_out <= {
            ({1'b0, lh_half} >= {6'd0, HALF10}) ? 10'd0 : HALF10 - lh_half[9:0],
            ({1'b0, lh_half} + {6'd0, HALF10} >= {6'd0, H_LAST10}) ? H_LAST10 : HALF10 + lh_half[9:0],
            3'(hit_cell - 4'd1),
            tex_x,
            shade,
            lh_half,
            step
          };
          if (x == X_LAST) begin
            state <= IDLE;
            busy_out <= 1'b0;
            done_out <= 1'b1;
            cycles_out <= cycles;
          end else begin
            x <= x + 1'b1;
            state <= CAMX;
          end
        end

        default: state <= IDLE;
      endcase
    end
  end
endmodule

`default_nettype wire
