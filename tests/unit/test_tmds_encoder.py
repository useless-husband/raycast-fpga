"""tmds_encoder against an independent DVI 1.0 reference (tests/ref/tmds_ref.py).

* exhaustive: every reachable running-disparity state x every input byte
* random: 60k cycles of random pixels and random blanking, compared symbol by
  symbol, decoded back, and with the stream's actual DC balance measured from
  the RTL output bits (a property check that does not use the reference)
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

import tmds_ref as ref
from simrun import run


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    dut.rst_in.value = 1
    dut.ve_in.value = 0
    dut.data_in.value = 0
    dut.control_in.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0


async def step(dut, data, de=1, ctrl=0):
    """Apply one input for one clock and return the registered symbol."""
    await FallingEdge(dut.clk_in)
    dut.data_in.value = data
    dut.ve_in.value = de
    dut.control_in.value = ctrl
    await RisingEdge(dut.clk_in)
    await Timer(1, "ns")
    return int(dut.tmds_out.value)


@cocotb.test()
async def exhaustive_disparity_x_byte(dut):
    prefixes = ref.reachable_disparities()
    assert sorted(prefixes) == list(range(-8, 9, 2))
    await start(dut)
    checked = 0
    for cnt, prefix in sorted(prefixes.items()):
        for d in range(256):
            await step(dut, 0, de=0)                 # blanking resets cnt to 0
            c = 0
            for b in prefix:
                exp, c = ref.encode(b, c)
                got = await step(dut, b)
                assert got == exp
            assert c == cnt
            exp, _ = ref.encode(d, cnt)
            got = await step(dut, d)
            assert got == exp, f"cnt={cnt} d=0x{d:02x}: expected {exp:010b} got {got:010b}"
            assert ref.decode(got) == ("data", d)
            checked += 1
    assert checked == 9 * 256
    dut._log.info("checked %d (disparity, byte) pairs", checked)


@cocotb.test()
async def control_tokens(dut):
    await start(dut)
    for ctrl in range(4):
        got = await step(dut, 0xA5, de=0, ctrl=ctrl)
        assert got == ref.CTRL_TOKENS[ctrl]
        assert ref.decode(got) == ("ctrl", ctrl)


@cocotb.test()
async def random_stream(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    cnt = 0
    stream = 0           # ones minus zeros actually sent since the last blanking
    worst = 0
    de_run = 0
    for i in range(60000):
        if de_run == 0:
            de_run = rng.choice([rng.randint(1, 40), -rng.randint(1, 12)])
        de = de_run > 0
        de_run += -1 if de_run > 0 else 1
        # bias the data: real video is smooth, plus some extreme bytes
        kind = rng.random()
        d = rng.choice([0x00, 0xFF, 0x0F, 0xF0, 0x55, 0xAA]) if kind < 0.2 else rng.randrange(256)
        ctrl = rng.randrange(4)
        exp, cnt = ref.encode(d, cnt, de=de, ctrl=ctrl)
        got = await step(dut, d, de=int(de), ctrl=ctrl)
        assert got == exp, f"seed={seed} cycle={i}: expected {exp:010b} got {got:010b}"
        if de:
            assert ref.decode(got) == ("data", d)
            ones = bin(got).count("1")
            stream += ones - (10 - ones)
            worst = max(worst, abs(stream))
            assert abs(stream) <= 8, f"seed={seed} cycle={i}: DC balance drifted to {stream}"
        else:
            stream = 0
    dut._log.info("max |running disparity| on the wire: %d", worst)


def test_tmds_encoder():
    run("tmds_encoder", ["tmds_encoder.sv"], "test_tmds_encoder")
