"""Dummy: long range, high damage, fragile. KITES — faces the enemy and back-pedals
while firing, holding it at range. The 'can you beat the good bot' bar: punishes
naive charging (you eat shots closing in); beat it by closing fast (speed/dash) so
you reach point-blank where its fragility and your full-damage shots win."""

from . import heat_ok, kite_move

LOADOUT = {"hp": 2, "speed": 4, "damage": 3, "range": 3, "special": 0}


def decide(view):
    if not view.enemies:
        return {"turn": 8}                       # sweep for a target
    target = view.enemies[0]
    me = view.self
    aim = target.bearing
    side = 1 if (view.tick // 50) % 2 == 0 else -1
    # reposition between rounds: strafe the ring while the gun cycles, settle
    # onto the shot as the cooldown runs out
    action = kite_move(view, side, standoff=0.85, spiral=14.0)
    if abs(aim) < me.weapon_arc and target.dist <= me.weapon_range and heat_ok(view):
        action["fire"] = True                    # fire whenever lined up + in range
    return action
