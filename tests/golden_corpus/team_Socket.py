# ============================================================================
#  "THE EXECUTIONER" — large tank-engine cannon rammer, with a matador mode.
#
#  Core idea: point-blank shots never miss, and flipped robots can't fight
#  back. Dash-ram to crunch + flip (mass splits ram damage by radius squared —
#  large chassis wins the trade), then execute at zero range with the cannon.
#  Tank engine barely flips and rights 2x fast, so the mirror tactic bounces.
#
#  Matador mode: against a BIGGER HP pool (read from their hp at first sight)
#  a straight slugfest is a losing trade — so refuse it. Bigger pool means
#  tank+large means slow: we're twice their speed. Stay outside their reach,
#  bleed them with rockets (slow bots don't dodge) and mines dropped in our
#  wake (they follow our path straight onto them). When their pool drops
#  below ours, the slugfest math has flipped — walk in and finish normally.
# ============================================================================

# 12 points: hp 2 + speed 5 + damage 4 + special 1 = 12.
#   effective HP  ~342 (230 x large 1.26 x tank 1.18)
#   cannon hit     ~56 (28 x cannon 2.0) — six clean hits end anyone
#   speed 5 + dash: enough legs to run down mid-speed bots for the ram
LOADOUT = {"hp": 2, "speed": 5, "damage": 4, "range": 0, "special": 1,
           "size": "large", "gun": "cannon", "engine": "tank"}

APPEARANCE = {"color": "#b3202a", "shape": "spike", "accent": "#f2c14e"}

import math

# Cannon heat: 45/shot, vent forced at 100. Below this we can always fire
# without triggering a vent.
SAFE_HEAT = 54

_foe_max_hp = 0     # biggest HP we've seen on the enemy = their pool size


def norm(deg):
    """Normalise an angle to [-180, 180]."""
    return (deg + 180) % 360 - 180


def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return norm(turn)


def bearing_to(view, x, y):
    """Degrees to turn (signed, normalised) to face a point."""
    ang = math.degrees(math.atan2(y - view.self.y, x - view.self.x))
    return norm(ang - view.self.heading)


def ring_margin(view):
    """Distance from us to the sudden-death ring edge (big = safe)."""
    c = view.arena.collapse
    if not c:
        return 9999
    return min(view.self.x - c, view.arena.width - c - view.self.x,
               view.self.y - c, view.arena.height - c - view.self.y)


def matador(view, target):
    """Refuse the brawl: stay out of reach, bleed them with rockets + mines."""
    me = view.self
    aim = target.bearing

    if target.dist < 300:                     # too close — open the gap
        act = {"turn": steer(view, aim + 180), "thrust": "forward"}
        if me.trap_ready:
            act["drop_trap"] = True           # they're following our wake
        if me.special_ready and target.dist < 240:
            act["special"] = True
        return act

    if me.rocket_ready and me.rockets_left > 0 and target.dist < 500:
        act = {"turn": aim, "thrust": "forward"}   # snap onto them
        if abs(aim) < 7:
            act["fire"] = "rocket"            # slow bots walk straight through
        return act

    # Hold the band: circle them just outside their reach.
    return {"turn": steer(view, aim + 100), "thrust": "forward"}


def decide(view):
    global _foe_max_hp
    me = view.self
    if view.tick < 3:
        _foe_max_hp = 0                       # fresh match, fresh book-keeping
    cx, cy = view.arena.width / 2, view.arena.height / 2

    # --- Sudden death: the ring does our chasing. Never get caught in it. ---
    if ring_margin(view) < me.radius + 50:
        turn = steer(view, bearing_to(view, cx, cy))
        act = {"turn": turn, "thrust": "forward"}
        if me.special_ready and abs(turn) < 30:
            act["special"] = True             # burn dash to escape fire
        return act

    if not view.enemies:
        # Hunt toward centre — best ram lanes, and where sudden death ends up.
        return {"turn": steer(view, bearing_to(view, cx, cy)), "thrust": "forward"}

    target = view.enemies[0]
    _foe_max_hp = max(_foe_max_hp, target.hp)
    aim = target.bearing

    # --- Matador mode: their pool beats ours in a slugfest, so don't slug —
    # kite and chip until the maths flips (or the ammo runs dry).
    outgunned = _foe_max_hp > me.max_hp * 1.02
    has_ammo = me.rockets_left > 0 or me.traps_left > 0
    if outgunned and target.hp > me.hp and not target.flipped and has_ammo:
        return matador(view, target)

    act = {"turn": steer(view, aim), "thrust": "forward"}   # always closing

    # --- Rockets: we barely flip and out-trade everyone — drive THROUGH it
    # toward the shooter rather than surrendering the ram line. Only sidestep
    # when it's far enough that dodging costs nothing.
    if view.incoming_rockets and view.incoming_rockets[0].dist > 220:
        act["turn"] = steer(view, aim + 25)

    # --- Dash-ram: commit when the gap is charge-sized and we're lined up.
    # The crunch splits by mass (we're the heaviest thing here) and can flip.
    if me.special_ready and abs(aim) < 15 and 90 < target.dist < 320:
        act["special"] = True

    # --- Fire discipline: the cannon only speaks when it can't miss. ---
    in_arc = abs(aim) < me.weapon_arc * 0.6
    point_blank = target.dist <= me.weapon_range * 0.7
    kill_shot = target.hp <= 56                       # one hit ends them
    no_vent = me.heat <= SAFE_HEAT
    if target.dist <= me.weapon_range and in_arc:
        if (point_blank or target.flipped) and (no_vent or kill_shot):
            act["fire"] = "laser"                     # fire the cannon

    # --- Kiter answer: they won't come to us, so make THEM dodge. A rocket
    # forces a juke (or a flip); either way they lose ground.
    elif (me.rocket_ready and me.rockets_left > 0
          and abs(aim) < 5 and 250 < target.dist < 500):
        act["fire"] = "rocket"

    # --- Mines: zone denial. Drop one when an orbiter slips behind us
    # (they're circling into it), or seed the centre once sudden death
    # starts and they're keeping their distance.
    if me.trap_ready:
        orbiter_behind = target.dist < 220 and abs(aim) > 100
        seeding_centre = (view.arena.collapse > 0 and target.dist > 300
                          and abs(bearing_to(view, cx, cy)) > 150)
        if orbiter_behind or seeding_centre:
            act["drop_trap"] = True

    return act
