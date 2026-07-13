#!/usr/bin/env python3
"""Terrain hazards, pickups and weather. Standalone (no pytest). Drives step()
directly so each mechanic is checked in isolation."""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine import config
from engine.view import build_view

PASS = True
def check(cond, msg):
    global PASS
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        PASS = False

IDLE = {"thrust": "none", "turn": 0, "fire": "none", "drop_trap": False, "special": False}
class Idle:
    @staticmethod
    def decide(view):
        return dict(IDLE)

def ent(name, team=None):
    return (name, Idle.decide, {}, None, team)

def hz(t, x, y, w, h):
    return {"type": t, "x": x, "y": y, "w": w, "h": h}

# --- lava: damage-over-time while standing in it --------------------------
lava = [hz("lava", 100, 100, 200, 200)]
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=lava, time_cap=50)
a = g.robots[0]; a.x, a.y = 200, 200          # inside the lava rect
hp_before = a.hp
g.step()
check(a.hp == hp_before - config.LAVA_DPS, f"lava deals {config.LAVA_DPS}/tick (hp {hp_before}->{a.hp})")

# --- pit: hurts + slows, but never kills ----------------------------------
pit = [hz("pit", 100, 100, 100, 100)]
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=pit, time_cap=50)
a = g.robots[0]; a.x, a.y = 150, 150          # inside the pit
hp_before = a.hp
g.step()
check(a.alive and a.hp == hp_before - config.PIT_DAMAGE,
      f"pit hurts (hp {hp_before}->{a.hp}) but does not eliminate")
check(a.slow_ticks > 0, "pit slows the robot")
# sitting in a pit can never drop you below the floor (a trap, not an executioner)
a.hp = config.PIT_DAMAGE  # one more full hit would go to/below zero without the floor
for _ in range(5):
    a.x, a.y = 150, 150                        # keep it parked in the pit
    g.step()
check(a.alive and a.hp == config.HAZARD_MIN_HP,
      f"pit floors at HAZARD_MIN_HP ({config.HAZARD_MIN_HP}), never kills (hp={a.hp})")

# --- water: bogs (slows) + light rust, but never kills --------------------
water = [hz("water", 100, 100, 200, 200)]
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=water, time_cap=50)
a = g.robots[0]; a.x, a.y = 200, 200          # inside the water rect
hp_before = a.hp
g.step()
check(a.alive and a.hp == hp_before - config.WATER_DPS,
      f"water rusts {config.WATER_DPS}/tick (hp {hp_before}->{a.hp}) but does not eliminate")
check(a.slow_ticks > 0, "water slows (bogs) the robot")
a.hp = config.WATER_DPS  # one more full tick would go to/below zero without the floor
for _ in range(5):
    a.x, a.y = 200, 200                        # keep it wading
    g.step()
check(a.alive and a.hp == config.HAZARD_MIN_HP,
      f"water floors at HAZARD_MIN_HP ({config.HAZARD_MIN_HP}), never kills (hp={a.hp})")

# --- mine: hurts + slows, but never kills ---------------------------------
g = Game([ent("A", "A"), ent("B", "B")], seed=1, time_cap=50)
a = g.robots[0]; b = g.robots[1]
a.x, a.y = 400, 400; b.x, b.y = 401, 401       # B parked on A's mine spot
b.hp = 3                                        # low enough that a full blast would kill
from engine.game import Mine
mn = Mine(999, a.id, 400, 400); mn.arm_in = 0; mn.armed = True
g.mines.append(mn)
g.step()
check(b.alive and b.hp == config.HAZARD_MIN_HP,
      f"mine blast floors an enemy at HAZARD_MIN_HP, never kills (hp={b.hp})")
check(b.slow_ticks > 0, "mine blast slows the enemy")

# --- ice: momentum carries when idle --------------------------------------
ice = [hz("ice", 0, 0, 600, 600)]
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=ice, time_cap=50)
a = g.robots[0]; a.x, a.y = 300, 300; a.last_dx, a.last_dy = 10.0, 0.0
x0 = a.x
g.step()                                      # idle, but on ice -> should slide
check(a.x > x0 + 1.0, f"ice carries momentum while idle (x {x0}->{round(a.x,1)})")
# off ice, an idle robot does not drift
g2 = Game([ent("A", "A"), ent("B", "B")], seed=1, time_cap=50)
a2 = g2.robots[0]; a2.x, a2.y = 300, 300; a2.last_dx = 10.0
x0 = a2.x; g2.step()
check(abs(a2.x - x0) < 1e-6, "no ice -> idle robot stays put (momentum unused)")

# --- pickups: collect, go dormant, respawn --------------------------------
pk = [{"x": 200, "y": 200, "kind": "rockets"}]
g = Game([ent("A", "A"), ent("B", "B")], seed=1, pickups=pk, time_cap=400)
a = g.robots[0]; a.x, a.y = 200, 200
rk_before = a.rockets_left
g.step()
check(a.rockets_left == rk_before + config.PICKUP_ROCKETS, "rockets crate refills ammo")
check(not g.pickups[0].active, "crate goes dormant after pickup")
a.x, a.y = 900, 900                           # walk away so it can respawn untouched
for _ in range(config.PICKUP_RESPAWN + 1):
    g.step()
check(g.pickups[0].active, "crate respawns after the delay")

# --- weather: deterministic roll, fog shortens range, wind has a vector ---
k1, w1 = Game._roll_weather("roll", 12345)
k2, w2 = Game._roll_weather("roll", 12345)
check(k1 == k2 and w1 == w2, "weather roll is deterministic for a seed")
check(Game._roll_weather("clear", 1)[0] == "clear", "default/clear weather has no effect")
fk, fw = Game._roll_weather("fog", 1)
check(fk == "fog", "fog can be forced")
g = Game([ent("A", "A"), ent("B", "B")], seed=1, weather="fog")
check(g._fog_mult() == config.WEATHER_FOG_RANGE, "fog shortens weapon/rocket range")
g = Game([ent("A", "A"), ent("B", "B")], seed=1, weather="wind")
check(g.arena.wind != (0.0, 0.0), "wind produces a drift vector")

# --- exposure: frame + view carry the new data ----------------------------
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=lava, pickups=pk, weather="fog")
fr = g.step()
check("hazards" in fr["status"] and fr["status"]["hazards"], "frame status carries hazards")
check("pickups" in fr and len(fr["pickups"]) == 1, "frame carries pickups")
check(fr["status"]["weather"] == "fog", "frame status carries weather")
v = build_view(g.robots[0], g.robots, g.arena, g.rockets, g.mines, g.pickups)
check(len(v.arena.hazards) == 1, "view exposes hazards to the bot")
check(v.weather.kind == "fog", "view exposes weather to the bot")
check(hasattr(v, "pickups"), "view exposes pickups to the bot")

# --- flipper: armed paddle hurls, flips, chips HP, then re-arms ------------
import math
flip_hz = [hz("flipper", 500, 300, 90, 90)]          # centre (545, 345), mid-arena
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=flip_hz, walls=[], time_cap=500)
a = g.robots[0]; b = g.robots[1]
a.x, a.y = 545, 345; b.x, b.y = 900, 600              # A parked dead-centre on the paddle
hp_before = a.hp
fr = g.step()
thrown = math.hypot(a.x - 545, a.y - 345)
check(thrown > config.FLIPPER_THROW * 0.9, f"flipper hurls the robot (moved {round(thrown, 1)} units)")
check(a.flipped_ticks > 0, "flipper leaves the robot wheels-up (flipped)")
check(a.hp == hp_before - config.FLIPPER_DAMAGE,
      f"flipper chips {config.FLIPPER_DAMAGE} HP (hp {hp_before}->{a.hp})")
kinds = {e["kind"] for e in fr["events"]}
check("flipper" in kinds and "flip" in kinds, "frame carries flipper + flip events for the viewer")
# on cooldown: the next robot over the paddle is NOT launched until it re-arms
b.x, b.y = 545, 345
bx, bhp = b.x, b.hp
g.step()
check(math.hypot(b.x - bx, b.y - 345) < config.FLIPPER_THROW * 0.5 and b.hp == bhp,
      "paddle on cooldown does not fire again immediately")
# a weak robot is chipped, never executed
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=flip_hz, walls=[], time_cap=500)
a = g.robots[0]; a.hp = config.FLIPPER_DAMAGE        # a full hit would reach zero
a.x, a.y = 545, 345
g.step()
check(a.alive and a.hp == config.HAZARD_MIN_HP,
      f"flipper floors at HAZARD_MIN_HP ({config.HAZARD_MIN_HP}), never kills (hp={a.hp})")

# --- turntable: grounded robots are carried + spun; hover skims it ---------
tt = [hz("turntable", 500, 300, 150, 150)]           # centre (575, 375), radius 75
g = Game([ent("A", "A"), ent("B", "B")], seed=1, hazards=tt, walls=[], time_cap=50)
a = g.robots[0]; a.x, a.y = 605, 375                  # 30 units east of centre
h0 = a.heading_deg
g.step()
moved = math.hypot(a.x - 605, a.y - 375)
check(moved > 3.0, f"turntable carries a grounded robot (moved {round(moved, 1)} units)")
check(abs((a.heading_deg - h0) % 360 - config.TURNTABLE_DEG_PER_TICK) < 1e-6,
      f"turntable spins the heading by {config.TURNTABLE_DEG_PER_TICK} deg/tick")
check(math.hypot(a.x - 575, a.y - 375) > 30, "turntable slings the robot outward (fling)")
g = Game([ent("A", "A"), ("H", Idle.decide, {"engine": "hover"}, None, "B")],
         seed=1, hazards=tt, walls=[], time_cap=50)
hbot = g.robots[1]; hbot.x, hbot.y = 605, 375
hh0 = hbot.heading_deg
g.step()
check(abs(hbot.x - 605) < 1e-6 and abs(hbot.y - 375) < 1e-6 and hbot.heading_deg == hh0,
      "hover engine skims the turntable (no carry, no spin)")

# --- spawns: nobody starts inside a hazard rect ------------------------------
from engine import maps
import random
m = maps.get("colosseum")
g = Game([ent(f"S{i}") for i in range(42)], seed=42, width=m["w"], height=m["h"],
         walls=m["walls"], hazards=m["hazards"], pickups=m["pickups"])
bad = [r.name for r in g.robots for h in m["hazards"]
       if h["x"] - r.radius <= r.x <= h["x"] + h["w"] + r.radius
       and h["y"] - r.radius <= r.y <= h["y"] + h["h"] + r.radius]
check(not bad, f"colosseum 42-bot FFA spawns clear of all hazard rects (violators: {bad})")
# hazard-free maps keep the locked ring placement byte-identical
g0 = Game([ent(f"S{i}") for i in range(6)], seed=11)
rng = random.Random(11 * 31 + 17)
off = rng.random() * 2 * math.pi
slots = list(range(6)); rng.shuffle(slots)
cx, cy = g0.arena.width / 2, g0.arena.height / 2
rad = min(g0.arena.width, g0.arena.height) * 0.40
exp = [(cx + rad * math.cos(off + 2 * math.pi * s / 6),
        cy + rad * math.sin(off + 2 * math.pi * s / 6)) for s in slots]
check(all(r.x == ex and r.y == ey for r, (ex, ey) in zip(g0.robots, exp)),
      "hazard-free ring placement byte-identical to locked formula")

# 1v1 / team-vs-team opposite-ends spawns must also clear the traps — this is
# the event-day bug: colosseum's water pools sit exactly on the 0.10/0.90 ends.
def sized(name, size, team):
    return (name, Idle.decide, {"size": size}, None, team)

for mapname in ("colosseum", "arena"):
    m = maps.get(mapname)
    for size in ("small", "medium", "large"):
        for k in (1, 3):    # 1v1 and 3v3 (teammates fan across the short axis)
            g = Game([sized(f"A{i}", size, "TeamA") for i in range(k)]
                     + [sized(f"B{i}", size, "TeamB") for i in range(k)],
                     seed=7, width=m["w"], height=m["h"], walls=m["walls"],
                     hazards=m["hazards"], pickups=m["pickups"])
            bad = [(r.name, round(r.x), round(r.y), h["type"]) for r in g.robots
                   for h in m["hazards"]
                   if h["x"] - r.radius <= r.x <= h["x"] + h["w"] + r.radius
                   and h["y"] - r.radius <= r.y <= h["y"] + h["h"] + r.radius]
            check(not bad, f"{mapname} {k}v{k} {size} end spawns clear of hazard rects (violators: {bad})")
# hazard-free maps keep the locked opposite-ends placement byte-identical
ge = Game([ent("A", "TeamA"), ent("B", "TeamB")], seed=5)
w, h = ge.arena.width, ge.arena.height
check((ge.robots[0].x, ge.robots[0].y) == (w * 0.10, h * 0.5)
      and (ge.robots[1].x, ge.robots[1].y) == (w * 0.90, h * 0.5),
      "hazard-free opposite-ends placement byte-identical to locked formula")

# --- determinism with everything on -----------------------------------------
mk = lambda: Game([ent("A", "A"), ent("B", "B")], seed=9,
                  hazards=lava + pit + ice + flip_hz + tt, pickups=pk, weather="roll", time_cap=80)
check(mk().run(collect_frames=False)["hash"] == mk().run(collect_frames=False)["hash"],
      "full hazard match is deterministic (same seed -> same hash)")

print("PASS" if PASS else "FAILED")
sys.exit(0 if PASS else 1)
