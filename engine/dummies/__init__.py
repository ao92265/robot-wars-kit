"""Stock demo bots + shared helpers for them."""
import math


def heat_ok(view, cost=27):
    """Fire discipline: True when a shot won't hit the heat ceiling. The stock
    bots predate the heat mechanic and used to spam until the 40-tick forced
    vent — spending most of a match with a dead gun. Skipping the shot instead
    keeps them firing at a steady pace. (cost 27 = laser's 26 + margin; all
    stock loadouts run the default laser.)"""
    return view.self.heat + cost < 100


def kite_move(view, side, standoff=0.75, spiral=13.0):
    """Continuous kiting movement: a forward-only orbit that spirals the nose
    onto the target as the gun comes off cooldown, bows OUT when the enemy
    crowds in and IN when they run. Never reverses thrust — the stock bots'
    old back/forward band edges made them jitter in place whenever an enemy
    sat on the boundary. Returns the movement half of an action."""
    me, target = view.self, view.enemies[0]
    aim = target.bearing
    r = me.weapon_range
    if target.dist > r * 0.95:
        turn = aim                             # out of reach: close straight in
    elif target.dist < r * 0.35:
        turn = aim + 155 * side                # point-blank: break away at speed
    else:
        offset = min(90.0, me.cooldown * spiral) if heat_ok(view) else 95.0
        err = target.dist - r * standoff       # + too far -> tighten, - too close -> widen
        offset += max(-25.0, min(30.0, -err * 0.8))
        turn = aim + offset * side
    if near_edge(view):                        # cornered: swing back toward centre
        a = view.arena
        turn = math.degrees(math.atan2(a.height / 2 - me.y, a.width / 2 - me.x)) - me.heading
        turn = (turn + 180) % 360 - 180
    return {"turn": nav_turn(view, turn), "thrust": "forward"}


def near_edge(view, margin=110):
    """True if the bot's centre is within `margin` units of any arena edge.

    Kiting dummies use this to STOP back-pedalling into a wall — reversing away
    from the enemy is what drove them to camp the corners. When cornered they
    push forward (toward the enemy, who is nearer centre) instead, keeping the
    fight watchable in the middle of the arena.
    """
    s, a = view.self, view.arena
    return (s.x < margin or s.x > a.width - margin
            or s.y < margin or s.y > a.height - margin)


def _avoid_rects(view):
    """Rects the stock bots steer AROUND: solid walls plus the HP-dangerous floor
    zones (lava burns, pits hurt+slow). Water is deliberately NOT avoided — it only
    bogs you, so a bot may wade through to close or grab a crate. On a hazard-free
    map this is exactly the wall list, so open-field movement is byte-identical."""
    rects = [(wx, wy, ww, wh) for (wx, wy, ww, wh) in view.arena.walls]
    for hz in getattr(view.arena, "hazards", []):
        if hz.get("type") in ("lava", "pit"):
            rects.append((hz["x"], hz["y"], hz["w"], hz["h"]))
    return rects


def _blocked(view, heading_deg, reach):
    """Would a step along `heading_deg` run the bot into a wall or a dangerous
    hazard? Samples a couple of points ahead and tests them against the avoid
    rects (with margin)."""
    s, rad = view.self, math.radians(heading_deg)
    cx, cy = math.cos(rad), math.sin(rad)
    m = s.radius + 6
    rects = _avoid_rects(view)
    for d in (reach * 0.5, reach):
        px, py = s.x + cx * d, s.y + cy * d
        for (wx, wy, ww, wh) in rects:
            if wx - m <= px <= wx + ww + m and wy - m <= py <= wy + wh + m:
                return True
    return False


def nav_turn(view, want_turn):
    """Head toward `want_turn` (relative degrees) but veer around a wall that's
    dead ahead so the bot slides past instead of grinding into it. Picks whichever
    side (left/right) is clear. No wall ahead -> returns want_turn unchanged, so
    open-field movement is untouched."""
    s = view.self
    reach = s.radius * 2 + max(28, s.speed * 3)
    if not _blocked(view, s.heading, reach):
        return want_turn
    for probe in (35, -35, 60, -60, 90, -90):   # sweep for a clear heading
        if not _blocked(view, s.heading + probe, reach):
            return probe
    return want_turn                            # boxed in — just keep trying
