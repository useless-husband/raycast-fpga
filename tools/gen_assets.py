#!/usr/bin/env python3
"""Generate every ROM image the RTL loads with $readmemh.

  rtl/mem/map.hex        32x32 cells, 4 bits each, row-major (address = y*32 + x)
  rtl/mem/map_start.svh  player start position/angle (localparams)
  rtl/mem/tex.hex        8 textures x 64x64 palette indices (address = id<<12 | ty<<6 | tx)
  rtl/mem/palette.hex    256 x RGB888
  rtl/mem/trig.hex       1024 x Q1.14 cosine (two's complement)

All textures are drawn procedurally below; no external image is used.  The
generator is deterministic (integer hashing, no random module), and CI checks
that rerunning it reproduces the committed files byte for byte.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

MAP_N = 32
TEX = 64
NUM_TEX = 8
START_CHARS = {">": 0, "v": 256, "<": 512, "^": 768}

# ------------------------------------------------------------------ the map


def parse_map(path: str) -> tuple[list[int], tuple[int, int, int]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("#") or not line.strip():
                continue
            rows.append(line)
    if len(rows) != MAP_N or any(len(r) != MAP_N for r in rows):
        sys.exit(f"{path}: need exactly {MAP_N} rows of {MAP_N} cells, got "
                 f"{len(rows)} rows with lengths {sorted({len(r) for r in rows})}")
    cells, start = [], None
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in START_CHARS:
                if start is not None:
                    sys.exit(f"{path}: more than one player start")
                start = (x, y, START_CHARS[ch])
                ch = "."
            if ch == ".":
                v = 0
            elif ch in "12345678":
                v = int(ch)
            else:
                sys.exit(f"{path}:{y + 1}:{x + 1}: unknown cell {ch!r}")
            border = x in (0, MAP_N - 1) or y in (0, MAP_N - 1)
            if border and v == 0:
                sys.exit(f"{path}: border cell ({x},{y}) is open; the map must be closed")
            cells.append(v)
    if start is None:
        sys.exit(f"{path}: no player start (one of > v < ^)")
    sx, sy, ang = start
    half = 1 << 13
    return cells, ((sx << 14) + half, (sy << 14) + half, ang)


# ---------------------------------------------------------------- textures


def h32(x: int, y: int, s: int) -> int:
    """Small integer hash (deterministic on every platform)."""
    v = (x * 0x27D4EB2D) ^ (y * 0x165667B1) ^ (s * 0x9E3779B9)
    v &= 0xFFFFFFFF
    v ^= v >> 15
    v = (v * 0x85EBCA6B) & 0xFFFFFFFF
    v ^= v >> 13
    v = (v * 0xC2B2AE35) & 0xFFFFFFFF
    v ^= v >> 16
    return v


def rnd(x: int, y: int, s: int) -> float:
    return h32(x, y, s) / 4294967296.0


def vnoise(u: int, v: int, cell: int, s: int) -> float:
    """Tileable value noise on a 64x64 torus, lattice every `cell` pixels."""
    n = TEX // cell
    gx, gy = u // cell, v // cell
    fx, fy = (u % cell) / cell, (v % cell) / cell
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = rnd(gx % n, gy % n, s)
    b = rnd((gx + 1) % n, gy % n, s)
    c = rnd(gx % n, (gy + 1) % n, s)
    d = rnd((gx + 1) % n, (gy + 1) % n, s)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def ramp(r: int, level: float) -> int:
    """Palette index: ramp r (0..7), level 0..31 (clamped)."""
    return r * 32 + max(0, min(31, int(level)))


BRICK, STONE, WOOD, BLUE, MOSS, STEEL, PCB, GOLD = range(8)
RAMP_BASE = [
    (178, 64, 46),    # brick red
    (165, 162, 154),  # stone / mortar grey
    (158, 104, 58),   # wood
    (66, 108, 186),   # glazed blue tile
    (92, 142, 58),    # moss
    (128, 148, 158),  # steel
    (28, 118, 72),    # circuit board
    (226, 182, 58),   # gold / sign yellow
]


def palette() -> list[int]:
    out = []
    for base in RAMP_BASE:
        for i in range(32):
            t = i / 31
            px = []
            for c in base:
                v = c * (0.12 + 0.88 * t) + (255 - c) * max(0.0, t - 0.75) * 0.8
                px.append(max(0, min(255, int(v + 0.5))))
            out.append((px[0] << 16) | (px[1] << 8) | px[2])
    return out


def tex_brick(u: int, v: int) -> int:
    row = v // 8
    off = 8 if row % 2 else 0
    col = ((u + off) % TEX) // 16
    if v % 8 == 7 or (u + off) % 16 == 0:
        return ramp(STONE, 9 + 4 * rnd(u, v, 11))
    tone = 13 + 9 * rnd(col, row, 12)
    shade = -3 if v % 8 == 6 else (2 if v % 8 == 0 else 0)
    return ramp(BRICK, tone + shade + 6 * (vnoise(u, v, 4, 13) - 0.5))


def tex_stone(u: int, v: int) -> int:
    row = v // 16
    off = 16 if row % 2 else 0
    uu = (u + off) % TEX
    lx, ly = uu % 32, v % 16
    col = uu // 32
    if lx == 0 or ly == 0:
        return ramp(STONE, 6)
    edge = 5 if (lx == 1 or ly == 1) else (-5 if (lx == 31 or ly == 15) else 0)
    tone = 15 + 6 * rnd(col, row, 21) + edge
    tone += 8 * (vnoise(u, v, 8, 22) - 0.5) + 5 * (rnd(u, v, 23) - 0.5)
    return ramp(STONE, tone)


def tex_wood(u: int, v: int) -> int:
    plank = u // 16
    lu = u % 16
    if lu == 0:
        return ramp(WOOD, 4)
    wav = vnoise(u, v, 16, 31 + plank) * 6
    grain = (lu + wav + plank * 5) % 5
    tone = 16 + 6 * rnd(plank, 0, 32) - (4 if grain < 1 else 0)
    if lu in (3, 12) and v % 32 in (4, 5):
        return ramp(STEEL, 26 if v % 32 == 4 else 12)
    return ramp(WOOD, tone + 3 * (rnd(u, v, 33) - 0.5))


def tex_tile(u: int, v: int) -> int:
    lx, ly = u % 16, v % 16
    if lx == 0 or ly == 0:
        return ramp(STONE, 22)
    tone = 17 + 5 * rnd(u // 16, v // 16, 41)
    if lx == 1 or ly == 1:
        tone += 8
    elif lx == 15 or ly == 15:
        tone -= 6
    if (lx - ly) in (4, 5) and 3 < lx < 12:
        tone += 6
    return ramp(BLUE, tone)


def tex_moss(u: int, v: int) -> int:
    base = tex_stone(u, v)
    m = vnoise(u, v, 8, 51) * 0.7 + vnoise(u, v, 4, 52) * 0.3 + v / 160
    if m > 0.62:
        return ramp(MOSS, 10 + 18 * (m - 0.62) / 0.6 + 6 * rnd(u, v, 53))
    return base


def tex_steel(u: int, v: int) -> int:
    lx, ly = u % 32, v % 32
    if lx == 0 or ly == 0:
        return ramp(STEEL, 5)
    if lx == 1 or ly == 1:
        return ramp(STEEL, 24)
    for cx, cy in ((5, 5), (26, 5), (5, 26), (26, 26)):
        d = (lx - cx) ** 2 + (ly - cy) ** 2
        if d <= 2:
            return ramp(STEEL, 28 if (lx <= cx and ly <= cy) else 20)
        if d <= 4:
            return ramp(STEEL, 9)
    brushed = 6 * (rnd(0, v, 61) - 0.5) + 3 * (rnd(u, v, 62) - 0.5)
    return ramp(STEEL, 16 + brushed)


def tex_circuit(u: int, v: int) -> int:
    # a chip in the middle with pins
    if 22 <= u <= 41 and 22 <= v <= 41:
        if u in (22, 41) or v in (22, 41):
            return ramp(STONE, 10)
        if (u, v) == (25, 25):
            return ramp(STONE, 18)
        return ramp(STONE, 3)
    if (22 <= u <= 41 and v in (20, 21, 42, 43) and u % 3 == 0) or \
       (22 <= v <= 41 and u in (20, 21, 42, 43) and v % 3 == 0):
        return ramp(STEEL, 26)
    # traces on an 8-pixel grid, chosen by hashing each grid segment
    if v % 8 == 4 and rnd(u // 8, v // 8, 71) > 0.45:
        return ramp(GOLD, 20)
    if u % 8 == 4 and rnd(u // 8, v // 8, 72) > 0.55:
        return ramp(GOLD, 17)
    if u % 8 == 4 and v % 8 == 4:
        return ramp(GOLD, 26)
    return ramp(PCB, 12 + 4 * vnoise(u, v, 16, 73) + 2 * rnd(u, v, 74))


def tex_sign(u: int, v: int) -> int:
    """An arrow pointing right: asymmetric on purpose so a mirrored texture is
    obvious on screen (and in the tests' reference images)."""
    if u < 3 or u > 60 or v < 3 or v > 60:
        return ramp(WOOD, 8 + 4 * rnd(u, v, 81))
    shaft = 14 <= u <= 36 and 26 <= v <= 37
    head = 36 <= u <= 52 and abs(v - 31.5) <= (52 - u) * 1.05
    if shaft or head:
        return ramp(BRICK, 6)
    return ramp(GOLD, 21 + 4 * (vnoise(u, v, 8, 82) - 0.5))


TEXTURES = [tex_brick, tex_stone, tex_wood, tex_tile, tex_moss, tex_steel, tex_circuit, tex_sign]


def textures() -> list[int]:
    out = []
    for fn in TEXTURES:
        for v in range(TEX):
            for u in range(TEX):
                out.append(fn(u, v))
    return out


def trig() -> list[int]:
    out = []
    for i in range(1024):
        c = math.floor(16384 * math.cos(2 * math.pi * i / 1024) + 0.5)
        out.append(c & 0xFFFF)
    return out


# ------------------------------------------------------------------- output


def write_hex(path: str, values: list[int], digits: int) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        for v in values:
            f.write(f"{v:0{digits}x}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", default="maps/level1.txt")
    ap.add_argument("--out", default="rtl/mem")
    ap.add_argument("--sheet", help="also write a PNG contact sheet of the textures")
    args = ap.parse_args()

    cells, (sx, sy, ang) = parse_map(args.map)
    os.makedirs(args.out, exist_ok=True)
    pal = palette()
    tex = textures()
    write_hex(os.path.join(args.out, "map.hex"), cells, 1)
    write_hex(os.path.join(args.out, "tex.hex"), tex, 2)
    write_hex(os.path.join(args.out, "palette.hex"), pal, 6)
    write_hex(os.path.join(args.out, "trig.hex"), trig(), 4)
    with open(os.path.join(args.out, "map_start.svh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"// Generated by tools/gen_assets.py from {os.path.basename(args.map)}. Do not edit.\n")
        f.write(f"localparam logic [18:0] START_X = 19'd{sx};\n")
        f.write(f"localparam logic [18:0] START_Y = 19'd{sy};\n")
        f.write(f"localparam logic [9:0] START_ANGLE = 10'd{ang};\n")

    if args.sheet:
        sys.path.insert(0, os.path.dirname(__file__))
        from png import write_png
        scale, gap = 2, 8
        w = NUM_TEX * TEX * scale + (NUM_TEX - 1) * gap
        h = TEX * scale
        img = bytearray([0xF4, 0xF2, 0xEE] * (w * h))
        for t in range(NUM_TEX):
            for y in range(h):
                for x in range(TEX * scale):
                    rgb = pal[tex[(t << 12) | ((y // scale) << 6) | (x // scale)]]
                    o = (y * w + t * (TEX * scale + gap) + x) * 3
                    img[o:o + 3] = bytes(((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255))
        write_png(args.sheet, w, h, bytes(img))
    print(f"assets: map {args.map} start=({sx / 16384:.2f},{sy / 16384:.2f}) angle={ang} -> {args.out}")


if __name__ == "__main__":
    main()
