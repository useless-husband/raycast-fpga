"""divider: edge cases, random operands of every magnitude, exact latency,
division by zero, and requests issued while busy."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from simrun import run

W = 32
MASK = (1 << W) - 1


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    dut.rst_in.value = 1
    dut.data_valid_in.value = 0
    dut.dividend_in.value = 0
    dut.divisor_in.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0


async def divide(dut, n, d, poke_while_busy=False):
    await FallingEdge(dut.clk_in)
    dut.dividend_in.value = n
    dut.divisor_in.value = d
    dut.data_valid_in.value = 1
    await FallingEdge(dut.clk_in)
    dut.data_valid_in.value = 0
    cycles = 1
    while True:
        if int(dut.data_valid_out.value):
            return int(dut.quotient_out.value), int(dut.remainder_out.value), int(dut.error_out.value), cycles
        if poke_while_busy and cycles == 3:
            assert int(dut.busy_out.value) == 1
            dut.dividend_in.value = 12345
            dut.divisor_in.value = 7
            dut.data_valid_in.value = 1
        await FallingEdge(dut.clk_in)
        dut.data_valid_in.value = 0
        cycles += 1
        assert cycles < 100, "divider hung"


@cocotb.test()
async def edge_cases(dut):
    await start(dut)
    cases = [(0, 1), (1, 1), (MASK, 1), (MASK, MASK), (5, 7), (1 << 28, 1), (1 << 28, 3),
             (1 << 28, 16384), (360 << 14, 1), (1 << 22, 65535), (1 << 22, 1), (MASK, 2), (6, 3)]
    for n, d in cases:
        q, r, err, cycles = await divide(dut, n, d)
        assert (q, r, err) == (n // d, n % d, 0), (n, d, q, r)
        assert cycles == W + 1, f"latency {cycles}"


@cocotb.test()
async def divide_by_zero(dut):
    await start(dut)
    q, r, err, cycles = await divide(dut, 1234, 0)
    assert err == 1 and q == MASK and r == 1234 and cycles == 1
    q, r, err, _ = await divide(dut, 10, 3)        # recovers
    assert (q, r, err) == (3, 1, 0)


@cocotb.test()
async def ignores_requests_while_busy(dut):
    await start(dut)
    q, r, err, _ = await divide(dut, 1000, 3, poke_while_busy=True)
    assert (q, r, err) == (333, 1, 0)


@cocotb.test()
async def random_operands(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    for i in range(3000):
        n = rng.getrandbits(rng.randint(0, W))
        d = rng.getrandbits(rng.randint(1, W)) or 1
        q, r, err, _ = await divide(dut, n, d)
        assert (q, r, err) == (n // d, n % d, 0), f"seed={seed} i={i}: {n}/{d} -> q={q} r={r}"


def test_divider():
    run("divider", ["divider.sv"], "test_divider", parameters={"WIDTH": W})
