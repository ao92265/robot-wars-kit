# Submitted by: Servo
# ============================================================================
#  THREE EXPLOIT BUILDS IN ONE FILE — pick one by editing VARIANT below.
#
#    "undertaker"  ⇒ ring-execution flipper (DEFAULT / tournament pick)
#                    Tank-engine brawler that treats SUDDEN DEATH as the win
#                    condition: holds centre, mines the shrinking safe zone,
#                    then dash-rams / rocket-flips the enemy into the lava.
#                    2.5s flipped in no-mercy lava = kill. Tank engine means
#                    the mirror (them flipping US) barely works.
#
#    "matador"     ⇒ 20%-floor kiter + heat/jam baiter + flip-chainer.
#                    Max speed/agility small sprint laser: enemy shots run at
#                    the 20% hit floor and every miss still costs them heat +
#                    a jam roll. Punishes flips with point-blank (≈never miss)
#                    lasers and re-flip rockets.
#
#    "landlord"    ⇒ crate/terrain economy (big maps). Hover kites across
#                    hazards, contests the centre powerup spot (⚡ sits
#                    dead-centre on colosseum), mines its doorstep, presses
#                    only while a powerup timer is live.
#
#  All three: fire discipline (never force-VENT), jam fallback to rockets,
#  flipped-enemy execution, sudden-death ring awareness.
# ============================================================================

VARIANT = "undertaker"          # "undertaker" | "matador" | "landlord"

_BUILDS = {
    # 12 points each, max 6 per stat — legal per CUSTOMIZE.md §1.
    "undertaker": {"hp": 4, "speed": 4, "special": 3, "agility": 1,
                   "size": "large", "gun": "shotgun", "engine": "tank"},
    "matador":    {"speed": 6, "damage": 1, "special": 1, "agility": 4,
                   "size": "small", "gun": "laser", "engine": "sprint"},
    "landlord":   {"hp": 3, "speed": 4, "range": 3, "special": 2,
                   "size": "medium", "gun": "laser", "engine": "hover"},
}
_LOOKS = {
    "undertaker": {"color": "#8b1e1e", "shape": "spike",   "accent": "#ffb347"},
    "matador":    {"color": "#f2f2f2", "shape": "speeder", "accent": "#d4380d"},
    "landlord":   {"color": "#2e7d32", "shape": "orb",     "accent": "#ffd93d"},
}

LOADOUT = _BUILDS[VARIANT]
APPEARANCE = _LOOKS[VARIANT]

import math

# Heat added per trigger pull (CUSTOMIZE §2). Never fire past 100 - cost:
# force-venting hands the enemy a free 2s window — we give away nothing.
_HEAT_COST = {"laser": 22, "shotgun": 30, "cannon": 45}


# ---------------------------------------------------------------- helpers ---

def _steer(view, turn):
    """Wall-aware steering probe (from examples/) — walls block movement."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def _rel_bearing(view, tx, ty):
    """Relative bearing (deg) from our heading to point (tx, ty)."""
    ang = math.degrees(math.atan2(ty - view.self.y, tx - view.self.x))
    return (ang - view.self.heading + 180.0) % 360.0 - 180.0


def _centre(view):
    return view.arena.width / 2.0, view.arena.height / 2.0


def _dist_to(view, tx, ty):
    return math.hypot(tx - view.self.x, ty - view.self.y)


def _cool_enough(view):
    """Fire discipline: stay under the vent ceiling with margin to spare."""
    cost = _HEAT_COST.get(view.self.gun, 45)
    return (not view.self.overheated) and view.self.heat <= 100 - cost - 3


def _can_shoot(view, target, arc_frac=1.0):
    me = view.self
    return (not me.jammed and _cool_enough(view)
            and target.dist <= me.weapon_range
            and abs(target.bearing) < me.weapon_arc * arc_frac)


def _near_ring(view, x, y, margin):
    """Is (x, y) within `margin` of the sudden-death lava edge?"""
    c = view.arena.collapse
    if c <= 0:
        return False
    return (x < c + margin or x > view.arena.width - c - margin or
            y < c + margin or y > view.arena.height - c - margin)


def _dodge_rockets(view, trigger=140):
    if view.incoming_rockets and view.incoming_rockets[0].dist < trigger:
        act = {"turn": 90, "thrust": "forward"}
        if view.self.special_ready:
            act["special"] = True
        return act
    return None


def _execute_flipped(view, target):
    """A flipped enemy is stationary: point-blank shots ≈ never miss.
    Close in and unload; re-flip with a rocket from safe splash range."""
    me = view.self
    act = {"turn": target.bearing, "thrust": "forward"}
    if me.special_ready and target.dist > 150:
        act["special"] = True                       # dash to the body
    if (me.rocket_ready and 140 < target.dist < 350
            and abs(target.bearing) < 8):
        act["fire"] = "rocket"                      # re-flip as they right
    elif _can_shoot(view, target):
        act["fire"] = "laser"                       # guaranteed hits
    return act


# ------------------------------------------------------------- undertaker ---

def _undertaker(view):
    me = view.self
    c = view.arena.collapse
    cx, cy = _centre(view)

    if not view.enemies:
        if c > 0:
            return {"turn": _steer(view, _rel_bearing(view, cx, cy)),
                    "thrust": "forward"}
        return {"turn": 10, "thrust": "forward"}

    t = view.enemies[0]
    aim = t.bearing

    if t.flipped:                                   # kill window — GO
        return _execute_flipped(view, t)

    # ---- SUDDEN DEATH: the whole build ----
    if c > 0:
        margin = me.radius * 3
        if _near_ring(view, me.x, me.y, margin):    # never burn ourselves
            return {"turn": _steer(view, _rel_bearing(view, cx, cy)),
                    "thrust": "forward"}
        if _near_ring(view, t.x, t.y, margin * 1.5):
            # Execution: ram them outward / rocket-flip them at the edge.
            act = {"turn": aim, "thrust": "forward"}
            if me.special_ready and 60 < t.dist < 320:
                act["special"] = True               # dash-ram
            if me.rocket_ready and abs(aim) < 8 and 130 < t.dist < 420:
                act["fire"] = "rocket"
            elif _can_shoot(view, t):
                act["fire"] = "laser"
            return act
        # Hold the centre; ring it with the mines we banked all match.
        cd = _dist_to(view, cx, cy)
        if cd > 70:
            act = {"turn": _steer(view, _rel_bearing(view, cx, cy)),
                   "thrust": "forward"}
        else:
            act = {"turn": aim}
            if me.trap_ready and view.tick % 25 == 0:
                act["drop_trap"] = True             # enemies MUST path here
        if _can_shoot(view, t):
            act["fire"] = "laser"
        return act

    # ---- Normal phase: patient tank shotgun brawler (bank mines/rockets) ----
    act = {"turn": aim}
    if view.incoming_rockets and view.incoming_rockets[0].dist < 90:
        act["thrust"] = "forward"                   # tank: walk THROUGH it
    if t.dist > me.weapon_range * 0.9:
        act["turn"] = _steer(view, aim)
        act["thrust"] = "forward"
        if me.special_ready and 140 < t.dist < 360 and abs(aim) < 20:
            act["special"] = True                   # dash-ram: heavy bulldozes
    if _can_shoot(view, t):
        act["fire"] = "laser"
    elif me.jammed and me.rocket_ready and abs(aim) < 8 and 140 < t.dist < 420:
        act["fire"] = "rocket"                      # jam plan B
    return act


# ---------------------------------------------------------------- matador ---

def _matador(view):
    me = view.self
    c = view.arena.collapse
    cx, cy = _centre(view)

    dodge = _dodge_rockets(view, 160)               # small chassis flips easy
    if dodge:
        return dodge

    if not view.enemies:
        return {"turn": 14, "thrust": "forward"}

    t = view.enemies[0]
    aim = t.bearing

    # Sudden death: our HP is paper — safety before style.
    if c > 0 and _near_ring(view, me.x, me.y, me.radius * 4):
        act = {"turn": _steer(view, _rel_bearing(view, cx, cy)),
               "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        return act

    if t.flipped:
        return _execute_flipped(view, t)

    # Anti-ram: never let closing speed spike at contact range. Peel + mine.
    if t.dist < 110:
        act = {"turn": _steer(view, aim + 100), "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        if me.trap_ready:
            act["drop_trap"] = True                 # chaser eats the breadcrumb
        return act

    band = me.weapon_range
    if me.cooldown > 0 or me.jammed or not _cool_enough(view):
        # Gun busy: WEAVE. Constant lateral motion keeps their hit chance
        # pinned at the 20% floor while their heat climbs and jam dice roll.
        offset = 35 if (view.tick // 12) % 2 == 0 else -35
        act = {"turn": _steer(view, aim + offset), "thrust": "forward"}
        if t.dist < band * 0.5:
            act = {"turn": _steer(view, aim + 130), "thrust": "forward"}
        if me.jammed and me.rocket_ready and abs(aim) < 8 and 140 < t.dist < 420:
            act["turn"] = aim
            act["fire"] = "rocket"                  # jam plan B
        return act

    # Gun ready: line up CLEANLY (tiny turn = no snap-shot penalty), then fire.
    act = {"turn": aim}
    if t.dist > band * 0.95:
        act["turn"] = _steer(view, aim)
        act["thrust"] = "forward"
    if t.dist <= band and abs(aim) < min(10, me.weapon_arc):
        act["fire"] = "laser"
        act["thrust"] = "forward"                   # never a stationary target
    return act


# --------------------------------------------------------------- landlord ---

def _landlord(view):
    me = view.self
    c = view.arena.collapse
    cx, cy = _centre(view)
    centre_bearing = _rel_bearing(view, cx, cy)
    centre_dist = _dist_to(view, cx, cy)

    dodge = _dodge_rockets(view, 150)
    if dodge:
        return dodge

    # Sudden death: hover burns in lava like everyone else — centre, now.
    if c > 0 and _near_ring(view, me.x, me.y, me.radius * 4):
        act = {"turn": _steer(view, centre_bearing), "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        return act

    if not view.enemies:
        # Rotate onto the crate spot (⚡ sits dead-centre on colosseum);
        # crates respawn, so the spot is worth owning all match.
        if centre_dist > 50:
            return {"turn": _steer(view, centre_bearing), "thrust": "forward"}
        act = {"turn": 20}
        if me.trap_ready and view.tick % 30 == 0:
            act["drop_trap"] = True                 # mine our doorstep
        return act

    t = view.enemies[0]
    aim = t.bearing

    if t.flipped:
        return _execute_flipped(view, t)

    band = me.weapon_range
    powered = bool(me.overdrive or me.haste or me.shield)
    act = {"turn": aim}

    if t.dist > band:
        if powered:                                  # press while the timer runs
            act["turn"] = _steer(view, aim)
            act["thrust"] = "forward"
        else:                                        # otherwise contest the crate
            act["turn"] = _steer(view, centre_bearing if centre_dist > 60 else aim)
            act["thrust"] = "forward"
    elif t.dist < band * 0.55 and not powered:
        # They're closing: peel ACROSS hazards wheels can't follow through.
        act["thrust"] = "back"
        if me.special_ready:
            act = {"turn": _steer(view, aim + 180), "thrust": "forward",
                   "special": True}
        if me.trap_ready:
            act["drop_trap"] = True                  # breadcrumb the chaser
    if _can_shoot(view, t):
        act["fire"] = "laser"
    elif me.jammed and me.rocket_ready and abs(aim) < 8 and 140 < t.dist < 420:
        act["fire"] = "rocket"
    return act


# --------------------------------------------------------------- dispatch ---

_BRAINS = {"undertaker": _undertaker, "matador": _matador, "landlord": _landlord}


def decide(view):
    """Called once per tick. Must be fast and must never raise."""
    try:
        if view.self.flipped:
            return {}                                # helpless: save the tick
        return _BRAINS[VARIANT](view)
    except Exception:
        return {"turn": 10}                          # never idle silently
