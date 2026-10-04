"""video_sig_gen: cycle-exact check against a reference counter at a small
resolution, plus edge-position checks over a full 1280x720@60 frame."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, First, RisingEdge, Timer

from simrun import run

SMALL = dict(ACTIVE_H_PIXELS=16, H_FRONT_PORCH=2, H_SYNC_WIDTH=3, H_BACK_PORCH=4,
             ACTIVE_LINES=8, V_FRONT_PORCH=1, V_SYNC_WIDTH=2, V_BACK_PORCH=3, FPS=5)


def p(dut, name):
    return int(getattr(dut, name).value)


async def reset(dut):
    cocotb.start_soon(Clock(dut.pixel_clk_in, 10, unit="ns").start())
    dut.rst_in.value = 1
    await ClockCycles(dut.pixel_clk_in, 3)
    await FallingEdge(dut.pixel_clk_in)
    # parked on the last blanking pixel: the next clock starts a full frame
    assert p(dut, "ad_out") == 0 and p(dut, "nf_out") == 0 and p(dut, "hs_out") == 0
    dut.rst_in.value = 0


@cocotb.test()
async def every_cycle_small(dut):
    """Every output on every cycle for 3 frames matches a reference model."""
    c = SMALL
    ht = c["ACTIVE_H_PIXELS"] + c["H_FRONT_PORCH"] + c["H_SYNC_WIDTH"] + c["H_BACK_PORCH"]
    vt = c["ACTIVE_LINES"] + c["V_FRONT_PORCH"] + c["V_SYNC_WIDTH"] + c["V_BACK_PORCH"]
    await reset(dut)
    h, v, fc = ht - 1, vt - 1, 0
    assert (p(dut, "hcount_out"), p(dut, "vcount_out")) == (h, v)
    for _ in range(3 * ht * vt + 7):
        await RisingEdge(dut.pixel_clk_in)
        await Timer(1, "ns")
        h += 1
        if h == ht:
            h = 0
            v = (v + 1) % vt
        nf = h == c["ACTIVE_H_PIXELS"] and v == c["ACTIVE_LINES"]
        if nf:
            fc = (fc + 1) % c["FPS"]
        hs0 = c["ACTIVE_H_PIXELS"] + c["H_FRONT_PORCH"]
        vs0 = c["ACTIVE_LINES"] + c["V_FRONT_PORCH"]
        exp = dict(hcount_out=h, vcount_out=v,
                   hs_out=int(hs0 <= h < hs0 + c["H_SYNC_WIDTH"]),
                   vs_out=int(vs0 <= v < vs0 + c["V_SYNC_WIDTH"]),
                   ad_out=int(h < c["ACTIVE_H_PIXELS"] and v < c["ACTIVE_LINES"]),
                   nf_out=int(nf), fc_out=fc)
        got = {k: p(dut, k) for k in exp}
        assert got == exp, f"h={h} v={v}: expected {exp}, got {got}"


@cocotb.test()
async def cea861_720p_edges(dut):
    """1280x720@60: over one whole frame (first visible pixel to the next
    frame's first visible pixel) every sync/active edge lands on its CEA-861
    position and the frame is exactly 1650 x 750 clocks."""
    await reset(dut)
    seen = {"hs_rise": 0, "vs_rise": 0, "ad_rise": 0, "nf": 0}
    starts = []
    cycle = 0

    async def count():
        nonlocal cycle
        while True:
            await RisingEdge(dut.pixel_clk_in)
            cycle += 1

    cocotb.start_soon(count())
    while len(starts) < 2:
        trig = await First(RisingEdge(dut.hs_out), FallingEdge(dut.hs_out),
                           RisingEdge(dut.vs_out), FallingEdge(dut.vs_out),
                           RisingEdge(dut.ad_out), FallingEdge(dut.ad_out),
                           RisingEdge(dut.nf_out))
        await Timer(1, "ns")
        h, v = p(dut, "hcount_out"), p(dut, "vcount_out")
        sig = trig.signal._name
        rising = isinstance(trig, RisingEdge)
        if sig == "hs_out":
            assert h == (1390 if rising else 1430), (sig, rising, h, v)
            seen["hs_rise"] += rising
        elif sig == "vs_out":
            assert h == 0 and v == (725 if rising else 730), (sig, rising, h, v)
            seen["vs_rise"] += rising
        elif sig == "ad_out":
            assert (h, rising) in ((0, True), (1280, False)) and v < 720, (sig, rising, h, v)
            if rising and v == 0:
                starts.append(cycle)
                if len(starts) == 2:
                    break
            seen["ad_rise"] += rising
        elif sig == "nf_out":
            assert (h, v) == (1280, 720)
            seen["nf"] += 1
    assert starts[1] - starts[0] == 1650 * 750, starts
    assert seen == {"hs_rise": 750, "vs_rise": 1, "ad_rise": 720, "nf": 1}, seen


def test_video_sig_gen_small():
    run("video_sig_gen", ["video_sig_gen.sv"], "test_video_sig_gen",
        parameters=SMALL, name="video_sig_gen_small", testcase="every_cycle_small")


def test_video_sig_gen_720p():
    run("video_sig_gen", ["video_sig_gen.sv"], "test_video_sig_gen", name="video_sig_gen_720p",
        testcase="cea861_720p_edges")
