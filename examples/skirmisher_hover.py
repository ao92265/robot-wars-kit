"""EXAMPLE BUILD — "Wraith": laser + hover engine, small chassis, max range.

The idea: terrain doesn't exist for you. Hover skims pits, water and ice, so
you kite ACROSS hazards the enemy has to route around — then tag them at
max laser range while they wade. Weakness: thin plating (hover frame) and low
damage per hit; anything that corners you kills you.

Copy anything here into your my_bot.py — LOADOUT, APPEARANCE, decide(), or all three.
"""

LOADOUT = {"hp": 1, "speed": 4, "damage": 0, "range": 6, "special": 1,
           "size": "small", "gun": "laser", "engine": "hover"}

APPEARANCE = {"color": "#3fd0c9", "shape": "orb", "accent": "#f4f7fb"}

import math

def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide.
    (Hover skims HAZARDS, not walls — steel is still steel.)"""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def decide(view):
    # Dodge rockets first, always.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 150:
        return {"turn": 80, "thrust": "forward", "special": True}

    if not view.enemies:
        return {"turn": 12}

    target = view.enemies[0]
    aim = target.bearing
    action = {"turn": aim}

    band = view.self.weapon_range           # your reach is the battlefield
    if target.dist > band:
        action["turn"] = steer(view, aim)
        action["thrust"] = "forward"        # drift into range
    elif target.dist < band * 0.55:
        # They're closing — back off THROUGH the nearest hazard if there is one;
        # hover doesn't care, wheels do. (Reverse is slower: dash out if ready.)
        action["thrust"] = "back"
        if view.self.special_ready:
            action["turn"] = aim + 180      # flip and dash away
            action["special"] = True
            action["thrust"] = "forward"

    if target.dist <= band and abs(aim) < view.self.weapon_arc:
        action["fire"] = "laser"
    return action
