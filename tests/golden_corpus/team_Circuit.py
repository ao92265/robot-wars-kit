# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs sniper --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
#  See GUIDELINES.md for the full view + action reference.
#
#  The point of this exercise: find the dirtiest *legal* strategy. Probe the
#  edges. Try to break the game. (You can't crash it — go on, try.)
# ============================================================================

# Spend up to 12 points across six stats (per-stat cap 6): hp, speed, damage,
# range, special (dash), agility (turn rate). Then three FREE archetype picks:
#   size:   "small" | "medium" | "large"
#   gun:    "laser" | "cannon" | "shotgun"
#   engine: "standard" | "sprint" | "tank" | "hover"
# Every knob + trade-off table: CUSTOMIZE.md. Copyable builds: examples/.
LOADOUT = {"hp": 1, "speed": 5, "damage": 4, "range": 0, "special": 1,
           "agility": 1, "size": "medium", "gun": "laser", "engine": "tank"}

# Branding — your colours on the big screen. color/accent are #RRGGBB hex; shape is
# your spinner's look: "tank", "speeder", "orb", "spike". Cosmetic only.
APPEARANCE = {"color": "#ff8c1a", "shape": "spike", "accent": "#1a1a1a"}

import math


def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide.
    Steel is steel — the tank engine skips hazards, not walls."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55        # swing around it
    return turn


_prev = {"tick": -99, "x": 0.0, "y": 0.0}   # last-seen enemy position, for leading


def aim_lead(view, t):
    """Aim a rocket where the target WILL be, not where it is. Rockets travel,
    so a moving enemy dodges a shot at its current spot. Estimate its velocity
    from last tick and offset the aim along it (TOF factor is a tuning knob)."""
    me = view.self
    bearing = t.bearing
    if _prev["tick"] == view.tick - 1:
        vx, vy = t.x - _prev["x"], t.y - _prev["y"]
        tof = t.dist / 14.0                          # rough rocket time-of-flight
        px, py = t.x + vx * tof, t.y + vy * tof
        raw = math.degrees(math.atan2(py - me.y, px - me.x)) - me.heading
        bearing = (raw + 180) % 360 - 180
    _prev["tick"], _prev["x"], _prev["y"] = view.tick, t.x, t.y
    return bearing


def decide(view):
    """FLIP-LOCK: rocket to flip -> charge in and dump laser during the ~2.5s
    helpless window -> seed a mine on the retreat line so a recovering enemy
    eats another flip. Tank engine barely flips, so I live in my own splash and
    only THEY go wheels-up. Chain the windows into a kill loop."""
    me = view.self

    # Flipped and helpless — wait it out. (Tank rights in ~1.2s.)
    if me.flipped:
        return {}

    # SUDDEN DEATH: never get caught by the closing ring — drift to centre.
    c = view.arena.collapse
    if c > 0:
        margin = c + me.radius + 12
        if not (margin < me.x < view.arena.width - margin and
                margin < me.y < view.arena.height - margin):
            cx, cy = view.arena.width / 2, view.arena.height / 2
            ang = math.degrees(math.atan2(cy - me.y, cx - me.x)) - me.heading
            ang = (ang + 180) % 360 - 180
            act = {"turn": steer(view, ang), "thrust": "forward"}
            if me.special_ready:
                act["special"] = True
            return act

    # PUNISH: a flipped enemy is the whole build. Charge and dump everything.
    if view.enemies and view.enemies[0].flipped:
        t = view.enemies[0]
        act = {"turn": steer(view, t.bearing), "thrust": "forward"}
        if me.special_ready and t.dist > 70:
            act["special"] = True                     # dash to spend the window closing
        if t.dist <= me.weapon_range and abs(t.bearing) < me.weapon_arc and not me.overheated:
            act["fire"] = "laser"                     # helpless + point-blank = free damage
        elif me.rocket_ready and abs(t.bearing) < 12 and t.dist < 320:
            act["fire"] = "rocket"                    # too far for the gun: keep them down
        # Seed a mine on top of them: when they self-right and lunge, they trip
        # it and flip AGAIN. That's the kill loop.
        if me.trap_ready and t.dist < 90:
            act["drop_trap"] = True
        return act

    # DODGE incoming rockets — being flipped mid-plan loses the fight.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 130:
        r = view.incoming_rockets[0]
        veer = 85 if r.bearing < 0 else -85           # break away from the rocket's side
        act = {"turn": steer(view, veer), "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        return act

    # No target in sight: hunt, never sit still.
    if not view.enemies:
        return {"turn": steer(view, 15), "thrust": "forward"}

    # ENGAGE: line up, open a window with a rocket, chip while closing.
    t = view.enemies[0]
    aim = t.bearing
    lead = aim_lead(view, t)                          # where to point for a rocket
    act = {"turn": steer(view, aim)}

    if t.dist <= me.weapon_range and abs(aim) < me.weapon_arc and not me.overheated:
        act["fire"] = "laser"                         # in gun reach: chip damage
    elif me.rocket_ready and abs(lead) < 9 and 90 < t.dist < 340:
        act["turn"] = steer(view, lead)               # swing onto the lead point
        act["fire"] = "rocket"                        # open a flip window from range

    if t.dist > me.weapon_range * 0.8:
        act["thrust"] = "forward"                     # close the gap for the dump

    # RETREAT LINE: drop a mine when they're on our doorstep. Tank barely flips,
    # so I seed mines around myself and a recovering chaser eats the next flip.
    if me.trap_ready and t.dist < 150:
        act["drop_trap"] = True

    return act
