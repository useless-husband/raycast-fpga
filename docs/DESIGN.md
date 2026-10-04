# Design

This document explains how raycast-fpga is put together, the problems that
shaped it, and the alternatives that were considered and rejected. The
6.205-style project report is [report.md](report.md); a beginner's walkthrough
in Traditional Chinese is [導讀.zh-TW.md](導讀.zh-TW.md).

![Block diagram](block_diagram.svg)

## 1. The constraint that decides everything: memory

The target is the Real Digital Urbana board (Spartan-7 XC7S50), which has
75 block RAMs of 36 Kbit: 2.7 Mbit of on-chip memory in total. A 1280x720
image is far bigger than that:

| Storage option | Bits | Share of the XC7S50's block RAM |
|---|---:|---:|
| One 720p frame, 24-bit colour | 22,118,400 | 800% |
| One 720p frame, 8-bit palette | 7,372,800 | 267% |
| Two 320x180 frames, 8-bit (what MazeCaster did) | 921,600 | 33% |
| **Two column tables, 1280 x 70 bit (this design)** | **179,200** | **6.5%** |

A raycaster does not need a framebuffer, because everything on screen column
`x` is determined by a handful of numbers about the single ray cast for that
column: where the wall starts and ends, which texture, which texture column,
and how dark. So the ray engine writes one 70-bit entry per screen column, and
the pixel pipeline recreates every pixel on the fly, in raster order, from
that entry plus the texture ROM. The output is full 1280x720 at 24-bit colour.

The table is double buffered: two halves of 1280 entries. The pixel pipeline
reads the *front* half while the ray engine fills the *back* half for the next
frame; the halves swap at the end of the visible part of a frame (`nf`, the
"new frame" pulse), during vertical blanking, so a swap can never tear the
image.

Total on-chip memory used: column table 179,200 + textures 262,144 + sine/cosine
16,384 + palette 6,144 + map 4,096 = 467,968 bits. After synthesis Yosys uses
13 of the 75 block-RAM sites (17%), mostly because the 70-bit table words and
the 32K-deep texture ROM do not pack perfectly; the palette and the map end up
in LUTs.

## 2. One clock

Everything inside `raycast_system` runs on the 74.25 MHz pixel clock. The
board's MMCM turns the 100 MHz oscillator into 74.25 MHz and the 371.25 MHz
(5x) clock that the OSERDESE2 serialisers need for 10 bits per pixel at DDR.

Running the ray engine on its own faster clock was considered and rejected.
It would need clock-domain crossings for the player state, the start/done
handshake and the column-table writes (or a true dual-clock RAM), each a
source of subtle bugs, and the budget below shows the pixel clock is already
3.7x what the engine needs in the worst case.

## 3. Frame protocol

`frame_ctrl.sv` is a four-state machine:

```
UPDATE --> PLAYER --player_done--> RENDER --engine_done--> READY --nf--> (swap) UPDATE
```

* **UPDATE** pulses the player FSM once. Movement is therefore once per frame,
  which makes it deterministic and easy to model.
* **PLAYER** waits for the player (8 cycles) and starts the ray engine with the
  new position.
* **RENDER** waits for the engine. If `nf` arrives first, that frame is a
  drop: the old half is shown again and a counter increments (it drives
  LEDs 15:8 on the board). With the budget below this never happens at 720p.
* **READY** waits for `nf`, then swaps.

A first version of this logic lived inside `raycast_system` and had a
deadlock: if `engine_done` arrived in the very cycle of `nf`, the `nf` branch
won, the done pulse was forgotten, and the controller waited forever. A design
review found it; the controller is now its own module with a test that sweeps
the engine's finishing time across the frame boundary (the old logic fails at
latency 55 of the sweep).

Input-to-photon latency is one to two frames, the usual price of double
buffering.

## 4. Fixed-point formats

All arithmetic is integer. Every value has a fixed binary point:

| Quantity | Format | Bits | Range | Why |
|---|---|---|---|---|
| angle | unsigned integer | 10 | 0..1023 = one turn | 90 degrees is exactly 256, so axis-aligned views are exact |
| cos, sin | Q1.14 signed | 16 | -1.0..+1.0 | 1.0 = 16384 is representable; fits a 16-bit word and the 18-bit DSP48 port |
| dir, plane, ray, camX | Q1.14 signed | 16 (ray: 18) | about -1.66..+1.66 | ray = dir + plane * camX, with abs(plane) = 0.66 |
| position | Q5.14 unsigned | 19 | 0..32 cells | 5 integer bits for a 32x32 map; same 14 fraction bits as everything else |
| delta, side, perp distance | Q.14 unsigned | 29-31 | see below | sums need no alignment shifts |
| lh_half | integer | 15 | 0..32767 | half the wall height in pixels, saturating |
| tex step | Q7.16 unsigned | 23 | 0..64 | texels per screen row |

Using 14 fraction bits everywhere means adds and compares never need shifts;
a product of two Q.14 values is shifted right by 14 once. The resolution is
1/16384 of a cell, far below a pixel at any distance the map allows.

Products are truncated with an arithmetic shift (`>>>`), which is floor
division by a power of two. The golden model uses Python's `>>`, which is
also floor for negative numbers, so the two agree exactly.

### Widths and the overflow argument

`delta_x = 1 / |ray_x|` is computed as `2^28 / |ray_x|` in Q.14. It is at
most `2^28` (when `|ray_x|` is one LSB), so it needs 29 bits. When `ray_x`
is exactly zero the ray never crosses a vertical grid line; the engine then
sets `delta_x` and `side_x` to `DELTA_INF = 2^30 - 1` instead of dividing.

The DDA always increments the *smaller* side distance, so the value being
incremented is at most the final hit distance. Because `dir` has length 1 and
`plane` is perpendicular to it, `|ray| >= 1`, so the hit distance in a 32x32
map is at most about 45 cells (under `2^20` in Q.14). After one more
increment a side distance is below `2^20 + 2^28`, and `DELTA_INF` is never
incremented. 31-bit registers therefore cannot overflow. This argument relies
on the view coming from the sine/cosine table (unit `dir`, perpendicular
`plane`); the engine is not meant for arbitrary vectors.

An earlier draft saturated `delta` at `2^24`. That is wrong in a subtle way:
`side = frac * delta` with `frac` one LSB then becomes a tiny distance even
for a ray parallel to the grid line, and the DDA would step across a line it
never actually crosses. Keeping the exact `2^28` bound and a separate
"infinite" value avoids it.

### Only the bits you need

Two products are kept modulo a power of two because only their low bits
matter:

* The hit point along the wall, `pos + perp * ray`, only matters through its
  fractional part (which texture column). Bits 27:14 of the product depend
  only on the low 28 bits of the operands, so the multiplier is 28 x 28 bits
  modulo `2^28`.
* The texture row, `((y - H/2 + lh_half) * step) >> 16`, only needs 6 bits,
  so the pixel pipeline keeps the product modulo `2^22`.

The first of these produced the project's first real bug: the ray component
fed to that multiplier was declared unsigned, so negative rays were
zero-extended instead of sign-extended and textures slid along some walls by a
few texels. The ray-engine unit test's edge cases caught it on the first run.

## 5. The ray engine

`ray_engine.sv` is a multi-cycle state machine that processes one column at a
time. Per column:

1. `camX = ((2x - W) * floor(2^30 / W)) >>> 16`, the column's position on the
   camera plane, -1 at the left edge, 0 at the centre column `x = W/2`.
2. `ray = dir + (plane * camX) >>> 14`.
3. `delta_x = 2^28 / |ray_x|`, `delta_y = 2^28 / |ray_y|` (divider), or
   `DELTA_INF` for a zero component.
4. `side_x = frac * delta_x` where `frac` is the distance from the player to
   the first vertical grid line in the ray's direction; the same for y.
5. DDA: step the smaller of `side_x` / `side_y`, move one cell, read the map.
   Ties step y. A cell outside 0..31 counts as a wall, so the walk always
   terminates even with a broken map (and the asset generator rejects maps
   without a closed border anyway).
6. `perp = side - delta` of the last step.
7. `lh_half = min((H/2 << 14) / perp, 32767)`; `perp = 0` saturates.
8. `step = (64 << 16) / (2 * lh_half + 1)`.
9. `tex_x` = top 6 fraction bits of the hit point, mirrored on two of the four
   face orientations so every texture reads left to right.
10. `shade = min(7, 2 * side + perp / 4 cells)`.

### Fisheye correction

`perp` is the distance from the camera *plane*, not from the eye. Because the
ray is written as `dir + plane * camX` and `side_x` measures "how many ray
lengths until the next grid line", `side - delta` at the hit is the multiple
`t` of the ray at which the wall is hit. The hit point is `pos + t * ray`, and
its projection onto `dir` is `t * |dir|^2 + t * camX * (plane . dir) = t`,
because `|dir| = 1` and `plane` is perpendicular to `dir`. So the DDA yields the
perpendicular distance directly and walls come out straight without any
cosine correction.

### The divider

Each column needs four divisions. `divider.sv` is a radix-2 restoring divider:
one quotient bit per cycle, 32 cycles for 32 bits, 34 cycles including the
launch and result cycles. It costs about 150 LUTs. A combinational 32-bit
divider would be a chain of 32 subtract-and-select stages, far too slow for
74 MHz and several times larger. A Newton-Raphson reciprocal (table seed plus
two multiply iterations) would take about 6 cycles but needs extra DSPs and a
correction step to be exactly floor-rounded; it is not needed because the
budget below has a 3.7x margin. Exact floor division also makes the golden
model trivial (`//`).

### Texture coordinates without clamping

The wall occupies rows `H/2 - lh_half .. H/2 + lh_half`, which is
`span = 2 * lh_half + 1` pixels. With `step = floor(64 * 2^16 / span)`,
`(y - top) * step < span * 64 * 2^16 / span = 64 * 2^16`, so the texture row
is always in 0..63 and needs no clamp. When the wall is taller than the screen
the visible rows are simply the middle of that range.

### Cycle budget

The number of cycles per column follows directly from the state machine
(`column_cycles()` in `model/raycast_model.py`):

```
cycles = 2 (camX, ray) + 34 + 34 (two deltas; 1 each for a zero component)
       + 1 (side) + 2 * steps (DDA) + 1 (perp) + 34 (lh; 1 if perp = 0)
       + 34 (step) + 1 (write)
       = 141 + 2 * steps
```

The tests check this formula against the engine's own cycle counter for every
frame they render, so it is not an estimate.

The longest possible DDA walk in a closed 32x32 map is 59 steps (30 in one
axis and 29 in the other before reaching the border);
`tests/model/test_model.py` checks that random rays reach and never exceed
it. Hence:

| | Cycles | Share of a 1650 x 750 = 1,237,500-cycle frame |
|---|---:|---:|
| Worst column (59 steps) | 259 | |
| Worst frame, 1280 columns | 331,520 | 26.8% |
| Worst frame measured in the tests | 215,098 | 17.4% |
| Typical frame (walk through the level) | ~198,000 | ~16% |

The engine is idle more than 70% of the time even in the worst case. That
margin is what a future version could spend on floor casting, sprites or a
second engine at a higher resolution.

## 6. The pixel pipeline

Five register stages turn `(hcount, vcount)` into a pixel:

| Stage | Work |
|---|---|
| 0 | column-table address = front-half base + hcount |
| 1 | table entry arrives; wall test `top <= y <= bottom`; `dy = y - H/2 + lh_half` |
| 2 | `dy * step` registered; texture address `{tex_id, ty, tex_x}` |
| 3 | palette index arrives from the texture ROM |
| 4 | RGB arrives from the palette ROM; background colour (row gradient) ready |
| 5 | shade (`c * (8 - shade) / 8` per channel) and select; output register |

`hsync`, `vsync` and data-enable go through the same five registers, so they
stay aligned with the pixel they describe. The test for this module compares
each output with the timing generator's signals five cycles earlier.

Ceiling and floor are flat colours darkened towards the horizon in eight
steps, a cheap depth cue (floor casting is future work).

## 7. HDMI output

`tmds_encoder.sv` implements DVI 1.0 section 3.3.3: transition minimisation
(XOR or XNOR chain), then DC balancing against a running disparity counter,
and the four control tokens during blanking. Blue carries `{vsync, hsync}`.
The running disparity of a valid stream is always an even number in -8..+8,
which the unit test proves by breadth-first search over the reference
encoder's states; 5 signed bits are enough.

On the board, `tmds_serializer.sv` cascades two OSERDESE2 primitives (master
and slave) for 10:1 DDR serialisation at 742.5 Mbit/s per lane, and the clock
lane sends the constant pattern `0000011111` so the sink sees one clock edge
pair per pixel.

## 8. Player and collisions

Each frame the player turns (4/1024 of a turn while a turn button is held),
then moves along x, then along y. A move is accepted if a probe point a
quarter cell ahead (in the direction of motion) is in an empty cell. Testing
the axes separately is what lets the player slide along a wall instead of
stopping dead. Because the speed (1/16 cell) plus the probe distance (1/4) is
less than one cell, the player can never end up inside a wall; the model
tests check this over 20,000 random button presses, and the RTL tests compare
every update with the model.

Buttons are bounce-filtered (5 ms) after a two-flop synchroniser.

## 9. Verification strategy

```
            maps/level1.txt --tools/gen_assets.py--> rtl/mem/*.hex
                                                       |          |
                                         model/raycast_model.py   RTL ($readmemh)
                                                       |          |
             tests/model  (model properties, budget, asset reproducibility)
             tests/unit   (cocotb + Icarus, one module at a time, vs. model or reference)
             tests/system (Verilator, whole design, vs. model, bit for bit)
```

* **One golden model** (`model/raycast_model.py`) describes every value the
  hardware computes, in the same order with the same widths and rounding. It
  reads the same `.hex` files as the RTL.
* **Unit tests** (cocotb on Icarus) check each module the way 6.205 labs do:
  timing positions, TMDS against an independent reference written from the
  spec, the divider against `//`, the debouncer against a cycle model, the
  player and ray engine against the golden model on random maps, the pixel
  pipeline against the golden model, the frame controller against a reference
  state machine.
* **System tests** build the whole design with Verilator. The C++ harness
  decodes the three TMDS lanes the way a monitor would, rebuilds frames only
  from those symbols, and checks on every clock that each symbol decodes to
  the pixel (or sync) that entered the encoders. Python then recomputes the
  player states, all column tables and the frames, and requires exact
  equality.
* Randomised tests use fixed seeds (`TEST_SEED`, default 6205) printed in
  every failure message.

## 10. Alternatives considered

| Idea | Why not |
|---|---|
| Framebuffer in block RAM | Does not fit at 720p (section 1); a quarter-resolution buffer costs 4x the memory of the column table and still looks blocky. |
| Framebuffer in the board's DDR3 | Needs a memory controller and arbitration with scanout; large effort for no visual gain here. |
| Floating point | Large, slow on a Spartan-7, and makes bit-exact checking against a model much harder. |
| CORDIC for sin/cos | A 1024-entry table is half a block RAM and exact. |
| Several ray engines in parallel | One engine already uses under 27% of the frame in the worst case. |
| Newton-Raphson reciprocal | Faster but unnecessary (see the budget) and harder to make exactly floor-rounded. |
| Separate fast clock for the engine | Clock-domain crossings for no benefit (section 2). |
| Per-column texture accumulators instead of a multiply per pixel | Needs a read-modify-write RAM per pixel; one DSP slice is cheaper. |

## 11. Known limitations

* Never run on a real board. The pin file comes from Real Digital's published
  constraints, but nothing has been placed, routed or timed: open-source tools
  give no timing sign-off for 7-series parts.
* The map is fixed at 32x32 and is baked in at build time.
* No floor/ceiling textures, sprites, doors or a minimap.
* Yosys maps the six 8x4-bit shade multiplications to DSP48E1 slices; a
  shift-and-add form would move them to about 150 LUTs. There is no DSP
  pressure (19 of 120), so it was left alone.
