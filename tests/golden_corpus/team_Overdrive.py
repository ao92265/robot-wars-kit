# Submitted by: Overdrive
# ============================================================================
#  "MATADOR" v4 — small · shotgun · sprint
#
#  The idea: speed is armour, and patience is a weapon. In a crowd we play
#  VULTURE — keep the whole pack at arm's length and let them grind each
#  other down — committing only to flipped robots and near-dead stragglers.
#  One threat left? DUEL: hold the mid-range band where their shots are coin
#  flips and fish for flips. When the sudden-death ring closes, the fastest
#  bot alive takes the centre and mines it.
#
#  v3 lesson (cornered at tick 219): all movement flows through best_lane()
#  — sample 16 headings, score for pack separation, walls, CORNERS, mines,
#  rocket paths and the lava ring, drive the best one.
#
#  v4 lesson (the Trapper problem): mines beat us only when we drive at
#  things in straight lines. Now every direct drive — pounce, rocket pass,
#  dash-ram — checks the corridor for mines first and reroutes; a mine hit
#  that slows us triggers an immediate escape dash; and a parked enemy
#  (a trapper planting its garden) eats a rocket for its trouble.
# ============================================================================

LOADOUT = {"hp": 2, "speed": 6, "damage": 1, "range": 0, "special": 2,
           "agility": 1, "size": "small", "gun": "shotgun", "engine": "sprint"}

APPEARANCE = {"color": "#ff2e63", "shape": "speeder", "accent": "#ffd93d"}

import math

PROBE = 110         # how far ahead (units) we score each candidate lane

_prev_t = None      # duel target's last position — persists between ticks
_still = 0          # how many ticks that target has barely moved


def norm(a):
    """Wrap an angle difference into [-180, 180]."""
    return (a + 180) % 360 - 180


def seg_dist(px, py, ax, ay, bx, by):
    """Distance from point (px,py) to the segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy or 1.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def in_safe_box(view, x, y, pad=30):
    """Inside the sudden-death safe area (trivially true before it starts)."""
    c = view.arena.collapse
    if not c:
        return True
    return (c + pad < x < view.arena.width - c - pad and
            c + pad < y < view.arena.height - c - pad)


def wall_probe(view, turn):
    """Cheap reactive check for direct drives (pounces): swing around cover."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def mines_on_path(view, bearing, dist):
    """Any enemy mine within a 45-unit corridor of this straight drive?
    Unarmed ones count — they'll be live by the time we arrive."""
    me = view.self
    h = math.radians(me.heading + bearing)
    bx, by = me.x + math.cos(h) * dist, me.y + math.sin(h) * dist
    for mn in view.mines:
        if not mn.mine and seg_dist(mn.x, mn.y, me.x, me.y, bx, by) < 45:
            return True
    return False


def best_lane(view, threats, keep_band=None, attract=None):
    """Score 16 candidate headings and return the best as a relative turn.

    Higher score = better lane. The terms encode everything that has killed
    us or could: proximity to enemies, walls and ESPECIALLY corners, enemy
    mines anywhere along the lane (weighted double once the ring is live),
    projected rocket paths, the lava ring, plus a mild preference for lanes
    we can point at quickly. `attract=(x, y, w)` pulls lanes toward a goal
    (a flipped victim) without ever overriding the safety terms.
    """
    me = view.self
    W, H = view.arena.width, view.arena.height
    collapse = view.arena.collapse
    pad = me.radius + 6
    mine_w = 50 if collapse else 25
    best_turn, best_score = 0, -1e18

    for i in range(16):
        ang = i * 22.5
        rad = math.radians(ang)
        px, py = me.x + math.cos(rad) * PROBE, me.y + math.sin(rad) * PROBE
        score = 0.0

        # Open distance from every healthy enemy — the pack, not just one.
        for e in threats:
            score -= 60000.0 / (math.hypot(px - e.x, py - e.y) + 40)

        # Arena edges hurt; corners (two edges at once) are the death zone.
        mx, my = min(px, W - px), min(py, H - py)
        if mx < 70:
            score -= (70 - mx) * 30
        if my < 70:
            score -= (70 - my) * 30
        if mx < 110 and my < 110:
            score -= 4000

        # Cover walls block the lane entirely.
        for (wx, wy, ww, wh) in view.arena.walls:
            for f in (0.5, 1.0):
                qx = me.x + math.cos(rad) * PROBE * f
                qy = me.y + math.sin(rad) * PROBE * f
                if wx - pad <= qx <= wx + ww + pad and wy - pad <= qy <= wy + wh + pad:
                    score -= 6000
                    break

        # Never lane out of the sudden-death box; once it's live, drift centre.
        # Late-game (ring imminent) we drift centre-ward EARLY — take the
        # ground before the trapper settles on it.
        if collapse:
            if not in_safe_box(view, px, py, pad=40):
                score -= 50000
            score -= math.hypot(px - W / 2, py - H / 2) * 2.0
        elif view.tick > 600:
            score -= math.hypot(px - W / 2, py - H / 2) * 0.5

        # Enemy mines: judge the whole lane, not just its far end — a mine
        # 40 units ahead is worse than one 110 units ahead.
        for mn in view.mines:
            if not mn.mine:
                d = seg_dist(mn.x, mn.y, me.x, me.y, px, py)
                if d < 100:
                    score -= (100 - d) * mine_w

        # Projected rocket paths: dodging is just steering done early.
        for rk in view.incoming_rockets:
            spd2 = rk.vx * rk.vx + rk.vy * rk.vy or 1.0
            t = max(0.0, ((px - rk.x) * rk.vx + (py - rk.y) * rk.vy) / spd2)
            d = math.hypot(px - (rk.x + rk.vx * t), py - (rk.y + rk.vy * t))
            if d < 90:
                score -= (90 - d) * 40

        # Duel mode: prefer lanes that hold the target in the fighting band.
        if keep_band and threats:
            lo, hi = keep_band
            d = math.hypot(px - threats[0].x, py - threats[0].y)
            if d < lo:
                score -= (lo - d) * 12
            elif d > hi:
                score -= (d - hi) * 6

        # Goal pull (e.g. a flipped victim) — never outranks the safety terms.
        if attract:
            gx, gy, w = attract
            score -= math.hypot(px - gx, py - gy) * w

        # Mild preference for lanes we can actually point at soon.
        rel = norm(ang - me.heading)
        score -= abs(rel) * 2

        if score > best_score:
            best_score, best_turn = score, rel
    return best_turn


def decide(view):
    global _prev_t, _still
    me = view.self

    if me.flipped:
        return {}                                   # helpless — wait it out

    threats = [e for e in view.enemies if not e.flipped]
    crowd = [e for e in threats if e.dist < 420]
    crowded = len(crowd) >= 2
    gun_ok = not me.jammed and not me.overheated and me.heat <= 68
    collapse = view.arena.collapse

    # -- 1 · PINNED: enemy on top of us with a wall at our back. Mine the
    #        doorstep and burst out along the least-bad lane.
    if threats and threats[0].dist < 90 and (
            min(me.x, view.arena.width - me.x) < 100 or
            min(me.y, view.arena.height - me.y) < 100):
        act = {"thrust": "forward", "turn": best_lane(view, threats)}
        if me.special_ready:
            act["special"] = True
        if me.trap_ready:
            act["drop_trap"] = True
        return act

    # -- 2 · SLOWED (we hit a mine / waded into something): speed is our
    #        armour and it's gone — dash clear before anything else.
    if me.slowed and threats and threats[0].dist < 300 and me.special_ready:
        return {"thrust": "forward", "turn": best_lane(view, threats),
                "special": True}

    # -- 3 · Rocket inbound: react EARLY — a flip in a crowd is most of our HP.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 220:
        act = {"thrust": "forward", "turn": best_lane(view, threats)}
        if me.special_ready and view.incoming_rockets[0].dist < 140:
            act["special"] = True
        return act

    # -- 4 · PUNISH: someone is wheels-up nearby. In a crowd, only if close.
    #        NEVER charge across mined ground for it — detour via best_lane
    #        with the victim as a magnet (the trapper's favourite bait).
    reach = 160 if crowded else 320
    pounce = next((e for e in view.enemies if e.flipped and e.dist < reach), None)
    if pounce is None and not threats and view.enemies:
        pounce = view.enemies[0]                    # everyone's flipped: buffet
    if pounce and not in_safe_box(view, pounce.x, pounce.y):
        pounce = None                               # let the lava have them
    if pounce:
        clean = not mines_on_path(view, pounce.bearing, pounce.dist)
        if clean:
            act = {"thrust": "forward", "turn": wall_probe(view, pounce.bearing)}
            if me.special_ready and pounce.dist > 130:
                act["special"] = True               # pounce
        else:
            act = {"thrust": "forward",             # circle in around the mines
                   "turn": best_lane(view, threats,
                                     attract=(pounce.x, pounce.y, 3.0))}
        if me.rocket_ready and pounce.dist > 90 and abs(pounce.bearing) < 10:
            act["fire"] = "rocket"                  # flies over the garden
        elif gun_ok and pounce.dist <= me.weapon_range \
                and abs(pounce.bearing) < me.weapon_arc:
            act["fire"] = "laser"                   # point-blank shotgun
        if me.trap_ready and pounce.dist < 60:
            act["drop_trap"] = True                 # wake-up present: may re-flip
        return act

    # -- 5 · SUDDEN DEATH: the ring funnels everyone to the centre — get
    #        there first (we're the fastest thing alive) and mine the floor.
    if collapse:
        act = {"thrust": "forward", "turn": best_lane(view, threats)}
        cx, cy = view.arena.width / 2, view.arena.height / 2
        if me.trap_ready and math.hypot(me.x - cx, me.y - cy) < 130:
            act["drop_trap"] = True                 # the ring herds them onto it
        if threats:
            near = threats[0]
            if gun_ok and near.dist <= me.weapon_range \
                    and abs(near.bearing) < me.weapon_arc:
                act["fire"] = "laser"
            elif me.rocket_ready and 90 < near.dist < 300 and abs(near.bearing) < 8:
                act["fire"] = "rocket"
        return act

    # -- 6 · VULTURE: two-plus healthy enemies near. No dance partners —
    #        pick the lane that opens distance from the whole pack.
    if crowded:
        act = {"thrust": "forward", "turn": best_lane(view, threats)}
        nearest = crowd[0]
        if me.trap_ready and me.traps_left > 2 \
                and nearest.dist < 240 and abs(nearest.bearing) > 110:
            act["drop_trap"] = True                 # breadcrumb for the chaser
        if gun_ok and nearest.dist <= me.weapon_range \
                and abs(nearest.bearing) < me.weapon_arc:
            act["fire"] = "laser"                   # free shot on the way out
        return act

    # -- 7 · DUEL: one real threat left. Hold the 120–280 band where their
    #        shots are coin flips, and fish for the flip.
    target = threats[0] if threats else view.enemies[0]
    aim = target.bearing

    # A target that hasn't moved for ~10 ticks is parked — a trapper planting
    # its garden. Parked = can't-miss rocket, and a flip chance on a fat HP pool.
    if _prev_t and math.hypot(target.x - _prev_t[0], target.y - _prev_t[1]) < 4:
        _still += 1
    else:
        _still = 0
    _prev_t = (target.x, target.y)
    parked = _still >= 10

    act = {"thrust": "forward"}
    move = best_lane(view, threats, keep_band=(120, 280))

    pass_max = 400 if parked else 320
    if me.rocket_ready and 140 < target.dist < pass_max and abs(aim) < 35 \
            and not mines_on_path(view, aim, min(target.dist, pass_max)):
        move = wall_probe(view, aim)                # head-on rocket pass, clean lane
        if abs(aim) < 8:
            act["fire"] = "rocket"
    if me.trap_ready and me.traps_left > 2 and target.dist < 220 and abs(aim) > 110:
        act["drop_trap"] = True                     # they're chasing: mine the path
    if target.hp < 25 and target.dist < 220 and abs(aim) < 25 and me.special_ready \
            and not mines_on_path(view, aim, target.dist):
        move = wall_probe(view, aim)                # dash-ram the finisher
        act["special"] = True
    if gun_ok and "fire" not in act and target.dist <= me.weapon_range \
            and abs(aim) < me.weapon_arc:
        act["fire"] = "laser"                       # free point-blank poke

    act["turn"] = move
    return act
