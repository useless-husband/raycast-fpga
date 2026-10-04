"""Bit-exact golden model of the raycast-fpga hardware.

Everything here is integer arithmetic that mirrors the RTL operation for
operation: the same widths, the same floor-rounding shifts (Python's ``>>`` on
negative integers is an arithmetic shift, exactly like SystemVerilog ``>>>``),
the same saturation points and the same tie-breaking.  If the hardware and
this file disagree on a single bit, a test fails.

Fixed-point formats (see docs/DESIGN.md for the reasoning):

  angle          10-bit unsigned, 1024 steps per revolution
  cos/sin        Q1.14 signed   (16384 == 1.0)
  dir, plane,    Q1.14 signed
  cameraX, ray
  position       Q5.14 unsigned (19 bits; 32x32 map)
  delta/side/    Q.14 unsigned  (31-bit registers; see the bound in DESIGN.md)
  perp distance
  lh_half        integer, saturates at 2^15-1
  tex step       Q7.16 unsigned (texels per screen row)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ---------------------------------------------------------------- constants
FRAC = 14
ONE = 1 << FRAC
MAP_BITS = 5
MAP_N = 1 << MAP_BITS
TEX_BITS = 6
TEX_N = 1 << TEX_BITS
NUM_TEX = 8
ANGLE_BITS = 10
ANGLES = 1 << ANGLE_BITS

PLANE_K = 10813            # round(0.66 * 2^14): tan(FOV/2) for a ~66 degree FOV
SPEED = 1024               # 0.0625 cells per frame
TURN = 4                   # angle units per frame (1.40625 degrees)
RADIUS = 4096              # 0.25 cell collision probe

DELTA_INF = (1 << 30) - 1  # "infinite" delta/side for a ray parallel to an axis
LHH_MAX = (1 << 15) - 1    # saturation for half of the wall line height
STEP_NUM = TEX_N << 16     # tex step = STEP_NUM / span, Q7.16

CEIL_RGB = (0x38, 0x40, 0x58)
FLOOR_RGB = (0x58, 0x4C, 0x40)

DEFAULT_MEM = os.path.join(os.path.dirname(__file__), "..", "rtl", "mem")


@dataclass(frozen=True)
class Video:
    """Active resolution; the porches do not influence any pixel value."""
    w: int = 1280
    h: int = 720

    @property
    def camx_mul(self) -> int:
        return (1 << 30) // self.w

    @property
    def row_mul(self) -> int:
        return (8 << 16) // (self.h // 2)


# ------------------------------------------------------------------- memories
def _read_hex(path: str) -> list[int]:
    with open(path, encoding="ascii") as f:
        return [int(line.split("//")[0], 16) for line in f if line.split("//")[0].strip()]


class Assets:
    """The same .hex files the RTL loads with $readmemh."""

    def __init__(self, mem_dir: str = DEFAULT_MEM):
        self.map = _read_hex(os.path.join(mem_dir, "map.hex"))
        self.tex = _read_hex(os.path.join(mem_dir, "tex.hex"))
        self.palette = _read_hex(os.path.join(mem_dir, "palette.hex"))
        trig = _read_hex(os.path.join(mem_dir, "trig.hex"))
        self.cos = [v - 0x10000 if v & 0x8000 else v for v in trig]
        assert len(self.map) == MAP_N * MAP_N
        assert len(self.tex) == NUM_TEX * TEX_N * TEX_N
        assert len(self.palette) == 256 and len(self.cos) == ANGLES

    def cell(self, mx: int, my: int) -> int:
        """Map lookup; anything outside the 32x32 grid counts as wall 1."""
        if not (0 <= mx < MAP_N and 0 <= my < MAP_N):
            return 1
        return self.map[(my << MAP_BITS) | mx]

    def sin(self, angle: int) -> int:
        return self.cos[(angle - ANGLES // 4) & (ANGLES - 1)]


# --------------------------------------------------------------------- player
@dataclass(frozen=True)
class Player:
    x: int        # Q5.14
    y: int        # Q5.14
    angle: int    # 10-bit


@dataclass(frozen=True)
class View:
    """What the ray engine latches at the start of a frame."""
    x: int
    y: int
    dir_x: int
    dir_y: int
    plane_x: int
    plane_y: int


@dataclass(frozen=True)
class Buttons:
    fwd: int = 0
    back: int = 0
    left: int = 0
    right: int = 0
    strafe_l: int = 0
    strafe_r: int = 0

    @staticmethod
    def from_bits(b: int) -> "Buttons":
        """Bit order matches the RTL btn[5:0] port: {sr, sl, right, left, back, fwd}."""
        return Buttons(b & 1, (b >> 1) & 1, (b >> 2) & 1, (b >> 3) & 1, (b >> 4) & 1, (b >> 5) & 1)


def view_of(a: Assets, p: Player) -> View:
    c = a.cos[p.angle]
    s = a.sin(p.angle)
    return View(p.x, p.y, c, s, (-s * PLANE_K) >> FRAC, (c * PLANE_K) >> FRAC)


def player_update(a: Assets, p: Player, b: Buttons) -> Player:
    """One frame of movement: turn, then move x, then move y (wall sliding)."""
    angle = (p.angle + TURN * (b.right - b.left)) & (ANGLES - 1)
    c = a.cos[angle]
    s = a.sin(angle)
    f = b.fwd - b.back
    t = b.strafe_r - b.strafe_l
    mx = f * c - t * s           # forward * (c, s) + strafe * (-s, c)
    my = f * s + t * c
    dx = (mx * SPEED) >> FRAC
    dy = (my * SPEED) >> FRAC
    x, y = p.x, p.y
    if dx != 0:
        nx = x + dx
        probe = nx + (RADIUS if dx > 0 else -RADIUS)
        if 0 <= probe < (MAP_N << FRAC) and a.cell(probe >> FRAC, y >> FRAC) == 0:
            x = nx
    if dy != 0:
        ny = y + dy
        probe = ny + (RADIUS if dy > 0 else -RADIUS)
        if 0 <= probe < (MAP_N << FRAC) and a.cell(x >> FRAC, probe >> FRAC) == 0:
            y = ny
    return Player(x, y, angle)


# --------------------------------------------------------------- ray engine
@dataclass(frozen=True)
class Column:
    draw_start: int
    draw_end: int
    tex_id: int
    tex_x: int
    shade: int
    lh_half: int
    step: int

    def pack(self) -> int:
        """70-bit column-table word, MSB first, identical to the RTL layout."""
        v = self.draw_start
        v = (v << 10) | self.draw_end
        v = (v << 3) | self.tex_id
        v = (v << 6) | self.tex_x
        v = (v << 3) | self.shade
        v = (v << 15) | self.lh_half
        v = (v << 23) | self.step
        return v

    @staticmethod
    def unpack(v: int) -> "Column":
        step = v & ((1 << 23) - 1); v >>= 23
        lh = v & ((1 << 15) - 1); v >>= 15
        shade = v & 7; v >>= 3
        tx = v & 63; v >>= 6
        tid = v & 7; v >>= 3
        de = v & 1023; v >>= 10
        ds = v & 1023
        return Column(ds, de, tid, tx, shade, lh, step)


@dataclass(frozen=True)
class Trace:
    """Internal values of one ray, handy when a test needs to explain a mismatch."""
    camx: int
    rdx: int
    rdy: int
    delta_x: int
    delta_y: int
    steps: int
    side: int
    map_x: int
    map_y: int
    perp: int


def _delta(rd: int) -> int:
    return DELTA_INF if rd == 0 else (1 << 28) // abs(rd)


def cast_column(a: Assets, vid: Video, v: View, x: int) -> tuple[Column, Trace]:
    camx = ((2 * x - vid.w) * vid.camx_mul) >> 16
    rdx = v.dir_x + ((v.plane_x * camx) >> FRAC)
    rdy = v.dir_y + ((v.plane_y * camx) >> FRAC)
    delta_x = _delta(rdx)
    delta_y = _delta(rdy)

    map_x = v.x >> FRAC
    map_y = v.y >> FRAC
    frac_x = v.x & (ONE - 1)
    frac_y = v.y & (ONE - 1)
    step_x = -1 if rdx < 0 else 1
    step_y = -1 if rdy < 0 else 1
    if rdx == 0:
        side_x = DELTA_INF
    elif rdx < 0:
        side_x = (frac_x * delta_x) >> FRAC
    else:
        side_x = ((ONE - frac_x) * delta_x) >> FRAC
    if rdy == 0:
        side_y = DELTA_INF
    elif rdy < 0:
        side_y = (frac_y * delta_y) >> FRAC
    else:
        side_y = ((ONE - frac_y) * delta_y) >> FRAC

    steps = 0
    while True:
        if side_x < side_y:
            side_x += delta_x
            map_x += step_x
            side = 0
        else:
            side_y += delta_y
            map_y += step_y
            side = 1
        steps += 1
        cell = a.cell(map_x, map_y)
        if cell != 0:
            break

    perp = side_x - delta_x if side == 0 else side_y - delta_y
    half = vid.h // 2
    lh_half = LHH_MAX if perp == 0 else min((half << FRAC) // perp, LHH_MAX)
    span = 2 * lh_half + 1
    step = STEP_NUM // span

    # Only the fractional bits of the hit point matter, so the hardware keeps
    # the product modulo 2^28; Python's floor shift gives the same low bits.
    if side == 0:
        wall = v.y + ((perp * rdy) >> FRAC)
    else:
        wall = v.x + ((perp * rdx) >> FRAC)
    tex_x = (wall & (ONE - 1)) >> (FRAC - TEX_BITS)
    if (side == 0 and rdx < 0) or (side == 1 and rdy > 0):
        tex_x = TEX_N - 1 - tex_x

    col = Column(
        draw_start=max(0, half - lh_half),
        draw_end=min(vid.h - 1, half + lh_half),
        tex_id=(cell - 1) & 7,
        tex_x=tex_x,
        shade=min(7, 2 * side + (perp >> 16)),
        lh_half=lh_half,
        step=step,
    )
    return col, Trace(camx, rdx, rdy, delta_x, delta_y, steps, side, map_x, map_y, perp)


def cast_frame(a: Assets, vid: Video, v: View) -> list[Column]:
    return [cast_column(a, vid, v, x)[0] for x in range(vid.w)]


# ------------------------------------------------------------- pixel pipeline
def shade_rgb(rgb: int, s: int) -> int:
    k = 8 - s
    r = (((rgb >> 16) & 0xFF) * k) >> 3
    g = (((rgb >> 8) & 0xFF) * k) >> 3
    b = ((rgb & 0xFF) * k) >> 3
    return (r << 16) | (g << 8) | b


def background(vid: Video, y: int) -> int:
    half = vid.h // 2
    if y < half:
        d, base = half - 1 - y, CEIL_RGB
    else:
        d, base = y - half, FLOOR_RGB
    level = (d * vid.row_mul) >> 16
    rgb = (base[0] << 16) | (base[1] << 8) | base[2]
    return shade_rgb(rgb, 7 - min(level, 7))


def pixel(a: Assets, vid: Video, c: Column, y: int) -> int:
    if c.draw_start <= y <= c.draw_end:
        ty = (((y - vid.h // 2 + c.lh_half) * c.step) >> 16) & (TEX_N - 1)
        idx = a.tex[(c.tex_id << 12) | (ty << 6) | c.tex_x]
        return shade_rgb(a.palette[idx], c.shade)
    return background(vid, y)


def render(a: Assets, vid: Video, table: list[Column]) -> bytearray:
    """Full frame as packed RGB888 rows (what the HDMI output carries)."""
    out = bytearray(vid.w * vid.h * 3)
    bg = [background(vid, y) for y in range(vid.h)]
    half = vid.h // 2
    tex, pal = a.tex, a.palette
    for x, c in enumerate(table):
        base_tex = c.tex_id << 12 | c.tex_x
        shaded = {}
        for y in range(vid.h):
            if c.draw_start <= y <= c.draw_end:
                ty = (((y - half + c.lh_half) * c.step) >> 16) & 63
                idx = tex[base_tex | (ty << 6)]
                rgb = shaded.get(idx)
                if rgb is None:
                    rgb = shaded[idx] = shade_rgb(pal[idx], c.shade)
            else:
                rgb = bg[y]
            o = (y * vid.w + x) * 3
            out[o] = rgb >> 16
            out[o + 1] = (rgb >> 8) & 0xFF
            out[o + 2] = rgb & 0xFF
    return out


# ------------------------------------------------------------- cycle budget
DIV_CYCLES = 34   # launch state + 32 restoring steps + the cycle that sees the result


def column_cycles(tr: Trace) -> int:
    """Clock cycles the RTL ray engine spends on one column (ray_engine.sv FSM):
    CAMX, RAYDIR, two delta divisions (1 cycle each when the ray component is
    0), SIDE, two cycles per DDA step, PERP, the line-height division (1 cycle
    when the distance is 0), the texture-step division and WRITE."""
    return (2 + (DIV_CYCLES if tr.rdx else 1) + (DIV_CYCLES if tr.rdy else 1) + 1
            + 2 * tr.steps + 1 + (DIV_CYCLES if tr.perp else 1) + DIV_CYCLES + 1)


def frame_cycles(a: Assets, vid: Video, v: View) -> int:
    """Value of the engine's cycles_out counter for a whole frame."""
    return sum(column_cycles(cast_column(a, vid, v, x)[1]) for x in range(vid.w))
