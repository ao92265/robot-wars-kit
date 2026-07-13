"""GATEKEEPER — the house robot's brain. Locked engine file.

A neutral perimeter bruiser: patrols the arena corners on a deterministic tick
clock and CHARGES anything that strays inside its zone. It never fires a gun —
its weapon is mass (large chassis + tank engine + ramming physics). Stateless
and a pure function of the view, so replays stay byte-identical.
"""

import math

from . import config


def _turn_to(me, tx, ty):
    ang = math.degrees(math.atan2(ty - me.y, tx - me.x))
    return (ang - me.heading + 180.0) % 360.0 - 180.0


def decide(view):
    me = view.self
    # Intruder in the zone? CHARGE. Dash to close when it's worth it.
    if view.enemies and view.enemies[0].dist < config.HOUSE_ZONE:
        target = view.enemies[0]
        act = {"turn": target.bearing, "thrust": "forward"}
        if me.special_ready and target.dist > 140:
            act["special"] = True
        return act
    # Otherwise patrol the corners, clockwise on the tick clock — staying inside
    # the SUDDEN DEATH ring when the floor starts closing (it's a menace, not a
    # martyr).
    w, h = view.arena.width, view.arena.height
    inset_x = max(w * config.HOUSE_PATROL_MARGIN, view.arena.collapse + 60.0)
    inset_y = max(h * config.HOUSE_PATROL_MARGIN, view.arena.collapse + 60.0)
    pts = [(inset_x, inset_y), (w - inset_x, inset_y),
           (w - inset_x, h - inset_y), (inset_x, h - inset_y)]
    tx, ty = pts[(view.tick // config.HOUSE_WAYPOINT_TICKS) % 4]
    return {"turn": _turn_to(me, tx, ty), "thrust": "forward"}
