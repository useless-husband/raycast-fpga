"""Python stand-in for a synchronous ROM read port (1 cycle latency), so a
unit test can feed a module any map it likes."""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge


def start_rom(clk, addr_sig, data_sig, table):
    """`table` is a list (or a callable addr -> value) that may be replaced
    between tests; the address is sampled mid-cycle, the data appears after
    the next rising edge, exactly like rom_1p/rom_2p."""
    async def serve():
        data_sig.value = 0
        while True:
            await FallingEdge(clk)
            try:
                addr = int(addr_sig.value)
            except ValueError:
                addr = 0
            await RisingEdge(clk)
            t = table() if callable(table) else table
            data_sig.value = t[addr]
    return cocotb.start_soon(serve())
