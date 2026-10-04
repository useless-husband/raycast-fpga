"""frame_ctrl: lockstep comparison with a reference model while the engine's
finishing time is swept across the frame boundary (early, exactly at nf,
late, more than a frame late), plus random timings.  Liveness: every
completed table is swapped in at a later nf; nothing ever deadlocks.

Regression: an earlier version, inlined in raycast_system, dropped an
engine_done that arrived in the same cycle as nf and then waited forever."""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from simrun import run


class Ref:
    def __init__(self):
        self.state, self.front, self.valid, self.drops = "UPDATE", 0, 0, 0
        self.start = self.swap = 0

    def update(self):
        return int(self.state == "UPDATE")

    def clock(self, nf, pd, ed):
        self.start = self.swap = 0
        if self.state == "UPDATE":
            self.state = "PLAYER"
        elif self.state == "PLAYER":
            if pd:
                self.start, self.state = 1, "RENDER"
        elif self.state == "RENDER":
            if nf:
                self.drops = (self.drops + 1) & 0xFFFF
            if ed:
                self.state = "READY"
        elif self.state == "READY" and nf:
            self.front ^= 1
            self.valid = self.swap = 1
            self.state = "UPDATE"


async def scenario(dut, period, player_lat, engine_lat, cycles, tag):
    """The caller starts the clock once per cocotb test."""
    dut.rst_in.value = 1
    dut.nf_in.value = dut.player_done_in.value = dut.engine_done_in.value = 0
    for _ in range(2):
        await RisingEdge(dut.clk_in)
    await FallingEdge(dut.clk_in)
    dut.rst_in.value = 0
    ref = Ref()
    pending_pd, pending_ed = [], []
    starts = swaps = 0
    last_done = None
    for c in range(cycles):
        nf = int(c % period == period - 1)
        pd = int(c in pending_pd)
        ed = int(c in pending_ed)
        if ref.update():
            pending_pd.append(c + player_lat(c))
        dut.nf_in.value, dut.player_done_in.value, dut.engine_done_in.value = nf, pd, ed
        assert int(dut.update_out.value) == ref.update(), f"{tag} cycle {c}"
        ref.clock(nf, pd, ed)
        await RisingEdge(dut.clk_in)
        await Timer(1, "ns")
        got = (int(dut.engine_start_out.value), int(dut.front_out.value), int(dut.valid_out.value),
               int(dut.swap_out.value), int(dut.drops_out.value))
        exp = (ref.start, ref.front, ref.valid, ref.swap, ref.drops)
        assert got == exp, f"{tag} cycle {c}: (start, front, valid, swap, drops) {got} != {exp}"
        if ref.start:
            starts += 1
            pending_ed.append(c + 1 + engine_lat(c))
        if ed:
            last_done = c
        if ref.swap:
            swaps += 1
            last_done = None
        # liveness: a finished table is shown within one frame period
        if last_done is not None:
            assert c - last_done <= period + 1, f"{tag}: table finished at {last_done} never swapped in"
        await FallingEdge(dut.clk_in)
    assert swaps >= 2, f"{tag}: only {swaps} swaps"
    return swaps, ref.drops


@cocotb.test()
async def sweep_engine_time_across_nf(dut):
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    period = 60
    for lat in range(30, 2 * period + 6):
        await scenario(dut, period, lambda c: 3, lambda c, lat=lat: lat, 8 * period, f"engine={lat}")


@cocotb.test()
async def random_latencies(dut):
    seed = int(os.environ.get("TEST_SEED", "6205"))
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    cocotb.start_soon(Clock(dut.clk_in, 10, unit="ns").start())
    await scenario(dut, 50, lambda c: rng.randint(1, 12), lambda c: rng.randint(5, 140), 20000,
                   f"seed={seed}")


def test_frame_ctrl():
    run("frame_ctrl", ["frame_ctrl.sv"], "test_frame_ctrl")
