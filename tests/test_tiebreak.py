"""Tiebreak: a FINAL must always crown someone, even on a mutual KO."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.runner import decide_champion

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  ok   {name}")
    else:
        failed += 1; print(f"  FAIL {name}")


# 1. Clean win: champion is the winner, no tiebreak note.
champ, tb = decide_champion({"winner": "Alpha", "standings": []})
check("clean win keeps winner", champ == "Alpha" and tb is None)

# 2. Mutual KO: no winner -> top of standings wins on survived-longest.
final = {"winner": None, "standings": [
    {"name": "Beta", "alive": False, "hp": 0, "damage_dealt": 90, "death_tick": 56},
    {"name": "Gamma", "alive": False, "hp": 0, "damage_dealt": 80, "death_tick": 40},
]}
champ, tb = decide_champion(final)
check("mutual KO crowns survivor-longest", champ == "Beta")
check("tiebreak note explains basis", tb is not None and "tick 56" in tb and "90 damage" in tb)

# 3. Time-cap survivors (both alive, no death_tick) -> basis falls back to HP.
final = {"winner": None, "standings": [
    {"name": "Delta", "alive": True, "hp": 12, "damage_dealt": 30, "death_tick": None},
]}
champ, tb = decide_champion(final)
check("alive tie crowns on HP basis", champ == "Delta" and "12 HP" in tb)

# 4. Degenerate: no winner and no standings -> None, no crash.
champ, tb = decide_champion({"winner": None, "standings": []})
check("empty standings -> None", champ is None and tb is None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
