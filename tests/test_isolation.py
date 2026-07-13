"""Isolation harness test: a match containing a while-True bot and a crashing bot
must complete quickly and never hang or crash the engine.
Run from repo root:  python3 tests/test_isolation.py"""

import os
import sys
import time
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from tournament.isolation import IsolationPool

FIX = os.path.join(ROOT, "tests", "fixtures")
BOTS = [("Good", "good_bot"), ("Looper", "loop_bot"), ("Crasher", "crash_bot")]


def load_loadout(name):
    path = os.path.join(FIX, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.LOADOUT, path


def main():
    entries, specs = [], []
    for rid, (disp, mod) in enumerate(BOTS):
        loadout, path = load_loadout(mod)
        entries.append((disp, None, loadout))
        specs.append((rid, path, 1000 + rid))

    t0 = time.time()
    pool = IsolationPool(specs)
    try:
        game = Game(entries, seed=5, decider=pool.decider)
        result = game.run(collect_frames=False)
    finally:
        pool.close()
    elapsed = time.time() - t0

    passed = True
    def check(name, cond):
        nonlocal passed
        print(("  ok   " if cond else "  FAIL ") + name)
        passed = passed and cond

    check("match completed (didn't hang)", result["ticks"] <= 1200)
    check("the good bot wins (idle/broken bots lose)", result["winner_name"] == "Good")
    check("finished promptly despite while-True (< 20s)", elapsed < 20)
    print(f"  [winner={result['winner_name']} ticks={result['ticks']} wall={elapsed:.1f}s]")
    print("PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
