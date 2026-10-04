"""player FSM against model.player_update(): scripted moves, wall sliding,
teleports, and long random button sequences on the real map and on random
maps.  Property checked throughout: the player's cell is never a wall."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

import raycast_model as m
from memmodel import start_rom
from simrun import run

ASSETS = m.Assets()
START = m.Player(int(2.5 * m.ONE), int(2.5 * m.ONE), 0)   # from maps/level1.txt


class World:
    def __init__(self):
        self.map = list(ASSETS.map)

    def cell(self, mx, my):
        if not (0 <= mx < m.MAP_N and 0 <= my < m.MAP_N):
            return 1
        return self.map[(my << 5) | mx]


WORLD = World()


class ModelAssets:
    """Golden assets with the test's current map swapped in."""
    cos = ASSETS.cos
    sin = ASSETS.sin

    def cell(self, mx, my):
        return WORLD.cell(mx, my)


MA = ModelAssets()


def signed16(v):
    return v - 0x10000 if v & 0x8000 else v


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    start_rom(dut.clk_in, dut.map_addr_out, dut.map_data_in, lambda: WORLD.map)
    dut.rst_in.value = 1
    dut.update_in.value = 0
    dut.btn_in.value = 0
    dut.load_in.value = 0
    dut.load_x_in.value = 0
    dut.load_y_in.value = 0
    dut.load_angle_in.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0


async def update(dut, btn=0, load=None):
    await FallingEdge(dut.clk_in)
    dut.update_in.value = 1
    dut.btn_in.value = btn
    dut.load_in.value = int(load is not None)
    if load is not None:
        dut.load_x_in.value, dut.load_y_in.value, dut.load_angle_in.value = load.x, load.y, load.angle
    await FallingEdge(dut.clk_in)
    dut.update_in.value = 0
    dut.load_in.value = 0
    for cycles in range(1, 50):
        if int(dut.done_out.value):
            break
        await FallingEdge(dut.clk_in)
    else:
        raise AssertionError("player never finished its update")
    return cycles


def state(dut):
    return (int(dut.pos_x_out.value), int(dut.pos_y_out.value), int(dut.angle_out.value),
            signed16(int(dut.dir_x_out.value)), signed16(int(dut.dir_y_out.value)),
            signed16(int(dut.plane_x_out.value)), signed16(int(dut.plane_y_out.value)))


def expect(p):
    v = m.view_of(MA, p)
    return (p.x, p.y, p.angle, v.dir_x, v.dir_y, v.plane_x, v.plane_y)


async def drive(dut, p, btns, tag):
    for i, b in enumerate(btns):
        p = m.player_update(MA, p, m.Buttons.from_bits(b))
        await update(dut, b)
        got = state(dut)
        assert got == expect(p), f"{tag} step {i} btn={b:06b}: expected {expect(p)} got {got}"
        assert WORLD.cell(p.x >> 14, p.y >> 14) == 0, f"{tag} step {i}: inside a wall"
    return p


FWD, BACK, LEFT, RIGHT, SL, SR = 1, 2, 4, 8, 16, 32


@cocotb.test()
async def reset_state_and_first_update(dut):
    await start(dut)
    assert state(dut)[:3] == (START.x, START.y, START.angle)
    cycles = await update(dut, 0)
    assert state(dut) == expect(START)
    dut._log.info("an update takes %d cycles", cycles)


@cocotb.test()
async def scripted_walk_and_wall_slide(dut):
    WORLD.map = list(ASSETS.map)
    await start(dut)
    p = START
    script = [0] + [FWD] * 40 + [RIGHT] * 30 + [FWD | SR] * 30 + [LEFT | FWD] * 50 + \
             [BACK] * 20 + [SL] * 40 + [FWD | LEFT | RIGHT] * 10 + [FWD | BACK] * 5
    p = await drive(dut, p, script, "scripted")
    # walk diagonally into the east wall of the start room and keep pushing:
    # x must stop RADIUS short of the wall while y keeps sliding
    p = m.Player(int(5.5 * m.ONE), int(3.5 * m.ONE), 32)
    await update(dut, load=p)
    p = await drive(dut, p, [FWD] * 60, "slide")
    gap = (7 << 14) - m.RADIUS - p.x      # wall face at x = 7
    assert 0 < gap <= m.SPEED, f"x stopped {gap} short of the probe limit"
    assert p.y > int(4.0 * m.ONE), "y should keep sliding along the wall"
    assert WORLD.cell((p.x + m.RADIUS) >> 14, p.y >> 14) == 0


@cocotb.test()
async def teleport_every_axis_angle(dut):
    await start(dut)
    for ang in (0, 256, 512, 768, 1, 255, 1023, 300):
        p = m.Player(int(10.25 * m.ONE), int(12.75 * m.ONE), ang)
        await update(dut, load=p)
        assert state(dut) == expect(p), ang


@cocotb.test()
async def random_buttons_real_map(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    WORLD.map = list(ASSETS.map)
    await start(dut)
    btns = [rng.choice([FWD, FWD, FWD | LEFT, FWD | RIGHT, BACK, SL, SR, LEFT, RIGHT, FWD | SR,
                        rng.randrange(64)]) for _ in range(1500)]
    await update(dut, 0)
    await drive(dut, START, btns, f"seed={seed}")


@cocotb.test()
async def random_maps(dut):
    seed = int(os.environ.get("TEST_SEED", "6205")) + 1
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    for trial in range(12):
        cells = []
        for y in range(32):
            for x in range(32):
                border = x in (0, 31) or y in (0, 31)
                cells.append(rng.randint(1, 8) if border or rng.random() < 0.3 else 0)
        empty = [(x, y) for y in range(32) for x in range(32) if cells[(y << 5) | x] == 0]
        WORLD.map = cells
        x, y = rng.choice(empty)
        p = m.Player((x << 14) + rng.randrange(4096, 12288), (y << 14) + rng.randrange(4096, 12288),
                     rng.randrange(1024))
        await update(dut, load=p)
        btns = [rng.choice([FWD, FWD | LEFT, FWD | RIGHT, SR, SL, BACK, rng.randrange(64)])
                for _ in range(150)]
        await drive(dut, p, btns, f"seed={seed} map {trial}")


def test_player():
    run("player", ["player.sv", "rom_2p.sv"], "test_player")
