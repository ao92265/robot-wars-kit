"""Identity-derived seed schedule (v2): staging/rename order must not change
any seed or outcome; every match records its exact seed.
Run from repo root:  python3 tests/test_seeds.py"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.seeds import SCHEME, match_seed, bot_seed, shuffle_seed
from tournament.runner import run_match
from tournament.bracket import _seed_bracket
from engine.dummies import sitting_duck, chaser

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def main():
    # ---- seed function units ----
    check("scheme is versioned", SCHEME == "v2")
    check("match seed ignores name order",
          match_seed(1, "FINAL", ["a", "b"]) == match_seed(1, "FINAL", ["b", "a"]))
    check("match seed changes with label",
          match_seed(1, "FINAL", ["a", "b"]) != match_seed(1, "R1_heat1", ["a", "b"]))
    check("match seed changes with event seed",
          match_seed(1, "FINAL", ["a", "b"]) != match_seed(2, "FINAL", ["a", "b"]))
    check("bot seeds distinct per bot",
          bot_seed(1, "FINAL", "a") != bot_seed(1, "FINAL", "b"))
    check("shuffle seed stable", shuffle_seed(7, "R1") == shuffle_seed(7, "R1"))

    # ---- restaging invariance: same roster, different list order ----
    duck = os.path.join(ROOT, "engine", "dummies", "sitting_duck.py")
    chas = os.path.join(ROOT, "engine", "dummies", "chaser.py")
    bots = [{"name": "duck", "path": duck, "loadout": sitting_duck.LOADOUT},
            {"name": "chaser", "path": chas, "loadout": chaser.LOADOUT}]

    tmp = tempfile.mkdtemp(prefix="rw_seed_test_")
    try:
        d1, d2 = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.makedirs(d1); os.makedirs(d2)
        r1 = run_match(bots, 42, "FINAL", d1)
        r2 = run_match(list(reversed(bots)), 42, "FINAL", d2)

        check("result records its seed", "seed" in r1 and r1["seed"] == match_seed(
            42, "FINAL", ["duck", "chaser"]))
        check("result records the seed scheme", r1.get("seed_scheme") == SCHEME)
        check("input order does not change the seed", r1["seed"] == r2["seed"])
        check("input order does not change the outcome",
              r1["winner"] == r2["winner"] and r1["ranking"] == r2["ranking"])
        with open(r1["jsonl"], "rb") as f1, open(r2["jsonl"], "rb") as f2:
            check("input order does not change the recording (byte-identical)",
                  f1.read() == f2.read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- bracket seeding invariance ----
    teams = [{"name": n} for n in ("Gamma", "Alpha", "Beta")]
    s1 = _seed_bracket(teams, 5)
    s2 = _seed_bracket(list(reversed(teams)), 5)
    check("bracket slots ignore input order",
          [t["name"] if t else None for t in s1] == [t["name"] if t else None for t in s2])

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all seed tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
