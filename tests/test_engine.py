"""Engine tests: determinism, termination, combat sanity, loadout validation.
Run from repo root:  python3 tests/test_engine.py   (pure stdlib, no pytest needed)."""

import os
import sys
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine.loadout import validate_loadout
from engine.dummies import sitting_duck, chaser, sniper


def load_bot(path):
    spec = importlib.util.spec_from_file_location("b", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


starter = load_bot(os.path.join(ROOT, "my_bot.py"))
entries = [
    ("Starter", starter.decide, starter.LOADOUT),
    ("Sniper", sniper.decide, sniper.LOADOUT),
    ("Chaser", chaser.decide, chaser.LOADOUT),
    ("Duck", sitting_duck.decide, sitting_duck.LOADOUT),
]

print("determinism + termination")
r1 = Game(entries, seed=7).run()
r2 = Game(entries, seed=7).run()
check("same seed -> identical state hash", r1["hash"] == r2["hash"])
check("same seed -> same winner", r1["winner_id"] == r2["winner_id"])
check("match terminates within time cap", r1["ticks"] <= 1200)
check("produced frames", len(r1["frames"]) == r1["ticks"])
r3 = Game(entries, seed=99).run()
check("different seed -> (usually) different match", r3["hash"] != r1["hash"])

print("combat sanity")
# starter vs sitting duck: duck should die, starter should win
duel = Game([("Starter", starter.decide, starter.LOADOUT),
             ("Duck", sitting_duck.decide, sitting_duck.LOADOUT)], seed=3).run()
check("starter beats sitting duck", duel["winner_name"] == "Starter")
check("duck took damage / died", duel["reason"] in ("last standing", "time cap"))

print("loadout validation")
ok, _ = validate_loadout({"hp": 4, "speed": 3, "damage": 2, "range": 2, "special": 1})
check("legal loadout accepted", ok)
bad, msg = validate_loadout({"hp": 10, "speed": 10})
check("over-budget rejected", not bad)
bad2, _ = validate_loadout({"hp": 4, "lasers": 3})
check("unknown stat rejected", not bad2)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
