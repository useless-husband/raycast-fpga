"""Build-and-run helper shared by the cocotb unit tests.

Each test file holds the cocotb coroutines (run inside the simulator) and a
plain pytest function that calls `run(...)` to compile the RTL with Icarus
and execute them.  Simulations run with the repository root as the working
directory so the RTL's $readmemh paths ("rtl/mem/...") resolve the same way
they do for Verilator and Yosys.  Paths are absolute Python Path objects and
are passed to the tools as separate argv entries, so a checkout path with
spaces or non-ASCII characters is fine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

REPO = Path(__file__).resolve().parents[2]
RTL = REPO / "rtl"
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(REPO / "tests" / "ref"))


def run(toplevel: str, sources: list[str], test_module: str, parameters: dict | None = None,
        name: str | None = None, testcase: str | None = None) -> None:
    sim = os.environ.get("SIM", "icarus")
    build_dir = REPO / "build" / "cocotb" / (name or toplevel)
    runner = get_runner(sim)
    runner.build(
        sources=[RTL / s for s in sources],
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        includes=[RTL / "mem"],
        parameters=parameters or {},
        always=True,
        log_file=build_dir / "build.log",
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        build_dir=build_dir,
        test_dir=REPO,
        results_xml=str(build_dir / "results.xml"),
        testcase=testcase,
        parameters=parameters or {},
        extra_env={"PYTHONPATH": os.pathsep.join(sys.path)},
    )
