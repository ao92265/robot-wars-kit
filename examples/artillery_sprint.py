"""EXAMPLE BUILD — "Longshot": cannon + sprint engine, medium chassis.

The idea: hit-and-run artillery. Sprint legs reposition between cannon
reloads — fire, dash to a new angle while the gun cycles, fire again. Rockets
fill the gaps. Weakness: middling HP and the cannon's narrow arc punishes
sloppy driving; if your dance partner corners you mid-reload you're in trouble.

Copy anything here into your my_bot.py — LOADOUT, APPEARANCE, decide(), or all three.
"""

LOADOUT = {"hp": 2, "speed": 4, "damage": 3, "range": 3, "special": 0,
           "size": "medium", "gun": "cannon", "engine": "sprint"}

APPEARANCE = {"color": "#b983ff", "shape": "spike", "accent": "#ffd93d"}

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
    if view.incoming_rockets and view.incoming_rockets[0].dist < 140:
        return {"turn": 95, "thrust": "forward"}

    if not view.enemies:
        return {"turn": 10}

    target = view.enemies[0]
    aim = target.bearing
    action = {"turn": aim}

    reloading = view.self.cooldown > 0
    if reloading:
        # Gun is cycling: strafe to a new firing angle instead of standing there.
        action["turn"] = steer(view, aim + 55)
        action["thrust"] = "forward"
        # Mid-range and lined up roughly? Spend a rocket while the cannon cycles.
        if view.self.rocket_ready and abs(aim) < 12 and target.dist < 420:
            action["turn"] = aim            # snap back to centre for the launch
            action["fire"] = "rocket"
    else:
        # Cannon ready: line up the narrow arc and take the shot.
        if target.dist > view.self.weapon_range:
            action["thrust"] = "forward"
        elif abs(aim) < view.self.weapon_arc * 0.7:
            action["fire"] = "laser"        # BOOM
    return action
