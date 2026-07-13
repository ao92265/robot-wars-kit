#!/usr/bin/env python3
"""1v1 team bracket runner. Standalone (no pytest). Uses in-process dummy bots
(no isolation subprocess) so it runs fast and deterministically."""
import os, sys, tempfile
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from engine.dummies import chaser, sniper, bomber, trapper
from tournament.bracket import (teams_from_accepted, _seed_bracket,
                                _tiebreak_team, run_bracket)

PASS = True
def check(cond, msg):
    global PASS
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        PASS = False

def bot(name, mod):
    return {"name": name, "bot": name, "loadout": {}, "decide": mod.decide}

# --- grouping: flat accepted list -> teams --------------------------------
accepted = [
    {"name": "team_Red__atk", "team": "Red", "bot": "atk", "loadout": {}, "decide": chaser.decide},
    {"name": "team_Red__def", "team": "Red", "bot": "def", "loadout": {}, "decide": bomber.decide},
    {"name": "team_Blue__one", "team": "Blue", "bot": "one", "loadout": {}, "decide": sniper.decide},
]
teams = teams_from_accepted(accepted)
check(len(teams) == 2, f"two teams grouped (got {len(teams)})")
check([t["name"] for t in teams] == ["Red", "Blue"], "teams keep first-seen order")
check(len(teams[0]["bots"]) == 2, "Red has both its bots")

# --- bracket seeding: pad to power of two with byes -----------------------
slots = _seed_bracket([{"name": n} for n in ("A", "B", "C")], seed=1)
check(len(slots) == 4, f"3 teams pad to a 4-slot bracket (got {len(slots)})")
check(sum(1 for s in slots if s is None) == 1, "exactly one bye added")
check(_seed_bracket([{"name": n} for n in "ABC"], 1) ==
      _seed_bracket([{"name": n} for n in "ABC"], 1), "seeding is deterministic")

# --- tiebreak helper ------------------------------------------------------
standings = [{"team": "X", "hp": 10, "damage_dealt": 5},
             {"team": "Y", "hp": 0, "damage_dealt": 50}]
check(_tiebreak_team(standings) == "X", "tiebreak favours surviving HP over damage")

# --- full bracket: 3 teams (forces a bye) -> one champion -----------------
T = [
    {"name": "Red",  "bots": [bot("atk", chaser), bot("def", bomber)]},
    {"name": "Blue", "bots": [bot("kite", sniper)]},
    {"name": "Green","bots": [bot("trap", trapper), bot("rush", chaser)]},
]
with tempfile.TemporaryDirectory() as d:
    s1 = run_bracket(T, seed=5, out_dir=d, auto=True)
check(s1 is not None and s1["champion"] in ("Red", "Blue", "Green"),
      f"bracket crowns a real champion (got {s1 and s1['champion']})")
check(all(m["winner"] in (m["team_a"], m["team_b"]) for m in s1["matches"]),
      "every match winner is one of its two teams")

with tempfile.TemporaryDirectory() as d:
    s2 = run_bracket(T, seed=5, out_dir=d, auto=True)
check(s1["champion"] == s2["champion"], "bracket is deterministic (same seed -> same champion)")

print("PASS" if PASS else "FAILED")
sys.exit(0 if PASS else 1)
