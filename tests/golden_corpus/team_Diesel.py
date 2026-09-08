# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python arena.py            (watch it fight the dummies)
#  Tune it:  python arena.py --vs sniper --fast --best-of 20
#  Check it: python arena.py --check     (is my loadout legal?)
#  Submit:   python arena.py --submit "Your Team Name"
# ============================================================================
#
#  "ANVIL" — laser + tank + medium.  An ANTI-META bot.
#
#  The bet: every other team read the same manuals, so the field is mostly
#  agile sprint-kiters that throw flip-rockets and punish flips. This bot beats
#  that field instead of joining it:
#    * TANK engine barely flips + self-rights 2x fast -> their flip-rockets
#      bounce off me; my rockets/mines flip THEM. I get the free kill window.
#    * I HOLD CENTRE -> sudden-death ring burns the kiters at the walls.
#    * LASER runs coolest + jams least -> I never hand over a vent window.
#    * Points into AGILITY cancel the tank turn penalty so orbiters can't
#      farm free misses off me.
#  Loses to: a pure long-range cannon sniper that out-reaches me while I close.
#            (I eat the ring-hold + rocket pressure to fix that.)
# ============================================================================

# 12 points, max 6 per stat: hp, speed, damage, range, special (dash), agility.
LOADOUT = {"hp": 1, "speed": 2, "damage": 3, "range": 2, "special": 1, "agility": 3,
           "size": "medium", "gun": "laser", "engine": "tank"}

# Branding — mean spiked drum, gunmetal + hazard-gold.
APPEARANCE = {"color": "#c0392b", "shape": "spike", "accent": "#f4d03f"}

import math


def _norm(a):
    """Wrap an angle to [-180, 180]."""
    while a > 180:
        a -= 360
    while a < -180:
        a += 360
    return a


def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide.
    Driving into walls is the #1 rookie death."""
    h = math.radians(view.self.heading + turn)
    px = view.self.x + math.cos(h) * 80
    py = view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55        # swing around it
    return turn


def bearing_to(view, tx, ty):
    """Degrees I must turn to face point (tx, ty)."""
    ang = math.degrees(math.atan2(ty - view.self.y, tx - view.self.x))
    return _norm(ang - view.self.heading)


def safe_center(view):
    """The point I want to hold: arena centre. In sudden death this is the only
    ground that never turns to lava."""
    return view.arena.width / 2.0, view.arena.height / 2.0


def near_ring(view):
    """Am I close to the molten sudden-death ring? Returns True if I should
    bail toward centre. collapse == 0 means the ring hasn't started."""
    c = view.arena.collapse
    if not c or c <= 0:
        return False
    margin = view.self.radius + 24
    return (view.self.x < c + margin or view.self.x > view.arena.width - c - margin or
            view.self.y < c + margin or view.self.y > view.arena.height - c - margin)


def can_fire_gun(view):
    """Fire discipline: don't cook the gun off. Never fire while venting; ease
    off before the ceiling so I never force-vent in a fight."""
    s = view.self
    if s.overheated or s.jammed:
        return False
    return s.heat < 82


def decide(view):
    s = view.self

    # --- 0. Stay out of the fire. Sudden-death ring beats everything else. ---
    if near_ring(view):
        cx, cy = safe_center(view)
        act = {"turn": steer(view, bearing_to(view, cx, cy)), "thrust": "forward"}
        if s.special_ready:
            act["special"] = True          # dash for the centre
        return act

    if not view.enemies:
        # No target: drift toward centre and scan (own the middle early).
        cx, cy = safe_center(view)
        b = bearing_to(view, cx, cy)
        return {"turn": steer(view, b + 20), "thrust": "forward"}

    target = view.enemies[0]                 # nearest first
    aim = target.bearing
    lined_up = abs(aim) < s.weapon_arc

    # --- 1. FLIPPED ENEMY = FREE KILL. Charge in and dump everything. --------
    if getattr(target, "flipped", False):
        act = {"turn": steer(view, aim), "thrust": "forward"}
        if s.special_ready and target.dist > 60:
            act["special"] = True            # dash-ram the helpless bot
        if lined_up and target.dist <= s.weapon_range and not (s.overheated or s.jammed):
            act["fire"] = "laser"            # dump heat freely — it's a free window
        elif s.rocket_ready and abs(aim) < 14:
            act["fire"] = "rocket"           # or a rocket to keep it flipped
        return act

    # --- 2. Dodge an incoming rocket. I barely flip, but splash still hurts. --
    if view.incoming_rockets and view.incoming_rockets[0].dist < 120:
        rk = view.incoming_rockets[0]
        # Turn broadside to the rocket's bearing and pull off its line.
        juke = 90 if rk.bearing >= 0 else -90
        act = {"turn": steer(view, juke), "thrust": "forward"}
        if s.special_ready:
            act["special"] = True
        return act

    # --- 3. Main fight: face the target, manage the range band. --------------
    act = {"turn": steer(view, aim)}

    if target.dist > s.weapon_range:
        act["thrust"] = "forward"            # too far: close in
    elif target.dist < s.weapon_range * 0.35:
        act["thrust"] = "back"               # too close: keep my range, don't get rammed

    # Fire the laser when lined up, in range, and the gun is cool.
    if lined_up and target.dist <= s.weapon_range and can_fire_gun(view):
        act["fire"] = "laser"
    # Otherwise, when roughly lined up at mid-range, spend a rocket to FLIP them
    # — that sets up my kill window (see block 1). Save the last rocket.
    elif s.rocket_ready and s.rockets_left > 1 and abs(aim) < 12 and \
            s.weapon_range * 0.4 < target.dist < s.weapon_range * 1.6:
        act["fire"] = "rocket"

    # --- 4. Mine the ground behind me when something's chasing close. --------
    # Orbiting kiters eat these and flip — then block 1 takes over.
    if s.trap_ready and target.dist < 150:
        act["drop_trap"] = True

    return act
