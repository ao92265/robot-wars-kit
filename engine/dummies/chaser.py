"""Dummy: turns toward the nearest enemy and rushes in, firing point-blank.
Punishes loitering in melee range; rewards range + kiting."""

from . import heat_ok, nav_turn

LOADOUT = {"hp": 4, "speed": 4, "damage": 3, "range": 0, "special": 1}


def decide(view):
    if not view.enemies:
        return {"turn": nav_turn(view, 0), "thrust": "forward"}
    target = view.enemies[0]               # nearest (list is dist-sorted)
    aim = target.bearing                    # 0 = dead ahead
    # Cut INSIDE the circle instead of orbiting: over-steer toward the target so
    # the path spirals in, plus a tiny per-robot jink (seeded rng -> deterministic)
    # so two bots can't lock into a perfect clockwise ring.
    steer = aim * 1.6 + (view.rng.random() - 0.5) * 14
    action = {"turn": nav_turn(view, steer), "thrust": "forward"}   # rush in, steering around walls
    if abs(aim) < view.self.weapon_arc and target.dist <= view.self.weapon_range and heat_ok(view):
        action["fire"] = "laser"            # fire check uses TRUE bearing, not the steer bias
    elif abs(aim) < 12 and view.self.rocket_ready:
        action["fire"] = "rocket"           # lob a rocket down the charge lane — projectiles in flight
    # dash to close whenever it's off the reload — collapses a standoff, not just out-of-range
    if view.self.special_ready and target.dist > view.self.weapon_range * 0.5:
        action["special"] = True
    return action
