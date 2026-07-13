"""EXAMPLE BUILD — "Riot": shotgun + sprint engine, small chassis.

The idea: nothing survives point-blank. Sprint legs + dash close the gap fast;
the shotgun's huge arc means you barely have to aim once you're in — and it
hits EVERYONE in the cone, so it's vicious against tight team pairs.
Weakness: paper armour and no reach. Get kited = get shredded.

Copy anything here into your my_bot.py — LOADOUT, APPEARANCE, decide(), or all three.
"""

LOADOUT = {"hp": 2, "speed": 6, "damage": 3, "range": 0, "special": 1,
           "size": "small", "gun": "shotgun", "engine": "sprint"}

APPEARANCE = {"color": "#ff5d3a", "shape": "speeder", "accent": "#ffd93d"}

import math

def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide.
    Copy this into your bot — driving into walls is the #1 rookie death."""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55        # swing around it
    return turn


def decide(view):
    # A rocket is bearing down — juke hard and burn the dash sideways.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 130:
        return {"turn": 90, "thrust": "forward", "special": True}

    if not view.enemies:
        return {"turn": 15, "thrust": "forward"}      # hunt — never sit still

    target = view.enemies[0]
    action = {"turn": steer(view, target.bearing), "thrust": "forward"}   # always closing

    # Dash in the moment the gap is dash-sized (not from across the map).
    if view.self.special_ready and 120 < target.dist < 360:
        action["special"] = True

    # Shotgun arc is huge — fire the instant they're inside reach.
    if target.dist <= view.self.weapon_range and abs(target.bearing) < view.self.weapon_arc:
        action["fire"] = "laser"    # "laser" = fire your gun, whatever archetype it is

    # Drop a mine on the way in: chasers following you eat it.
    if view.self.trap_ready and target.dist < 200:
        action["drop_trap"] = True
    return action
