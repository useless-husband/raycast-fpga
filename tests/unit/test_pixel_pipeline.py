"""pixel_pipeline against model.pixel(): every visible pixel of several frames,
from real column tables (the golden ray caster's output) and from random
column words, in both buffer halves; plus sync/data-enable alignment (the
outputs must be exactly the timing signals delayed by LATENCY clocks)."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

import raycast_model as m
from simrun import run

ASSETS = m.Assets()
W, H = 64, 48
VID = m.Video(W, H)
LATENCY = 5


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    dut.rst_in.value = 1
    dut.front_in.value = 0
    dut.valid_in.value = 1
    for _ in range(3):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0


def load(dut, half, words):
    for x, wd in enumerate(words):
        dut.table_mem[half * W + x].value = wd


async def frame(dut, table, tag, valid=True):
    """Run from the first visible pixel of a frame to the last and compare."""
    while not (int(dut.ad.value) and int(dut.hcount.value) == 0 and int(dut.vcount.value) == 0):
        await RisingEdge(dut.clk_in)
        await Timer(1, "ns")
    hist = []
    checked = 0
    while True:
        hist.append((int(dut.hcount.value), int(dut.vcount.value), int(dut.ad.value),
                     int(dut.hs.value), int(dut.vs.value)))
        await RisingEdge(dut.clk_in)
        await Timer(1, "ns")
        if len(hist) < LATENCY:
            continue
        # hist[-1] is the input one clock ago, so hist[-LATENCY] is the input
        # LATENCY clocks before the output we are looking at now
        h, v, ad, hs, vs = hist[-LATENCY]
        got = (int(dut.de_out.value), int(dut.hs_out.value), int(dut.vs_out.value))
        assert got == (ad, hs, vs), f"{tag}: sync misaligned at ({h},{v}): {got} vs {(ad, hs, vs)}"
        if ad:
            exp = m.pixel(ASSETS, VID, table[h], v) if valid else 0
            rgb = int(dut.rgb_out.value)
            assert rgb == exp, (f"{tag}: pixel ({h},{v}) expected {exp:06x} got {rgb:06x}\n"
                                f"  column {table[h]}")
            checked += 1
            if h == W - 1 and v == H - 1:
                return checked


def random_column(rng):
    ds = rng.randrange(H)
    de = rng.randrange(ds, H)
    return m.Column(ds, de, rng.randrange(8), rng.randrange(64), rng.randrange(8),
                    rng.randrange(1 << 15), rng.randrange(1 << 23))


@cocotb.test()
async def golden_tables_both_halves(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    cells = ASSETS.map
    empty = [(x, y) for y in range(32) for x in range(32) if cells[(y << 5) | x] == 0]
    total = 0
    for i in range(6):
        x, y = rng.choice(empty)
        p = m.Player((x << 14) | rng.randrange(m.ONE), (y << 14) | rng.randrange(m.ONE), rng.randrange(1024))
        table = m.cast_frame(ASSETS, VID, m.view_of(ASSETS, p))
        half = i % 2
        load(dut, half, [c.pack() for c in table])
        load(dut, 1 - half, [0] * W)        # the other half must not leak through
        dut.front_in.value = half
        total += await frame(dut, table, f"seed={seed} frame {i} half {half}")
    dut._log.info("checked %d pixels", total)


@cocotb.test()
async def random_column_words(dut):
    seed = int(os.environ.get("TEST_SEED", "6205")) + 3
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    for i in range(4):
        table = [random_column(rng) for _ in range(W)]
        load(dut, 0, [c.pack() for c in table])
        dut.front_in.value = 0
        await frame(dut, table, f"seed={seed} random frame {i}")


@cocotb.test()
async def black_until_valid(dut):
    await start(dut)
    dut.valid_in.value = 0
    table = [m.Column(0, H - 1, 1, 5, 0, 100, 1000)] * W
    load(dut, 0, [c.pack() for c in table])
    await frame(dut, table, "not valid", valid=False)


def test_pixel_pipeline():
    run("tb_pixel_pipeline", ["pixel_pipeline.sv", "rom_1p.sv", "video_sig_gen.sv",
                              "tests/unit/tb_pixel_pipeline.sv"], "test_pixel_pipeline")
