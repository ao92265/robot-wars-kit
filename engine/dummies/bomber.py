"""Dummy: keeps its distance and lobs rockets. Teaches you to dodge (watch
view.incoming_rockets) and to close the gap before its ammo runs out."""

from . import heat_ok, kite_move

LOADOUT = {"hp": 3, "speed": 4, "damage": 1, "range": 3, "special": 1, "size": "medium"}
APPEARANCE = {"color": "#ff9f43", "shape": "spike"}


def decide(view):
    if not view.enemies:
        return {"turn": 10}
    target = view.enemies[0]
    me = view.self
    aim = target.bearing
    side = 1 if (view.tick // 45) % 2 == 0 else -1   # swap orbit direction
    action = kite_move(view, side, standoff=0.75, spiral=12.0)
    # roughly lined up? launch a rocket
    if abs(aim) < 14 and me.rocket_ready:
        action["fire"] = "rocket"
    elif abs(aim) < me.weapon_arc and target.dist <= me.weapon_range and heat_ok(view):
        action["fire"] = "laser"
    return action
