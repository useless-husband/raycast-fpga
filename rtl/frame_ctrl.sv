`timescale 1ns / 1ps
`default_nettype none

// Frame controller for the double-buffered column table.
//
//   UPDATE  one-cycle pulse to the player FSM (update_out)
//   PLAYER  wait for player_done_in, then pulse engine_start_out
//   RENDER  wait for engine_done_in (the back half is complete)
//   READY   wait for nf_in, then swap halves (front_out toggles, swap_out
//           pulses, valid_out goes high for good) and start over
//
// Every nf_in that arrives before the back half is complete is a dropped
// frame: the old half is shown again and drops_out counts it.  An
// engine_done_in in the same cycle as nf_in still counts as late (that nf
// has passed), but it is never lost; the swap happens at the next nf.
module frame_ctrl (
    input wire clk_in,
    input wire rst_in,
    input wire nf_in,
    input wire player_done_in,
    input wire engine_done_in,
    output logic update_out,
    output logic engine_start_out,
    output logic front_out,
    output logic valid_out,
    output logic swap_out,
    output logic [15:0] drops_out
);
  typedef enum logic [1:0] {
    UPDATE,
    PLAYER,
    RENDER,
    READY
  } state_t;
  state_t state;

  assign update_out = (state == UPDATE);

  always_ff @(posedge clk_in) begin
    if (rst_in) begin
      state <= UPDATE;
      front_out <= 1'b0;
      valid_out <= 1'b0;
      drops_out <= '0;
      engine_start_out <= 1'b0;
      swap_out <= 1'b0;
    end else begin
      engine_start_out <= 1'b0;
      swap_out <= 1'b0;
      case (state)
        UPDATE: state <= PLAYER;
        PLAYER: if (player_done_in) begin
          engine_start_out <= 1'b1;
          state <= RENDER;
        end
        RENDER: begin
          if (nf_in) drops_out <= drops_out + 1'b1;
          if (engine_done_in) state <= READY;
        end
        READY: if (nf_in) begin
          front_out <= ~front_out;
          valid_out <= 1'b1;
          swap_out <= 1'b1;
          state <= UPDATE;
        end
        default: state <= UPDATE;
      endcase
    end
  end
endmodule

`default_nettype wire
