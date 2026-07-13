"""v2 combat: rockets, mines, walls (line-of-sight), chassis size, determinism."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config
from engine.game import Game
from engine.loadout import resolve_stats, validate_loadout

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  ok   {name}")
    else:
        failed += 1; print(f"  FAIL {name}")


BIG = {"hp": 6, "speed": 0, "damage": 6, "range": 6, "special": 0, "size": "medium"}
IDLE = lambda v: {}


def two_bot_game(a_decide, b_decide, walls=None):
    g = Game([("A", a_decide, BIG), ("B", b_decide, BIG)], seed=1, walls=walls or [])
    return g


# --- rockets: travel + splash damage --------------------------------------
def fire_rocket(view):
    return {"fire": "rocket"}

g = two_bot_game(fire_rocket, IDLE)
g.robots[0].x, g.robots[0].y, g.robots[0].heading_deg = 100.0, 400.0, 0.0   # face +x
g.robots[1].x, g.robots[1].y = 320.0, 400.0
b_hp0 = g.robots[1].hp
for _ in range(40):
    g.step()
    if g.robots[1].hp < b_hp0:
        break
check("rocket travels and splash-damages target", g.robots[1].hp < b_hp0)
check("shooter credited rocket damage", g.robots[0].damage_dealt > 0)

# --- rocket ammo cap -------------------------------------------------------
g = two_bot_game(fire_rocket, IDLE)
for _ in range(config.TIME_CAP):
    g.step()
    if g.robots[0].rockets_left == 0:
        break
check("rocket ammo cannot exceed ROCKET_AMMO", g.robots[0].rockets_left == 0)

# --- mines: arm then detonate on enemy proximity ---------------------------
drop_once = {"done": False}
def dropper(view):
    if not drop_once["done"]:
        drop_once["done"] = True
        return {"drop_trap": True}
    return {}

g = two_bot_game(dropper, IDLE)
g.robots[0].x, g.robots[0].y = 200.0, 200.0
g.robots[1].x, g.robots[1].y = 900.0, 600.0   # far away
g.step()                                        # mine dropped at (200,200)
for _ in range(config.TRAP_ARM_TICKS + 1):
    g.step()
check("mine is on the field and armed", len(g.mines) == 1 and g.mines[0].armed)
g.robots[1].x, g.robots[1].y = 205.0, 205.0     # walk the enemy onto it
hp_before = g.robots[1].hp
g.step()
check("mine detonates on enemy proximity", g.robots[1].hp < hp_before)
check("mine consumed after detonation", len(g.mines) == 0)

# --- walls block laser line-of-sight ---------------------------------------
def laser(view):
    return {"fire": "laser"}

wall = [(190.0, 360.0, 20.0, 80.0)]             # between A and B
g = two_bot_game(laser, IDLE, walls=wall)
g.robots[0].x, g.robots[0].y, g.robots[0].heading_deg = 100.0, 400.0, 0.0
g.robots[1].x, g.robots[1].y = 320.0, 400.0
hp_before = g.robots[1].hp
for _ in range(15):
    g.step()
check("wall blocks laser (target unharmed)", g.robots[1].hp == hp_before)

g = two_bot_game(laser, IDLE, walls=[])         # same setup, no wall
g.robots[0].x, g.robots[0].y, g.robots[0].heading_deg = 100.0, 400.0, 0.0
g.robots[1].x, g.robots[1].y = 320.0, 400.0
hp_before = g.robots[1].hp
for _ in range(15):
    g.step()
check("no wall -> laser hits", g.robots[1].hp < hp_before)

# --- chassis size: radius + hp tradeoff ------------------------------------
small = resolve_stats({"hp": 4, "size": "small"})
large = resolve_stats({"hp": 4, "size": "large"})
check("small radius < large radius", small["radius"] < large["radius"])
check("large hp > small hp (same points)", large["max_hp"] > small["max_hp"])
ok, _ = validate_loadout({"hp": 4, "size": "huge"})
check("invalid size rejected", not ok)
ok, _ = validate_loadout({"hp": 4, "size": "small"})
check("valid size accepted", ok)

# --- determinism with rockets + mines in play ------------------------------
def chaotic(view):
    a = {"turn": 7, "thrust": "forward"}
    if view.self.rocket_ready:
        a["fire"] = "rocket"
    if view.self.trap_ready:
        a["drop_trap"] = True
    return a

r1 = Game([("A", chaotic, BIG), ("B", chaotic, BIG)], seed=9).run(collect_frames=False)
r2 = Game([("A", chaotic, BIG), ("B", chaotic, BIG)], seed=9).run(collect_frames=False)
check("same seed -> identical hash (with rockets+mines)", r1["hash"] == r2["hash"])

# --- appearance sanitised --------------------------------------------------
g = Game([("A", IDLE, BIG, {"color": "not-a-hex", "shape": "banana"}),
          ("B", IDLE, BIG, {"color": "#ABCDEF", "shape": "orb"})], seed=1)
check("bad color -> None", g.robots[0].appearance["color"] is None)
check("bad shape -> default tank", g.robots[0].appearance["shape"] == "tank")
check("good color kept", g.robots[1].appearance["color"] == "#ABCDEF")
check("good shape kept", g.robots[1].appearance["shape"] == "orb")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
