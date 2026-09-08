# ============================================================================
#  "MATADOR" — mid-size laser duelist. The cape, not the bull.
#  · Lives at 60–95% of its own laser reach, orbiting so enemy shots eat the
#    motion penalty — being unhittable IS the armour.
#  · Fire discipline: never trips the 100-heat forced vent.
#  · The instant an enemy is FLIPPED: charge, rocket, unload. Toro.
#  · Dodges rockets with a perpendicular juke + dash; mines the feet of
#    anything that chases; gives ground to the sudden-death ring early.
#  Run it:   python arena.py            Check it: python arena.py --check
# ============================================================================

# 12 points: enough gun to matter, enough legs to never be caught.
LOADOUT = {"hp": 2, "speed": 4, "damage": 2, "range": 2, "special": 1,
           "agility": 1, "size": "medium", "gun": "laser", "engine": "sprint"}

# Matador colours: blood red, gold trim, bar spinner for the cape swish.
APPEARANCE = {"color": "#d7263d", "shape": "speeder", "accent": "#ffd93d"}

import math

HEAT_PER_SHOT = 22          # laser heat per trigger pull (CUSTOMIZE §2)
HEAT_CEILING = 100          # hit this and the gun force-vents ~2s

_mem = {"last_dist": None, "orbit": 1}


def _norm(a):
    """Wrap an angle to [-180, 180]."""
    while a > 180:
        a -= 360
    while a < -180:
        a += 360
    return a


def _steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def _dodge_mines(view, turn):
    """Veer off if an armed enemy mine sits roughly where we're headed."""
    for m in view.mines:
        if (not m.mine) and m.armed and m.dist < 95 and abs(_norm(m.bearing - turn)) < 35:
            return turn + 60
    return turn


def _course(view, turn):
    return _steer(view, _dodge_mines(view, turn))


def _to_centre(view):
    """Bearing from our nose to the arena centre."""
    ang = math.degrees(math.atan2(view.arena.height / 2 - view.self.y,
                                  view.arena.width / 2 - view.self.x))
    return _norm(ang - view.self.heading)


def _ring_pressure(view):
    """Sudden death: True if the molten ring is uncomfortably close."""
    c = view.arena.collapse
    if not c:
        return False
    margin = c + view.self.radius + 45
    return (view.self.x < margin or view.self.y < margin
            or view.self.x > view.arena.width - margin
            or view.self.y > view.arena.height - margin)


def decide(view):
    me = view.self

    # 1 · Rocket bearing down: juke to its blind side and burn the dash.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 150:
        rk = view.incoming_rockets[0]
        side = 90 if rk.bearing <= 0 else -90
        act = {"turn": _course(view, side), "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        return act

    ring = _ring_pressure(view)

    if not view.enemies:
        if ring:
            return {"turn": _course(view, _to_centre(view)), "thrust": "forward"}
        return {"turn": 14, "thrust": "forward"}      # hunt — never sit still

    band = me.weapon_range

    # Prefer a nearby flipped enemy over merely the nearest — free damage first.
    target = view.enemies[0]
    for e in view.enemies[:3]:
        if e.flipped and e.dist < band * 1.3:
            target = e
            break
    aim = target.bearing

    # Is the nearest enemy closing on us? (mine-drop trigger)
    closing = _mem["last_dist"] is not None and view.enemies[0].dist < _mem["last_dist"] - 1
    _mem["last_dist"] = view.enemies[0].dist

    # Swap orbit direction now and then so we're not clockwork.
    if view.tick % 90 == 0:
        _mem["orbit"] = -_mem["orbit"]

    gun_ready = (not me.jammed and not me.overheated and me.cooldown == 0
                 and me.heat + HEAT_PER_SHOT < HEAT_CEILING)

    # 2 · KILL WINDOW — they're wheels-up. Charge and unload.
    if target.flipped and not ring:
        act = {"turn": aim, "thrust": "forward"}
        if me.rocket_ready and abs(aim) < 8 and 90 < target.dist < 320:
            act["fire"] = "rocket"                    # splash a helpless target
        elif gun_ready and target.dist <= band and abs(aim) < me.weapon_arc:
            act["fire"] = "laser"
        if me.special_ready and target.dist > 200:
            act["special"] = True                     # arrive before they right
        return act

    # 3 · Movement: the ring first, then hold the 60–95% range band, always moving.
    act = {"thrust": "forward"}
    if ring:
        act["turn"] = _course(view, _to_centre(view))
    elif target.dist > band * 0.95:
        act["turn"] = _course(view, aim)              # drift in
    elif target.dist < band * 0.60:
        if target.dist < 65:
            # Ram range: never stand in the bull's line. Hard sidestep out.
            act["turn"] = _course(view, _norm(aim + 120 * _mem["orbit"]))
            if me.special_ready:
                act["special"] = True
        elif closing:
            # PICADOR: it's charging in a straight line — the easiest target
            # in the game. Face it, back-pedal, empty everything into it:
            # rocket down the throat (can FLIP), laser between reloads, and
            # a mine line it has to cross to reach us.
            act["turn"] = aim
            act["thrust"] = "back"
            if me.rocket_ready and abs(aim) < 10 and target.dist > 100:
                act["fire"] = "rocket"
            elif gun_ready and abs(aim) < me.weapon_arc:
                act["fire"] = "laser"
            if me.trap_ready and target.dist > 70:
                act["drop_trap"] = True
        else:
            # Merely too close: open the gap on a diagonal, stay hard to hit.
            act["turn"] = _course(view, _norm(aim + 135 * _mem["orbit"]))
            if target.dist < band * 0.35 and me.special_ready:
                act["special"] = True
    elif gun_ready and target.dist <= band:
        act["turn"] = aim                             # line up the shot
    else:
        # Gun cycling/hot: orbit so their shots eat the motion penalty.
        act["turn"] = _course(view, _norm(aim + 75 * _mem["orbit"]))

    # 4 · Trigger: only lined up, in reach, and never tripping the vent.
    if ("fire" not in act and gun_ready and target.dist <= band
            and abs(aim) < min(me.weapon_arc, 14)):
        act["fire"] = "laser"

    # 5 · Gun out of action with company nearby: slide away, leave a present.
    if (me.jammed or me.overheated) and target.dist < 200 and me.trap_ready:
        act["drop_trap"] = True

    return act
