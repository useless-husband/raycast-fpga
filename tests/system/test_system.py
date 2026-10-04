"""Full-system tests: the Verilator build of rtl/raycast_system.sv against the
golden model, bit for bit.

For every scenario the harness (sim/sim_main.cpp) records
  * the player state the ray engine latched for each frame,
  * every column-table word the engine wrote,
  * whole frames rebuilt only from the three TMDS output channels,
and checks on every clock that each TMDS symbol decodes to the pixel or sync
that entered the encoders.  Here we recompute all of it with
model/raycast_model.py and require exact equality.

Randomised scenarios use a fixed seed (TEST_SEED, default 6205) that is
printed in every failure message.
"""

from __future__ import annotations

import json
import os
import random
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "model"))
import raycast_model as m  # noqa: E402

ASSETS = m.Assets(str(REPO / "rtl" / "mem"))
SEED = int(os.environ.get("TEST_SEED", "6205"))
CONFIGS = {"small": m.Video(320, 180), "720p": m.Video(1280, 720)}
ONE = m.ONE
START = m.Player(int(2.5 * ONE), int(2.5 * ONE), 0)
EMPTY = [(x, y) for y in range(32) for x in range(32) if ASSETS.map[(y << 5) | x] == 0]


# --------------------------------------------------------------- harness I/O
class Entry:
    def __init__(self, load: m.Player | None = None, mask: int = 0, capture: bool = True):
        self.load, self.mask, self.capture = load, mask, capture

    def line(self) -> str:
        c = " C" if self.capture else ""
        if self.load:
            return f"L {self.load.x} {self.load.y} {self.load.angle}{c}"
        return f"B {self.mask}{c}"


def run_harness(cfg: str, entries: list[Entry], name: str, tmp_path: Path):
    exe = REPO / "build" / f"vsim_{cfg}" / "vsim"
    if not exe.exists():
        pytest.fail(f"{exe.relative_to(REPO)} is missing: run the tests with 'make system'")
    scen = tmp_path / f"{name}.txt"
    scen.write_text("".join(e.line() + "\n" for e in entries))
    out = tmp_path / name
    out.mkdir()
    subprocess.run([str(exe), "run", str(scen), str(out)], cwd=REPO, check=True,
                   stdout=subprocess.DEVNULL, timeout=900)
    summary = json.loads((out / "summary.json").read_text())

    raw = (out / "tables.bin").read_bytes()
    assert raw[:4] == b"RCT1"
    w, h, n = struct.unpack_from("<3I", raw, 4)
    off = 16
    tables = []
    for _ in range(n):
        x, y, a, cycles, written = struct.unpack_from("<5I", raw, off)
        off += 20
        cols = [int.from_bytes(raw[off + 9 * i: off + 9 * i + 9], "little") for i in range(w)]
        off += 9 * w
        tables.append((m.Player(x, y, a), cycles, written, cols))

    raw = (out / "frames.bin").read_bytes()
    assert raw[:4] == b"RCF1"
    fw, fh, nf = struct.unpack_from("<3I", raw, 4)
    off = 16
    frames = {}
    size = fw * fh * 3
    for _ in range(nf):
        (t,) = struct.unpack_from("<I", raw, off)
        frames[t] = raw[off + 4: off + 4 + size]
        off += 4 + size
    return summary, tables, frames


# ---------------------------------------------------------------- comparison
def expected_states(entries: list[Entry]) -> list[m.Player]:
    """Replay the scenario through the golden player.  Update 0 happens right
    after reset, before any button can pass the debouncer, so its buttons are
    zero by construction."""
    p, out = START, []
    for i, e in enumerate(entries):
        if e.load:
            p = e.load
        else:
            p = m.player_update(ASSETS, p, m.Buttons.from_bits(e.mask if i else 0))
        out.append(p)
    return out


def first_pixel_diff(vid, a: bytes, b: bytes):
    for i in range(0, len(a), 3):
        if a[i:i + 3] != b[i:i + 3]:
            px = i // 3
            return px % vid.w, px // vid.w, a[i:i + 3].hex(), b[i:i + 3].hex()
    return None


def check(cfg: str, entries: list[Entry], name: str, tmp_path: Path) -> dict:
    vid = CONFIGS[cfg]
    summary, tables, frames = run_harness(cfg, entries, name, tmp_path)
    tag = f"[{name}, {cfg}, TEST_SEED={SEED}]"
    assert summary["drops"] == 0, f"{tag} the ray engine missed a frame: {summary}"
    assert summary["tmds_errors"] == 0, f"{tag} TMDS stream disagrees with the pixels: {summary}"
    assert summary["worst_disparity"] <= 8, summary
    assert summary["captured"] == summary["wanted"], summary

    states = expected_states(entries)
    for k, ((rtl_p, cycles, written, cols), exp_p) in enumerate(zip(tables, states)):
        assert rtl_p == exp_p, f"{tag} frame {k}: player state {rtl_p} != golden {exp_p}"
        assert written == vid.w, f"{tag} frame {k}: {written} columns written"
        view = m.view_of(ASSETS, exp_p)
        for x, word in enumerate(cols):
            exp, trace = m.cast_column(ASSETS, vid, view, x)
            if word != exp.pack():
                pytest.fail(f"{tag} frame {k} {exp_p} column {x}:\n  golden {exp}\n"
                            f"  rtl    {m.Column.unpack(word)}\n  trace  {trace}")
        if entries[k].capture:
            golden = bytes(m.render(ASSETS, vid, m.cast_frame(ASSETS, vid, view)))
            got = frames[k]
            if got != golden:
                x, y, g, r = first_pixel_diff(vid, golden, got)
                pytest.fail(f"{tag} frame {k} {exp_p}: first differing pixel ({x},{y}) "
                            f"golden {g} rtl {r}; column {m.cast_column(ASSETS, vid, view, x)[0]}")
    summary["frames_compared"] = sum(e.capture for e in entries)
    summary["columns_compared"] = len(entries) * vid.w
    print(f"\n{name} [{cfg}]: {json.dumps(summary)}")
    return summary


# -------------------------------------------------------------- scenarios
def rand_player(rng: random.Random) -> m.Player:
    x, y = rng.choice(EMPTY)
    return m.Player((x << 14) | rng.randrange(ONE), (y << 14) | rng.randrange(ONE), rng.randrange(1024))


def edge_case_players() -> list[m.Player]:
    """Axis-aligned views (one ray component exactly 0 in the centre column),
    rays exactly along grid lines and through grid vertices, and standing
    exactly on / a hair away from a wall face (distance 0 -> saturation)."""
    ps = []
    for a in (0, 256, 512, 768):
        ps += [m.Player(int(2.5 * ONE), int(2.5 * ONE), a),      # cell centre
               m.Player(3 * ONE, 3 * ONE, a),                    # grid vertex
               m.Player(3 * ONE, int(5.5 * ONE), a),             # on a vertical grid line
               m.Player(int(4.5 * ONE), 2 * ONE, a)]             # on a horizontal grid line
    ps += [m.Player(1 * ONE, int(1.5 * ONE), 512),               # on the west wall face, looking at it
           m.Player(7 * ONE - 1, int(1.5 * ONE), 0),             # 1/16384 from the east wall
           m.Player(int(3.5 * ONE), 1 * ONE, 768),               # on the north wall face
           m.Player(1 * ONE, 1 * ONE, 640),                      # in the room's corner, looking into it
           m.Player(int(2.5 * ONE), int(2.5 * ONE), 1),
           m.Player(int(2.5 * ONE), int(2.5 * ONE), 1023)]
    return ps


FWD, BACK, LEFT, RIGHT, SL, SR = 1, 2, 4, 8, 16, 32


def walk_entries() -> list[Entry]:
    """Scripted walk out of the start room, including pushing into walls."""
    script = [(1, 0), (20, FWD), (16, RIGHT), (40, FWD), (64, LEFT), (30, FWD | SR),
              (12, BACK), (25, SL), (40, FWD | RIGHT), (60, FWD), (10, FWD | BACK | LEFT)]
    out = []
    for n, mask in script:
        out += [Entry(mask=mask, capture=False) for _ in range(n)]
    for i in range(0, len(out), 9):
        out[i].capture = True
    out[-1].capture = True
    return out


# ------------------------------------------------------------------ tests
def test_edge_cases_small(tmp_path):
    check("small", [Entry(load=p) for p in edge_case_players()], "edge_cases", tmp_path)


def test_random_teleports_small(tmp_path):
    rng = random.Random(SEED)
    entries = [Entry(load=rand_player(rng)) for _ in range(int(os.environ.get("SYSTEM_FRAMES", "150")))]
    check("small", entries, "random_teleports", tmp_path)


def test_scripted_walk_small(tmp_path):
    check("small", walk_entries(), "scripted_walk", tmp_path)


def test_random_buttons_small(tmp_path):
    rng = random.Random(SEED + 1)
    entries = [Entry(mask=0)] + [Entry(mask=rng.choice([FWD, FWD, FWD | LEFT, FWD | RIGHT, SR, SL, BACK,
                                                         rng.randrange(64)]), capture=rng.random() < 0.2)
                                 for _ in range(300)]
    entries[-1].capture = True
    check("small", entries, "random_buttons", tmp_path)


def test_720p_frames(tmp_path):
    """Whole 1280x720 frames: start view, edge cases and random views."""
    rng = random.Random(SEED + 2)
    ps = [START] + edge_case_players()[16:20] + [rand_player(rng) for _ in range(5)]
    entries = [Entry(load=p) for p in ps] + [Entry(mask=FWD), Entry(mask=FWD | RIGHT)]
    check("720p", entries, "frames_720p", tmp_path)


def test_720p_tables(tmp_path):
    """Column tables at 720p for many random views and all edge cases."""
    rng = random.Random(SEED + 3)
    ps = edge_case_players() + [rand_player(rng) for _ in range(int(os.environ.get("SYSTEM_FRAMES_720P", "40")))]
    entries = [Entry(load=p, capture=False) for p in ps]
    entries[-1].capture = True
    s = check("720p", entries, "tables_720p", tmp_path)
    assert s["max_engine_cycles"] < 1650 * 750, s
