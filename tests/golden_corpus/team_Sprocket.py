# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python arena.py            (watch it fight the dummies)
#  Check it: python arena.py --check     (is my loadout legal?)
#  See GUIDELINES.md / CUSTOMIZE.md for the full view + action reference.
#
#  BUILD — "Ghost": a glass laser KITER.  small + laser + sprint, 12 pts into
#  range(5) + speed(4) + a dash.  ~155 HP (fragile), ~27.6 speed (top-tier),
#  410 reach, 13 dmg/hit (low).  The plan: never be where they're shooting.
#  Live at the far edge of our reach, poke with heat-safe discipline, and dash
#  the gap open the instant anything closes.  We win by not getting hit.
#  Loses to: anything that corners us, and the sudden-death clock (low DPS) —
#  so we hold centre and never reverse into a wall.
# ============================================================================

LOADOUT = {"hp": 1, "speed": 4, "damage": 1, "range": 5, "special": 1, "agility": 0,
           "size": "small", "gun": "laser", "engine": "sprint"}

# Branding — cosmetic only. Fast bar-spinner in electric cyan / hot magenta.
APPEARANCE = {"color": "#2de1ff", "shape": "speeder", "accent": "#ff3ca6"}

import math


# ---- angle helpers ---------------------------------------------------------

def _norm(a):
    """Wrap degrees to [-180, 180] so we always turn the SHORT way."""
    if a > 180 or a < -180:
        a = (a + 180) % 360 - 180
    return a


def _blend(a, b, wa):
    """Circular weighted blend of two headings (deg); wa = weight of a in [0,1]."""
    ar, br = math.radians(a), math.radians(b)
    x = math.cos(ar) * wa + math.cos(br) * (1 - wa)
    y = math.sin(ar) * wa + math.sin(br) * (1 - wa)
    if x == 0 and y == 0:
        return a
    return math.degrees(math.atan2(y, x))


# ---- wall / ring probes ----------------------------------------------------

def _probe_blocked(view, heading_deg, dist):
    """Is the point `dist` ahead along heading_deg unsafe? Walls, arena edges,
    AND the molten ring all count as solid — so a kiter never reverses into death."""
    s = view.self
    pad = s.radius + 4
    r = math.radians(heading_deg)
    px = s.x + math.cos(r) * dist
    py = s.y + math.sin(r) * dist
    W = getattr(view.arena, "width", 0)
    H = getattr(view.arena, "height", 0)
    if px < pad or px > W - pad or py < pad or py > H - pad:
        return True
    c = getattr(view.arena, "collapse", 0) or 0
    if c > 0:
        if px < c + pad or px > W - c - pad or py < c + pad or py > H - c - pad:
            return True
    for w in (getattr(view.arena, "walls", None) or []):
        if w[0] - pad <= px <= w[0] + w[2] + pad and w[1] - pad <= py <= w[1] + w[3] + pad:
            return True
    return False


def _wall_ahead(view, heading_deg, dist):
    """Walls-only ray check (lasers/rockets are wall-stopped) so we never waste a
    poke — or a rationed rocket — into a wall. Ring/edges don't block shots."""
    walls = getattr(view.arena, "walls", None) or []
    if not walls or dist <= 0:
        return False
    s = view.self
    pad = s.radius + 4
    r = math.radians(heading_deg)
    for i in range(1, 5):
        d = dist * i / 4.0
        px = s.x + math.cos(r) * d
        py = s.y + math.sin(r) * d
        for w in walls:
            if w[0] - pad <= px <= w[0] + w[2] + pad and w[1] - pad <= py <= w[1] + w[3] + pad:
                return True
    return False


def steer(view, turn):
    """Wall/ring-aware steer: probe ~80 ahead along heading+turn; if the lane is
    unsafe, swing to the nearest clear heading. Retreat lands in OPEN space."""
    h = view.self.heading
    if not _probe_blocked(view, h + turn, 80.0):
        return _norm(turn)
    for extra in (55, -55, 90, -90, 130, -130, 180):
        if not _probe_blocked(view, h + turn + extra, 80.0):
            return _norm(turn + extra)
    return _norm(turn)  # fully boxed; keep intent, engine clamps


# ---- light closing detector (arms the head-on rocket) ----------------------

_PREV = {"d": None, "t": -99}


def _closing(view, dist):
    """True when the enemy has measurably charged in since last tick."""
    pd, pt = _PREV["d"], _PREV["t"]
    _PREV["d"], _PREV["t"] = dist, view.tick
    if pd is None or view.tick - pt != 1:
        return False
    return dist < pd - 3.0


# ---- brain -----------------------------------------------------------------

def decide(view):
    try:
        return _decide(view)
    except Exception:
        return {}  # never crash-idle on a bug; doing nothing is the safe floor


def _decide(view):
    s = view.self
    wr = s.weapon_range if getattr(s, "weapon_range", 0) and s.weapon_range > 1 else 410.0
    arc = s.weapon_arc if getattr(s, "weapon_arc", 0) else 16.0
    W = getattr(view.arena, "width", 0)
    H = getattr(view.arena, "height", 0)
    cx, cy = W * 0.5, H * 0.5
    collapse = getattr(view.arena, "collapse", 0) or 0

    # 1) ROCKET DODGE FIRST, ALWAYS. Break perpendicular to the rocket's track,
    #    toward open centre, and burn the dash. Nothing else matters this tick.
    rk = view.incoming_rockets
    if rk:
        nr = min(rk, key=lambda r: getattr(r, "dist", 1e9))
        if getattr(nr, "dist", 1e9) < 150:
            vx, vy = getattr(nr, "vx", 0.0), getattr(nr, "vy", 0.0)
            if vx or vy:
                ang = math.degrees(math.atan2(vy, vx))       # rocket travel dir
                o1, o2 = _norm(ang + 90), _norm(ang - 90)     # the two side-steps
                c = math.degrees(math.atan2(cy - s.y, cx - s.x))
                goal = o1 if abs(_norm(o1 - c)) <= abs(_norm(o2 - c)) else o2
                jt = steer(view, _norm(goal - s.heading))
            else:
                jt = steer(view, 85)
            act = {"turn": jt, "thrust": "forward"}
            if s.special_ready:
                act["special"] = True
            return act

    # 2) NO ENEMIES: drift back to centre if we've strayed, else rotate to scan.
    ens = view.enemies
    if not ens:
        if math.hypot(cx - s.x, cy - s.y) > 0.25 * (min(W, H) or 1):
            ct = _norm(math.degrees(math.atan2(cy - s.y, cx - s.x)) - s.heading)
            return {"turn": steer(view, ct), "thrust": "forward"}
        return {"turn": steer(view, 12)}

    e = ens[0]                     # enemies are sorted nearest-first
    dist = e.dist
    bearing = e.bearing
    frac = dist / wr
    closing = _closing(view, dist)

    center_turn = _norm(math.degrees(math.atan2(cy - s.y, cx - s.x)) - s.heading)
    away_turn = _norm(bearing + 180)   # straight away from the enemy (frame-free)

    # sudden-death: are we hugging the molten ring?
    near_ring = False
    if collapse > 0:
        m = 55.0
        near_ring = (s.x < collapse + m or s.x > W - collapse - m or
                     s.y < collapse + m or s.y > H - collapse - m)

    # escape trigger: inner-band breach, OR a fast closer we can still dash off
    # (that second clause IS the jink-on-threat rule — dash before they arrive).
    escape = frac < 0.5 or (frac < 0.62 and closing and s.special_ready)

    # 3) RANGE-BAND CONTROL -------------------------------------------------
    if escape:
        # DASH-RESET the gap. Retreat heading = away from enemy, biased to centre
        # (harder under the ring) so we open space without backing into a wall.
        wa = 0.35 if collapse > 0 else 0.7
        goal = _blend(away_turn, center_turn, wa)
        if s.special_ready:
            act = {"turn": steer(view, goal), "thrust": "forward", "special": True}
        elif not _probe_blocked(view, s.heading + 180, 80.0):
            act = {"turn": _norm(bearing), "thrust": "back"}   # reverse, keep aim for a parting poke
        else:
            act = {"turn": steer(view, goal), "thrust": "forward"}  # wall/ring behind: run to open
    elif frac <= 0.85:
        # IN BAND: face the enemy and hold aim; drift back to keep the gap wide.
        act = {"turn": _norm(bearing)}
        if collapse > 0 and near_ring:
            act = {"turn": steer(view, center_turn), "thrust": "forward"}   # ring wins
        elif frac < 0.8 and not _probe_blocked(view, s.heading + 180, 80.0):
            act["thrust"] = "back"                                          # widen, stay aimed
    else:
        # TOO FAR: drift in to regain the band; wall-aware, never over-commit.
        act = {"turn": steer(view, _norm(bearing)), "thrust": "forward"}
        if collapse > 0 and near_ring:
            act = {"turn": steer(view, center_turn), "thrust": "forward"}

    # 4/5) FIRE DISCIPLINE — the shot rides the CURRENT bearing, independent of
    #      the turn we queue for next tick. Suppress shots mid-dash / snap-turn.
    heat = getattr(s, "heat", 0) or 0
    planned = act.get("turn", 0.0)
    dashing = bool(act.get("special"))
    shot_clear = not _wall_ahead(view, s.heading, min(dist, wr))
    lined = abs(bearing) < arc * 0.8 and abs(planned) < arc * 0.75 and not dashing

    if (s.rocket_ready and abs(bearing) < 12 and 0.3 < frac < 0.9 and shot_clear
            and (frac < 0.6 or closing)):
        act["fire"] = "rocket"     # flip a head-on charger = a free poke window (rationed to 3)
    elif (lined and shot_clear and dist <= wr and frac <= 0.9 and s.cooldown == 0
            and not s.jammed and not s.overheated and heat < 78):
        act["fire"] = "laser"      # clean, heat-safe poke; heat<78 means we never self-vent
    return act
