"""Dummy: a large, tanky bot that lays mines and lures you onto them. Teaches
you to watch view.mines and not chase blindly into a trap field."""

from . import heat_ok, kite_move, nav_turn

LOADOUT = {"hp": 5, "speed": 2, "damage": 2, "range": 2, "special": 1, "size": "large"}
APPEARANCE = {"color": "#b983ff", "shape": "tank"}


def decide(view):
    me = view.self
    if not view.enemies:
        return {"turn": nav_turn(view, 8), "thrust": "forward"}
    target = view.enemies[0]
    aim = target.bearing
    side = 1 if (view.tick // 60) % 2 == 0 else -1
    action = kite_move(view, side, standoff=0.8, spiral=11.0)
    # mine the FIGHT, not the spawn: lay along the orbit ring the enemy has to
    # cross, spaced out so the field covers ground
    own = [m for m in view.mines if m.mine]
    if (me.trap_ready and target.dist < me.weapon_range * 1.6
            and all(m.dist > 130 for m in own)):
        action["drop_trap"] = True
    if abs(aim) < me.weapon_arc and target.dist <= me.weapon_range and heat_ok(view):
        action["fire"] = "laser"
    return action
