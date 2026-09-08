# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs sniper --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
#  See GUIDELINES.md for the full view + action reference.
#
#  "WASP" — evasive laser skirmisher. The plan: never stop moving (misses go up
#  vs a fast/agile target), hold a range band, throw rockets to FLIP, then charge
#  the helpless bot and delete it. Mid-size + smart beats big + dumb 7/10.
# ============================================================================

# 12 pts: hp2 speed3 damage3 range2 special1 agility1. Speed is the load-bearing
# stat: it feeds the miss formula (fast targets get missed), lets us kite heavies
# and disengage at will. Tank engine: barely flips, rights 2x fast, +HP — flips
# were our #1 loss mode in brawls. Laser: wide arc suits a moving gun platform.
LOADOUT = {"hp": 2, "speed": 3, "damage": 3, "range": 2, "special": 1,
           "agility": 1, "size": "medium", "gun": "laser", "engine": "tank"}

# Branding — cosmetic only.
APPEARANCE = {"color": "#3fd0c9", "shape": "speeder", "accent": "#ffd93d"}

import math

# Range band, as fractions of our weapon reach. Fight at arm's length: hit
# chance falls with distance for BOTH sides, but we never vent and undisciplined
# enemies do — long-range trades favour the cooler gun.
BAND_NEAR = 0.80   # closer than this: peel away (fire while backing)
BAND_FAR  = 1.00   # farther than this: close in


# Floor hazards we refuse to drive over (ice we tolerate — it just slides).
AVOID = ("lava", "pit", "flipper", "turntable", "water")


def hazard_at(view, x, y, pad=0):
    """The hazard rect covering point (x,y), or None."""
    for hz in view.arena.hazards:
        if (hz["x"] - pad <= x <= hz["x"] + hz["w"] + pad and
                hz["y"] - pad <= y <= hz["y"] + hz["h"] + pad):
            if hz["type"] in AVOID:
                return hz
    return None


def steer(view, turn):
    """Wall- and hazard-aware steering: probe 80 units ahead; if a wall or a
    nasty floor patch sits there, swing wide around it."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    if hazard_at(view, px, py, pad):
        return turn + 55
    return turn


def seg_blocked(view, x1, y1, x2, y2):
    """The wall rect sitting on the segment (x1,y1)-(x2,y2), or None."""
    dx, dy = x2 - x1, y2 - y1
    steps = max(2, int(math.hypot(dx, dy) / 25))
    for i in range(1, steps):
        px, py = x1 + dx * i / steps, y1 + dy * i / steps
        for w in view.arena.walls:
            (wx, wy, ww, wh) = w
            if wx <= px <= wx + ww and wy <= py <= wy + wh:
                return w
    return None


def blocking_wall(view, tx, ty):
    """The wall between us and (tx, ty), or None. Guns and rockets are
    wall-blocked — shooting cover is pure heat waste."""
    return seg_blocked(view, view.self.x, view.self.y, tx, ty)


def flank_point(view, wall, tx, ty):
    """A waypoint past a corner of `wall` that actually OPENS a firing line to
    (tx, ty) — nearest such corner by total detour. A close corner that leaves
    the wall in the way is a parking spot, not a flank."""
    (wx, wy, ww, wh) = wall
    m = view.self.radius + 30                     # clearance past the corner
    corners = [(wx - m, wy - m), (wx + ww + m, wy - m),
               (wx - m, wy + wh + m), (wx + ww + m, wy + wh + m)]
    me = view.self
    cost = lambda c: (math.hypot(c[0] - me.x, c[1] - me.y)
                      + math.hypot(c[0] - tx, c[1] - ty))
    open_corners = [c for c in corners if seg_blocked(view, c[0], c[1], tx, ty) is None]
    return min(open_corners or corners, key=cost)


def bearing_to(view, x, y):
    """Bearing to a world point, relative to our heading (0 = dead ahead)."""
    ang = math.degrees(math.atan2(y - view.self.y, x - view.self.x))
    return (ang - view.self.heading + 180) % 360 - 180


_first_seen = {}      # enemy id-ish (initial hp) bookkeeping across ticks


def _enemy_max_hp(view, target):
    """First hp we ever saw for the nearest enemy ≈ its max (we meet at full)."""
    if "hp0" not in _first_seen:
        _first_seen["hp0"] = target.hp
    return _first_seen["hp0"]


def decide(view):
    me = view.self
    if view.tick <= 1:
        _first_seen.clear()          # fresh match, fresh bookkeeping

    # 0) STANDING IN A HAZARD — get out before anything else (lava is 5 HP/tick
    #    and WILL kill; we once burned to death on our own spawn point).
    hz = hazard_at(view, me.x, me.y)
    if hz is not None:
        # Head straight away from the patch centre — shortest way out.
        cx, cy = hz["x"] + hz["w"] / 2, hz["y"] + hz["h"] / 2
        out = bearing_to(view, 2 * me.x - cx, 2 * me.y - cy)
        act = {"turn": out, "thrust": "forward"}
        if hz["type"] == "lava" and me.special_ready:
            act["special"] = True                     # burn dash, not HP
        return act

    # 1) DODGE — a rocket bearing down beats everything else. Juke perpendicular
    #    to it and burn the dash to clear the splash.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 150:
        r = view.incoming_rockets[0]
        act = {"turn": 90 if r.bearing >= 0 else -90, "thrust": "forward"}
        if me.special_ready:
            act["special"] = True
        return act

    # 2) SUDDEN DEATH — molten ring closing in. If we're near the edge of the
    #    safe box, sprint back to center; the ring kills, no mercy floor.
    if view.arena.collapse > 0:
        m = view.arena.collapse + me.radius + 20
        if (me.x < m or me.x > view.arena.width - m or
                me.y < m or me.y > view.arena.height - m):
            cx, cy = view.arena.width / 2, view.arena.height / 2
            act = {"turn": steer(view, bearing_to(view, cx, cy)), "thrust": "forward"}
            if me.special_ready:
                act["special"] = True
            return act

    # 3) No target in sight — prowl and scan (never sit still).
    if not view.enemies:
        return {"turn": steer(view, 18), "thrust": "forward"}

    target = view.enemies[0]        # nearest first
    aim = target.bearing
    rng = me.weapon_range
    act = {"turn": aim}
    wall = blocking_wall(view, target.x, target.y)

    # Fire discipline: hitting 100 heat force-vents (~2s helpless = free hits for
    # the enemy). Per-shot heat varies by gun and damage stat, so LEARN it: watch
    # our own heat jump after each shot, then never pull the trigger unless the
    # worst observed jump still keeps us under the ceiling.
    if me.heat > _first_seen.get("heat_prev", 0) + 5:
        jump = me.heat - _first_seen["heat_prev"]
        _first_seen["heat_jump"] = max(jump, _first_seen.get("heat_jump", 0))
    _first_seen["heat_prev"] = me.heat
    budget = 93 - _first_seen.get("heat_jump", 30)
    can_laser = (not me.overheated) and me.heat <= budget
    lined = abs(aim) < me.weapon_arc

    # THE STALL: against a HEAVIER bot (out-trades us head-on), a straight fight
    # is a loss — but a time-cap with a damage lead is a WIN. Once we're ahead
    # on hp fraction, keep a wall between us until the clock or the ring
    # decides it. Never triggers on lighter bots — those we just kill.
    heavy = _enemy_max_hp(view, target) > me.max_hp
    leading = (target.hp / _enemy_max_hp(view, target)) < (me.hp / me.max_hp)
    if heavy and leading and view.arena.walls and view.arena.collapse == 0:
        if wall is not None:
            # Cover is holding — orbit AWAY from the corner they'd round.
            fx, fy = flank_point(view, wall, target.x, target.y)
            away = bearing_to(view, fx, fy) + 180
            return {"turn": steer(view, away), "thrust": "forward"}
        # No cover: sprint to put the nearest wall between us and them.
        wx, wy, ww, wh = min(view.arena.walls,
                             key=lambda w: math.hypot(w[0] + w[2] / 2 - me.x,
                                                      w[1] + w[3] / 2 - me.y))
        cx, cy = wx + ww / 2, wy + wh / 2
        hx, hy = cx + (cx - target.x) * 0.35, cy + (cy - target.y) * 0.35
        act = {"turn": steer(view, bearing_to(view, hx, hy)), "thrust": "forward"}
        if me.special_ready and target.dist < 200:
            act["special"] = True
        return act

    # 4) WALL BETWEEN US — never trade with cover: shots are heat wasted into
    #    brick. COMMIT to one corner and drive all the way around it — re-picking
    #    every tick just orbits the wall in a forever-dance with the enemy.
    if wall is not None:
        goal = _first_seen.get("flank")
        if goal is None:
            goal = flank_point(view, wall, target.x, target.y)
            _first_seen["flank"] = goal
        if math.hypot(goal[0] - me.x, goal[1] - me.y) < 35:
            _first_seen.pop("flank", None)     # corner reached, re-plan next tick
        act["turn"] = steer(view, bearing_to(view, goal[0], goal[1]))
        act["thrust"] = "forward"
        return act
    _first_seen.pop("flank", None)             # line is open — forget the detour

    # 4a) HIT-AND-RUN vs heavies we can outrun: dart in, ONE clean shot, then
    #    retreat beyond their reach while our gun cycles. They only get the few
    #    ticks we're inside their range — we get every shot we take.
    if heavy and not target.flipped:
        ready = can_laser and me.cooldown == 0
        if ready:
            act["turn"] = steer(view, aim)
            act["thrust"] = "forward"
            if target.dist <= rng and abs(aim) < me.weapon_arc:
                # Rockets are extra damage on the same heat budget — a big slow
                # hull can't dodge them. Spend them; the laser covers the rest.
                if me.rocket_ready and target.dist > 150:
                    act["fire"] = "rocket"
                else:
                    act["fire"] = "laser"     # poke lands — next tick we peel away
            return act
        # Gun cycling: open the gap so their answer whiffs or never comes.
        act["turn"] = steer(view, aim + 180)
        act["thrust"] = "forward"
        if me.special_ready and target.dist < 260:
            act["special"] = True
        if me.trap_ready and target.dist < 200:
            act["drop_trap"] = True       # a present, if it chases
        return act

    # 4b) FLIPPED ENEMY = free damage. Drop the dance, charge in, unload.
    if target.flipped:
        act["turn"] = steer(view, aim)
        act["thrust"] = "forward"
        if target.dist <= rng and abs(aim) < me.weapon_arc and not me.overheated:
            act["fire"] = "laser"
        return act

    # 5) RANGE-BAND KITING — keep them in the sweet spot, always moving laterally
    #    so their shots miss.
    if target.dist > rng * BAND_FAR:
        act["turn"] = steer(view, aim)                 # too far: close in
        act["thrust"] = "forward"
    elif target.dist < rng * BAND_NEAR:
        # Too close: back-pedal WHILE FIRING (gun stays on target), and seed a
        # mine at our feet — they have to drive over it to reach us.
        act["thrust"] = "back"
        # Yes, even glued: the blast slows us a little but can FLIP them —
        # a flipped chaser is 2.5s of free damage. Observed net-positive.
        if me.trap_ready:
            act["drop_trap"] = True
        if me.special_ready and target.dist < rng * 0.35:
            act["turn"] = steer(view, aim + 150)   # point-blank: dash out
            act["thrust"] = "forward"
            act["special"] = True
    else:
        # In the band. Gun ready → settle the aim dead-on and take the shot
        # (weaving with a narrow arc means never firing). Gun cycling → weave
        # to stay a hard target; the reload is our dodge window.
        if can_laser and me.cooldown == 0:
            act["turn"] = aim
        else:
            # Unpredictable jink (seeded rng) — a fixed-period weave synchronises
            # with the enemy's fire cycle and they hit every single shot.
            if view.tick >= _first_seen.get("jink_until", 0):
                _first_seen["jink"] = (view.rng.random() - 0.5) * 90
                _first_seen["jink_until"] = view.tick + view.rng.randint(8, 26)
            act["turn"] = aim + _first_seen.get("jink", 0)
        act["thrust"] = "forward"

    # 6) FIRE. A rocket to try for a flip when we're lined up at flip range
    #    (not point-blank — splash bites us too); otherwise the laser.
    if me.rocket_ready and can_laser and 90 < target.dist < rng * 1.3 and abs(aim) < 8:
        act["fire"] = "rocket"        # rockets heat the gun too (observed)
    elif target.dist <= rng and lined and can_laser:
        act["fire"] = "laser"

    # 7) Being chased (enemy close and behind us)? Leave a mine in our wake —
    #    it hurts, slows, and can flip whoever's on our tail.
    if me.trap_ready and target.dist < 160 and abs(aim) > 120:
        act["drop_trap"] = True

    return act
