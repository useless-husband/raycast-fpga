"""debouncer: exact delay for a clean press, bounce rejection, and a random
input compared cycle by cycle with a reference model."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from simrun import run

N = 20  # STABLE_CYCLES for the test build


class Ref:
    def __init__(self):
        self.sync = [0, 0]
        self.count = 0
        self.out = 0

    def clock(self, din):
        s1 = self.sync[1]
        if s1 == self.out:
            self.count = 0
        elif self.count == N - 1:
            self.out, self.count = s1, 0
        else:
            self.count += 1
        self.sync = [din, self.sync[0]]


async def start(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    dut.rst_in.value = 1
    dut.dirty_in.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0


async def tick(dut, din):
    await FallingEdge(dut.clk_in)
    dut.dirty_in.value = din
    await RisingEdge(dut.clk_in)
    await Timer(1, "ns")
    return int(dut.clean_out.value)


@cocotb.test()
async def clean_press_delay(dut):
    await start(dut)
    outs = [await tick(dut, 1) for _ in range(N + 5)]
    first = outs.index(1)
    assert first == N + 1, f"output rose after {first + 1} cycles"   # 2 sync + N stable - 1
    assert all(outs[first:])


@cocotb.test()
async def bounce_is_rejected(dut):
    await start(dut)
    rng = random.Random(1)
    for _ in range(40):   # bursts shorter than N never get through
        level = rng.randint(0, 1)
        for _ in range(rng.randint(1, N - 3)):
            assert await tick(dut, level) == 0
        for _ in range(rng.randint(1, 3)):
            assert await tick(dut, 0) == 0


@cocotb.test()
async def random_matches_reference(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    model = Ref()
    din = 0
    for i in range(20000):
        if rng.random() < 0.08:
            din ^= 1
        model.clock(din)
        got = await tick(dut, din)
        assert got == model.out, f"seed={seed} cycle={i}"


def test_debouncer():
    run("debouncer", ["debouncer.sv"], "test_debouncer", parameters={"STABLE_CYCLES": N})
