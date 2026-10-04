"""ray_engine against model.cast_column(), column by column and bit for bit.

Edge cases first (rays exactly along grid lines, axis-aligned view angles
where one ray component is exactly zero, standing exactly on a wall face so
the distance is 0, grid vertices), then random positions and angles on the
real level and on random maps.  Every mismatch prints the seed, the frame,
the column and the reference model's intermediate values.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

import raycast_model as m
from simrun import run

ASSETS = m.Assets()
ONE = m.ONE


class MapAssets:
    """Golden assets with a replaceable map."""
    def __init__(self, cells):
        self.map = cells

    def cell(self, mx, my):
        if not (0 <= mx < m.MAP_N and 0 <= my < m.MAP_N):
            return 1
        return self.map[(my << 5) | mx]


def random_map(rng, density):
    cells = []
    for y in range(32):
        for x in range(32):
            border = x in (0, 31) or y in (0, 31)
            cells.append(rng.randint(1, 8) if border or rng.random() < density else 0)
    return cells


def box_map():
    """Border walls only, plus a pillar; makes exact wall-face positions easy."""
    cells = [0] * 1024
    for i in range(32):
        for x, y in ((i, 0), (i, 31), (0, i), (31, i)):
            cells[(y << 5) | x] = 1 + (i % 8)
    for x, y in ((10, 10), (11, 10), (10, 11), (11, 11)):
        cells[(y << 5) | x] = 7
    return cells


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.w = int(dut.W.value)
        self.h = int(dut.H.value)
        self.vid = m.Video(self.w, self.h)
        self.loaded = None
        self.max_cycles = 0
        self.frames = 0
        self.columns = 0

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk_in, 10, unit="ns").start())
        d.rst_in.value = 1
        d.start_in.value = 0
        for _ in range(3):
            await RisingEdge(d.clk_in)
        await FallingEdge(d.clk_in)
        d.rst_in.value = 0

    def load_map(self, cells):
        if cells is not self.loaded:
            for i, v in enumerate(cells):
                self.dut.map_mem[i].value = v
            self.loaded = cells

    async def frame(self, cells, p, tag):
        d = self.dut
        self.load_map(cells)
        a = MapAssets(cells)
        v = m.view_of(ASSETS, p)
        await FallingEdge(d.clk_in)
        d.pos_x_in.value, d.pos_y_in.value = v.x, v.y
        d.dir_x_in.value, d.dir_y_in.value = v.dir_x, v.dir_y
        d.plane_x_in.value, d.plane_y_in.value = v.plane_x, v.plane_y
        d.start_in.value = 1
        await FallingEdge(d.clk_in)
        d.start_in.value = 0
        got = {}
        while True:
            await RisingEdge(d.clk_in)
            await Timer(1, "ns")
            if int(d.col_we_out.value):
                got[int(d.col_x_out.value)] = int(d.col_data_out.value)
            if int(d.done_out.value):
                break
        assert sorted(got) == list(range(self.w)), f"{tag}: columns written {len(got)}"
        for x in range(self.w):
            exp, tr = m.cast_column(a, self.vid, v, x)
            if got[x] != exp.pack():
                raise AssertionError(
                    f"{tag} {p} column {x}:\n  expected {exp}\n  got      {m.Column.unpack(got[x])}\n"
                    f"  model trace {tr}")
        cycles = int(d.cycles_out.value)
        exp_cycles = m.frame_cycles(a, self.vid, v)
        assert cycles == exp_cycles, f"{tag}: engine took {cycles} cycles, cycle model says {exp_cycles}"
        self.max_cycles = max(self.max_cycles, cycles)
        self.frames += 1
        self.columns += self.w
        await Timer(1, "ns")
        return cycles

    async def walk(self, cells, p, tag, frames=1):
        return await self.frame(cells, p, tag)


def p_(x, y, a):
    return m.Player(int(round(x * ONE)), int(round(y * ONE)), a)


@cocotb.test()
async def edge_cases(dut):
    b = Bench(dut)
    await b.start()
    box = box_map()
    cases = []
    for a in (0, 256, 512, 768):
        cases += [p_(5.5, 5.5, a), p_(5.0, 5.0, a), p_(5.0, 5.5, a), p_(5.5, 5.0, a)]  # grid lines/vertices
        cases += [p_(1.0, 7.5, a), p_(30.0 - 1 / ONE, 7.5, a), p_(7.5, 1.0, a)]         # on / at wall faces
        cases += [p_(12.0, 10.5, a), p_(9.0 + 1 / ONE, 10.5, a)]                          # touching the pillar
    for a in (1, 255, 257, 511, 513, 767, 769, 1023, 128, 384, 640, 896):          # near-axis and diagonals
        cases += [p_(5.5, 5.5, a), p_(9.0, 9.0, a), p_(1.0 + 1 / ONE, 1.0 + 1 / ONE, a)]
    for i, p in enumerate(cases):
        await b.frame(box, p, f"edge case {i}")
    dut._log.info("edge cases: %d frames, max %d cycles/frame (W=%d)", b.frames, b.max_cycles, b.w)


@cocotb.test()
async def zero_distance_saturates(dut):
    """Standing exactly on a wall face looking at it: perp = 0, the line
    height must saturate instead of dividing by zero."""
    b = Bench(dut)
    await b.start()
    box = box_map()
    p = p_(1.0, 7.5, 512)                     # x = 1.0, wall cell x = 0 to the west
    v = m.view_of(ASSETS, p)
    col, tr = m.cast_column(MapAssets(box), b.vid, v, b.w // 2)
    assert tr.perp == 0 and col.lh_half == m.LHH_MAX
    await b.frame(box, p, "zero distance")


@cocotb.test()
async def random_real_map(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    b = Bench(dut)
    await b.start()
    cells = list(ASSETS.map)
    empty = [(x, y) for y in range(32) for x in range(32) if cells[(y << 5) | x] == 0]
    n = int(os.environ.get("ENGINE_FRAMES", "60"))
    for i in range(n):
        x, y = rng.choice(empty)
        p = m.Player((x << 14) | rng.randrange(ONE), (y << 14) | rng.randrange(ONE), rng.randrange(1024))
        await b.frame(cells, p, f"seed={seed} frame {i}")
    dut._log.info("real map: %d frames, max %d cycles/frame (W=%d)", b.frames, b.max_cycles, b.w)


@cocotb.test()
async def random_maps(dut):
    seed = int(os.environ.get("TEST_SEED", "6205")) + 7
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    b = Bench(dut)
    await b.start()
    n = int(os.environ.get("ENGINE_FRAMES", "60"))
    for i in range(n):
        cells = random_map(rng, rng.choice([0.0, 0.05, 0.2, 0.45]))
        empty = [(x, y) for y in range(32) for x in range(32) if cells[(y << 5) | x] == 0]
        x, y = rng.choice(empty)
        p = m.Player((x << 14) | rng.randrange(ONE), (y << 14) | rng.randrange(ONE), rng.randrange(1024))
        await b.frame(cells, p, f"seed={seed} frame {i}")
    dut._log.info("random maps: %d frames, max %d cycles/frame (W=%d)", b.frames, b.max_cycles, b.w)


SRC = ["ray_engine.sv", "divider.sv", "tests/unit/tb_ray_engine.sv"]


def test_ray_engine_small():
    run("tb_ray_engine", SRC, "test_ray_engine", parameters={"W": 64, "H": 48}, name="ray_engine_64x48")


def test_ray_engine_odd():
    run("tb_ray_engine", SRC, "test_ray_engine", parameters={"W": 77, "H": 45}, name="ray_engine_77x45",
        testcase=["edge_cases", "random_maps"])


def test_ray_engine_720p():
    run("tb_ray_engine", SRC, "test_ray_engine", parameters={"W": 1280, "H": 720}, name="ray_engine_720p",
        testcase=["zero_distance_saturates", "random_real_map"], env={"ENGINE_FRAMES": "2"})
