"""CI determinism golden gate: run one fixed-seed match TWICE with the
per-move wall-clock budget lifted (so a loaded runner can never trigger the
last-action substitution path) and require byte-identical frames.

The compared hash covers frames only — the header line carries
python_version, which legitimately differs across the CI matrix, so it is
excluded to let the cross-OS job compare hashes between platforms.

  python3 tests/determinism_gate.py [--out hash.txt]
Exit 0 + prints the frames sha256 twice-identical; exit 1 on divergence.
"""

import hashlib
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Lift the budget BEFORE any worker exists: the gate measures the engine,
# never the scheduler.
os.environ["ROBOT_WARS_MOVE_TIMEOUT_S"] = "30"

from tournament.runner import run_match
from engine.dummies import sitting_duck, chaser, sniper


def frames_sha(jsonl_path):
    h = hashlib.sha256()
    with open(jsonl_path, "rb") as f:
        first = True
        for line in f:
            if first and b'"schema"' in line and b'"robots"' not in line:
                first = False
                continue  # header excluded: python_version differs across matrix
            first = False
            h.update(line)
    return h.hexdigest()


def main():
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    bots = [
        {"name": "duck", "path": os.path.join(ROOT, "engine", "dummies", "sitting_duck.py"),
         "loadout": sitting_duck.LOADOUT},
        {"name": "chaser", "path": os.path.join(ROOT, "engine", "dummies", "chaser.py"),
         "loadout": chaser.LOADOUT},
        {"name": "sniper", "path": os.path.join(ROOT, "engine", "dummies", "sniper.py"),
         "loadout": sniper.LOADOUT},
    ]
    tmp = tempfile.mkdtemp(prefix="rw_gate_")
    try:
        d1, d2 = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.makedirs(d1); os.makedirs(d2)
        r1 = run_match(bots, 424242, "GATE", d1, map_kw="colosseum")
        r2 = run_match(bots, 424242, "GATE", d2, map_kw="colosseum")
        s1, s2 = frames_sha(r1["jsonl"]), frames_sha(r2["jsonl"])
        print(f"run1 frames sha256: {s1}")
        print(f"run2 frames sha256: {s2}")
        if s1 != s2:
            print("DETERMINISM GATE FAILED: same seed, same bots, different frames.")
            return 1
        print("determinism gate OK (byte-identical frames)")
        if out:
            with open(out, "w") as f:
                f.write(s1 + "\n")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
