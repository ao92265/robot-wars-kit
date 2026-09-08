# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs chaser --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
#  See GUIDELINES.md for the full view + action reference.
#
#  BUILD: "Longshot" — a KITING SNIPER. Big long-range cannon; keep the enemy at
#  arm's length, land heavy hits, and back-pedal whenever they close in. Dash to
#  escape if something fast gets on top of us. Weakness: if a rusher DOES close
#  the gap, the slow cannon + thin defence is a bad time — so the whole brain is
#  about MANAGING DISTANCE.
# ============================================================================

# Spend up to 12 points across six stats (per-stat cap 6): hp, speed, damage,
# range, special (dash), agility (turn rate). Then three FREE archetype picks.
LOADOUT = {"hp": 0, "speed": 3, "damage": 3, "range": 5, "special": 1,
           "agility": 0, "size": "medium", "gun": "cannon", "engine": "standard"}

# Branding — cosmetic only.
APPEARANCE = {"color": "#2f6fed", "shape": "spike", "accent": "#e8f0ff"}

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
    me = view.self

    # 1) A rocket is bearing down — juke sideways and burn the dash to escape.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 150:
        return {"turn": 90, "thrust": "forward", "special": True}

    # 2) No one in sight — turn slowly and scan (stay put, don't wander into trouble).
    if not view.enemies:
        return {"turn": 20}

    target = view.enemies[0]                      # nearest enemy
    aim = target.bearing                          # 0 = dead ahead
    action = {"turn": aim}                        # always face the target to aim the cannon

    # Our ideal fighting distance: near the edge of our reach, so they can't touch us.
    reach = me.weapon_range
    too_close = reach * 0.55                       # if they get inside this, RETREAT
    too_far = reach                                # if they're past our reach, edge closer

    # 3) DISTANCE CONTROL — the heart of a sniper.
    if target.dist < too_close:
        action["thrust"] = "back"                  # back-pedal to reopen the gap
        if me.special_ready and target.dist < too_close * 0.6:
            action["special"] = True               # too close for comfort — dash away
    elif target.dist > too_far:
        action["thrust"] = "forward"               # out of range — close in a touch
        action["turn"] = steer(view, aim)          # (only steer round walls while advancing)

    # 4) FIRE — cannon has a tiny aim window, so only shoot when truly lined up
    #    and in range. Ease off if the gun is running hot (cannon overheats fast),
    #    unless the enemy is flipped and helpless — then unload.
    lined_up = abs(aim) < me.weapon_arc
    in_range = target.dist <= reach
    if lined_up and in_range and (target.flipped or me.heat < 60):
        action["fire"] = "laser"                   # "laser" just means "fire your gun"

    return action
