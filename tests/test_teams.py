#!/usr/bin/env python3
"""Team play: no friendly fire, shared win condition, FFA unchanged.
Standalone (no pytest): prints checks and sys.exit(1) on first failure."""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine.dummies import chaser, sniper, bomber, trapper

PASS = True
def check(cond, msg):
    global PASS
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        PASS = False

def ent(name, mod, team=None):
    base = (name, mod.decide, {}, None)
    return base + (team,) if team is not None else base

# --- 1. FFA is byte-identical run-to-run (determinism intact) -------------
ffa = [ent("A", chaser), ent("B", sniper), ent("C", bomber), ent("D", trapper)]
r1 = Game(ffa, seed=7).run(collect_frames=False)
r2 = Game(ffa, seed=7).run(collect_frames=False)
check(r1["hash"] == r2["hash"], "FFA deterministic (same seed -> same hash)")
check(r1["winner_team"] == f"solo:{r1['winner_id']}", "FFA winner_team is the soloist's own team")

# --- 2. Two teams of two: match resolves to a team -----------------------
teams = [ent("R1", chaser, "Red"), ent("R2", bomber, "Red"),
         ent("B1", sniper, "Blue"), ent("B2", trapper, "Blue")]
res = Game(teams, seed=7).run(collect_frames=True)
alive_teams = {s["team"] for s in res["standings"] if s["alive"]}
# Valid terminal states: one team wiped the other (<=1 alive team), OR the clock
# ran out with both still standing (time cap). Never an in-progress state.
ended_clean = len(alive_teams) <= 1 or res["reason"] == "time cap"
check(ended_clean, f"ends on last-team-standing or time cap (teams={alive_teams}, reason={res['reason']})")
check(res["winner_team"] in ("Red", "Blue"), f"winner_team is a real team (got {res['winner_team']})")
if alive_teams:
    check(res["winner_team"] in alive_teams, "winner_team is among the survivors")
# Team match is deterministic too.
check(Game(teams, seed=7).run(collect_frames=False)["hash"] == res["hash"],
      "team match deterministic (same seed -> same hash)")

# --- 3. A lone team present wins immediately (no enemy to fight) ----------
solo_team = [ent("S1", chaser, "Solo"), ent("S2", bomber, "Solo")]
res3 = Game(solo_team, seed=3).run(collect_frames=False)
check(res3["winner_team"] == "Solo" and res3["ticks"] <= 1,
      f"single team alone wins instantly (team={res3['winner_team']}, ticks={res3['ticks']})")

# --- 4. No friendly fire: splash spares teammates, still bites the owner ---
# Stack an owner, a teammate and an enemy inside one blast and detonate directly.
ff = [ent("Owner", chaser, "A"), ent("Mate", bomber, "A"), ent("Foe", sniper, "B")]
g = Game(ff, seed=1)
owner, mate, foe = g.robots
owner.x, owner.y = 100.0, 100.0
mate.x, mate.y = 110.0, 100.0      # well inside blast radius
foe.x, foe.y = 115.0, 100.0
hp0 = {r.id: r.hp for r in g.robots}
g._explode(105.0, 100.0, 80.0, 40, owner.id)
check(mate.hp == hp0[mate.id], "teammate takes ZERO splash from an ally")
check(foe.hp < hp0[foe.id], "enemy takes splash")
check(owner.hp < hp0[owner.id], "owner still hurts itself (self-damage preserved)")

print("PASS" if PASS else "FAILED")
sys.exit(0 if PASS else 1)
