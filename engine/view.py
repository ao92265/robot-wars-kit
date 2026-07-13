"""Builds the read-only `view` handed to each bot's decide(). Locked engine file.

Everything here is a fresh copy of primitives (numbers/strings) so a bot that
mutates its view cannot reach into and corrupt the live simulation.
"""

import math
from types import SimpleNamespace


def _bearing_deg(robot, tx, ty):
    """Relative angle from robot's heading to target point. 0 = dead ahead, + = to the right, range -180..180."""
    dx, dy = tx - robot.x, ty - robot.y
    absolute = math.degrees(math.atan2(dy, dx))
    rel = (absolute - robot.heading_deg + 180.0) % 360.0 - 180.0
    return rel


def build_view(robot, robots, arena, rockets, mines, pickups=()):
    """Construct an immutable-ish snapshot for one robot's decide()."""
    # Enemies exclude teammates (physics already blocks friendly fire — showing
    # allies as "enemies" made team bots chase/kite their own side). Teammates go
    # in a separate `allies` list so a bot can coordinate instead of colliding.
    enemies = []
    allies = []
    for other in robots:
        if other.id == robot.id or not other.alive:
            continue
        dx, dy = other.x - robot.x, other.y - robot.y
        dist = math.hypot(dx, dy)
        ns = SimpleNamespace(
            x=round(other.x, 2), y=round(other.y, 2),
            hp=other.hp, dist=round(dist, 2),
            bearing=round(_bearing_deg(robot, other.x, other.y), 2),
            flipped=(other.flipped_ticks > 0),   # wheels-up = free hits, go NOW
        )
        (allies if other.team == robot.team else enemies).append(ns)
    enemies.sort(key=lambda e: e.dist)
    allies.sort(key=lambda e: e.dist)

    # Incoming rockets you didn't fire — see them coming so you can dodge.
    incoming = []
    for rk in rockets:
        if rk.owner == robot.id:
            continue
        dx, dy = rk.x - robot.x, rk.y - robot.y
        incoming.append(SimpleNamespace(
            x=round(rk.x, 2), y=round(rk.y, 2),
            vx=round(rk.vx, 2), vy=round(rk.vy, 2),
            dist=round(math.hypot(dx, dy), 2),
            bearing=round(_bearing_deg(robot, rk.x, rk.y), 2),
        ))
    incoming.sort(key=lambda r: r.dist)

    # Mines on the field. `mine=True` if it's one of yours.
    mine_views = []
    for mn in mines:
        dx, dy = mn.x - robot.x, mn.y - robot.y
        mine_views.append(SimpleNamespace(
            x=round(mn.x, 2), y=round(mn.y, 2),
            mine=(mn.owner == robot.id), armed=mn.armed,
            dist=round(math.hypot(dx, dy), 2),
            bearing=round(_bearing_deg(robot, mn.x, mn.y), 2),
        ))
    mine_views.sort(key=lambda m: m.dist)

    # Map crates you can grab (active only). bearing/dist help you path to them.
    pickup_views = []
    for p in pickups:
        if not getattr(p, "active", True):
            continue
        dx, dy = p.x - robot.x, p.y - robot.y
        pickup_views.append(SimpleNamespace(
            x=round(p.x, 2), y=round(p.y, 2), kind=p.kind,
            dist=round(math.hypot(dx, dy), 2),
            bearing=round(_bearing_deg(robot, p.x, p.y), 2),
        ))
    pickup_views.sort(key=lambda p: p.dist)

    me = SimpleNamespace(
        x=round(robot.x, 2), y=round(robot.y, 2),
        heading=round(robot.heading_deg % 360.0, 2),
        hp=robot.hp, max_hp=robot.stats["max_hp"],
        team=robot.team,
        slowed=(robot.slow_ticks > 0),      # a trap (pit/mine) is dragging you
        flipped=(robot.flipped_ticks > 0),  # wheels-up (you can't act, but you can see)
        jammed=(robot.jam_ticks > 0),       # main gun jammed — rockets/mines still work
        heat=int(round(robot.heat)),        # gun heat 0..100; at 100 the gun force-vents
        overheated=(robot.vent_ticks > 0),  # venting: gun dead until it finishes
        overdrive=robot.od_ticks,           # powerup ticks left (0 = not active)
        shield=robot.shield_ticks,
        haste=robot.haste_ticks,
        radius=robot.stats["radius"], size=robot.stats["size"],
        gun=robot.stats["gun"], engine=robot.stats["engine"],
        turn_rate=robot.stats["turn_rate_deg"],
        speed=robot.stats["move_speed"],
        weapon_range=robot.stats["weapon_range"],
        weapon_arc=robot.stats["weapon_arc_deg"],
        cooldown=robot.cooldown,
        rockets_left=robot.rockets_left,
        traps_left=robot.traps_left,
        rocket_ready=(robot.rockets_left > 0 and robot.rocket_cd == 0),
        trap_ready=(robot.traps_left > 0 and robot.trap_cd == 0),
        special_ready=(robot.stats["special_level"] > 0 and robot.special_cd == 0),
    )
    return SimpleNamespace(
        self=me,
        enemies=enemies,
        allies=allies,
        incoming_rockets=incoming,
        mines=mine_views,
        arena=SimpleNamespace(width=arena.width, height=arena.height,
                              walls=[tuple(w) for w in arena.walls],
                              hazards=[dict(h) for h in getattr(arena, "hazards", [])],
                              # SUDDEN DEATH: molten ring this wide creeps in from every
                              # wall (0 = not started). Stay inside [collapse, w-collapse]!
                              collapse=round(arena.collapse_width(), 1)
                                       if hasattr(arena, "collapse_width") else 0.0),
        pickups=pickup_views,
        weather=SimpleNamespace(kind=getattr(arena, "weather", "clear"),
                                wind=tuple(getattr(arena, "wind", (0.0, 0.0)))),
        tick=arena.tick,
        rng=robot.rng,
    )
