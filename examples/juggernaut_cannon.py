"""EXAMPLE BUILD — "Bastion": cannon + tank engine, large chassis.

The idea: a walking siege gun. Huge HP pool wades through mines, pits and
water drag (tank engine shrugs slows off twice as fast); the cannon deletes a
quarter of a health bar per hit. Weakness: half the aim arc and double the
reload — a fast orbiting target makes you whiff, and every whiff hurts.

Copy anything here into your my_bot.py — LOADOUT, APPEARANCE, decide(), or all three.
"""

LOADOUT = {"hp": 5, "speed": 1, "damage": 4, "range": 2, "special": 0,
           "size": "large", "gun": "cannon", "engine": "tank"}

APPEARANCE = {"color": "#4d6bff", "shape": "tank", "accent": "#9fdcff"}

import math

def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def decide(view):
    if not view.enemies:
        return {"turn": 8}                            # slow scan — you're the anvil

    target = view.enemies[0]
    aim = target.bearing
    action = {"turn": aim}

    # The cannon punishes wasted shots: only fire when DEAD centred (well inside
    # the narrow arc) and in range. Patience is the whole build.
    if target.dist <= view.self.weapon_range and abs(aim) < view.self.weapon_arc * 0.6:
        action["fire"] = "laser"    # fire the cannon

    # Too far to threaten? Advance. In their face? Hold ground and trade — you win trades.
    if target.dist > view.self.weapon_range * 0.9:
        action["turn"] = steer(view, aim)
        action["thrust"] = "forward"

    # Rocket incoming and you can't dodge much — walk THROUGH it toward the shooter.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 100:
        action["thrust"] = "forward"
    return action
