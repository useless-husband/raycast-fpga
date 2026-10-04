"""Fast checks of the golden model and the asset pipeline (no simulator).

These back the numbers quoted in docs/DESIGN.md: the worst-case DDA step
count, the per-frame cycle budget, and that the committed ROM images are
exactly what tools/gen_assets.py produces."""

import filecmp
import os
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "model"))
import raycast_model as m  # noqa: E402

ASSETS = m.Assets(str(REPO / "rtl" / "mem"))
SEED = int(os.environ.get("TEST_SEED", "6205"))
MAX_STEPS = 59           # 30 steps on one axis + 29 on the other inside a closed 32x32 map
WORST_COLUMN = m.column_cycles(m.Trace(0, 1, 1, 0, 0, MAX_STEPS, 0, 0, 0, 1))


class MapAssets:
    def __init__(self, cells):
        self.map = cells

    def cell(self, mx, my):
        if not (0 <= mx < 32 and 0 <= my < 32):
            return 1
        return self.map[(my << 5) | mx]


def test_column_word_roundtrip():
    rng = random.Random(SEED)
    for _ in range(2000):
        c = m.Column(rng.randrange(1024), rng.randrange(1024), rng.randrange(8), rng.randrange(64),
                     rng.randrange(8), rng.randrange(1 << 15), rng.randrange(1 << 23))
        assert m.Column.unpack(c.pack()) == c
        assert c.pack() < 1 << 70


def test_worst_case_steps_and_cycle_budget():
    """No ray in a closed 32x32 map takes more than 59 DDA steps, so a column
    costs at most 259 cycles and a 1280-column frame at most 331,520 of the
    1,237,500 cycles in a 720p frame (both simulation configs fit too)."""
    rng = random.Random(SEED)
    open_box = [1 if x in (0, 31) or y in (0, 31) else 0 for y in range(32) for x in range(32)]
    worst = 0
    for cells in (ASSETS.map, open_box):
        a = MapAssets(cells)
        empty = [(x, y) for y in range(32) for x in range(32) if cells[(y << 5) | x] == 0]
        for _ in range(300):
            x, y = rng.choice(empty)
            v = m.view_of(ASSETS, m.Player((x << 14) | rng.randrange(m.ONE),
                                           (y << 14) | rng.randrange(m.ONE), rng.randrange(1024)))
            for col in range(0, 1280, 37):
                worst = max(worst, m.cast_column(a, m.Video(), v, col)[1].steps)
    # corner to corner across the empty box reaches the bound
    v = m.view_of(ASSETS, m.Player(int(1.01 * m.ONE), int(1.02 * m.ONE), 128))
    worst = max(worst, max(m.cast_column(MapAssets(open_box), m.Video(), v, c)[1].steps for c in range(1280)))
    assert worst == MAX_STEPS
    assert WORST_COLUMN == 259
    assert 1280 * WORST_COLUMN < 1650 * 750          # 720p: 26.8% of the frame
    assert 320 * WORST_COLUMN < (320 + 96) * (180 + 40)   # the 320x180 sim build


def test_player_never_enters_a_wall():
    rng = random.Random(SEED)
    p = m.Player(int(2.5 * m.ONE), int(2.5 * m.ONE), 0)
    for _ in range(20000):
        p = m.player_update(ASSETS, p, m.Buttons.from_bits(rng.randrange(64)))
        assert ASSETS.cell(p.x >> 14, p.y >> 14) == 0, p


def test_axis_aligned_view_has_exact_zero_component():
    """At 0/90/180/270 degrees the centre column's ray is exactly axis
    aligned, which is the 'infinite delta' path in the hardware."""
    for ang, axis in ((0, "rdy"), (256, "rdx"), (512, "rdy"), (768, "rdx")):
        v = m.view_of(ASSETS, m.Player(int(2.5 * m.ONE), int(2.5 * m.ONE), ang))
        tr = m.cast_column(ASSETS, m.Video(), v, 640)[1]
        assert getattr(tr, axis) == 0
        assert (tr.delta_x if axis == "rdx" else tr.delta_y) == m.DELTA_INF


def test_generated_assets_match_committed(tmp_path):
    subprocess.run([sys.executable, str(REPO / "tools" / "gen_assets.py"), "--map",
                    str(REPO / "maps" / "level1.txt"), "--out", str(tmp_path)], check=True,
                   stdout=subprocess.DEVNULL)
    for name in ("map.hex", "tex.hex", "palette.hex", "trig.hex", "map_start.svh"):
        assert filecmp.cmp(tmp_path / name, REPO / "rtl" / "mem" / name, shallow=False), name


def test_map_loader_rejects_open_border(tmp_path):
    rows = (REPO / "maps" / "level1.txt").read_text().splitlines()
    rows = [r for r in rows if not r.startswith("#")]
    rows[0] = "." + rows[0][1:]
    bad = tmp_path / "bad.txt"
    bad.write_text("\n".join(rows) + "\n")
    r = subprocess.run([sys.executable, str(REPO / "tools" / "gen_assets.py"), "--map", str(bad),
                        "--out", str(tmp_path / "out")], capture_output=True, text=True)
    assert r.returncode != 0 and "closed" in r.stderr
