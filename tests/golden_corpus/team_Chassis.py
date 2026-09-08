# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs sniper --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
#  See GUIDELINES.md for the full view + action reference.
#
#  The point of this exercise: find the dirtiest *legal* strategy. Probe the
#  edges. Try to break the game. (You can't crash it — go on, try.)
# ============================================================================

# "Bastion" — a walking siege gun. Cannon hits for 2x damage (the biggest single
# hit in the game); large + tank chassis has a huge HP pool, barely flips, and
# wins ramming trades against anyone who charges in. Weakness: narrow 8 degree
# aim arc and slow reload, so a fast orbiting target makes you whiff.
LOADOUT = {"hp": 4, "speed": 0, "damage": 6, "range": 1, "special": 1,
           "size": "large", "gun": "cannon", "engine": "tank"}

# Branding — your colours on the big screen. color/accent are #RRGGBB hex; shape is
# your spinner's look: "tank", "speeder", "orb", "spike". Cosmetic only.
APPEARANCE = {"color": "#008080", "shape": "spike", "accent": "#ffffff"}

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
        return {"turn": 8}                        # slow scan — you're the anvil

    target = view.enemies[0]
    aim = target.bearing
    action = {"turn": aim}

    # Flipped enemy = free damage. Line up and unload, no patience needed.
    if target.flipped:
        if target.dist <= view.self.weapon_range and abs(aim) < view.self.weapon_arc:
            action["fire"] = "laser"
        if target.dist > 40:
            action["thrust"] = "forward"
        return action

    # Someone diving in on us: seed a mine at our feet before they arrive, then
    # hold ground. It arms in time to catch the charge and can flip them into
    # our next cannon shot.
    if 60 < target.dist < 160 and view.self.trap_ready:
        action["drop_trap"] = True

    # The cannon punishes wasted shots: only fire dead-centred and in range.
    if target.dist <= view.self.weapon_range and abs(aim) < view.self.weapon_arc * 0.6:
        action["fire"] = "laser"

    if target.dist > view.self.weapon_range * 0.9:
        # Too far to threaten — close the gap, dashing if we're lined up.
        action["turn"] = steer(view, aim)
        action["thrust"] = "forward"
        if view.self.special_ready and abs(aim) < 20:
            action["special"] = True
    elif target.dist < 60 and view.self.special_ready:
        # They're already in our face — dash-charge into them. Large + tank
        # wins the ram trade (heavy bulldozes light), and it can flip them.
        action["special"] = True
        action["thrust"] = "forward"

    # Rocket incoming and we can't outrun it — tank engine barely flips, so
    # walk through it straight at the shooter instead of losing position.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 100:
        action["thrust"] = "forward"

    return action
