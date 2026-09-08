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

# Spend up to 12 points across six stats (per-stat cap 6): hp, speed, damage,
# range, special (dash), agility (turn rate). Then three FREE archetype picks:
#   size:   "small" | "medium" | "large"
#   gun:    "laser" | "cannon" | "shotgun"
#   engine: "standard" | "sprint" | "tank" | "hover"
# Every knob + trade-off table: CUSTOMIZE.md. Copyable builds: examples/.
LOADOUT = {"hp": 3, "speed": 3, "damage": 3, "range": 2, "special": 1,
           "size": "medium", "gun": "laser", "engine": "tank"}

# Branding — your colours on the big screen. color/accent are #RRGGBB hex; shape is
# your spinner's look: "tank", "speeder", "orb", "spike". Cosmetic only.
APPEARANCE = {"color": "#3fd0c9", "shape": "tank", "accent": "#f4f7fb"}

import math


def _blocked(view, turn, reach=80):
    """True if a wall or dangerous hazard sits ahead along heading+turn."""
    me = view.self
    h = math.radians(me.heading + turn)
    px, py = me.x + math.cos(h) * reach, me.y + math.sin(h) * reach
    pad = me.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return True
    for hz in getattr(view.arena, "hazards", []):
        kind = getattr(hz, "kind", getattr(hz, "type", ""))
        if kind in ("lava", "pit", "flipper"):
            hx, hy = getattr(hz, "x", None), getattr(hz, "y", None)
            hr = getattr(hz, "r", getattr(hz, "radius", 40))
            if hx is not None and (px - hx) ** 2 + (py - hy) ** 2 <= (hr + pad) ** 2:
                return True
    return False


def steer(view, turn):
    """Wall/hazard-aware steering: if the path ahead is blocked, swing wide."""
    if _blocked(view, turn):
        for swing in (55, -55, 90, -90, 130):
            if not _blocked(view, turn + swing):
                return turn + swing
    return turn


def decide(view):
    """Called once per tick. Return any subset of:
      {"thrust": "forward"|"back", "turn": <deg>, "fire": "laser"|"rocket",
       "drop_trap": True, "special": True}
    """
    me = view.self

    # Dodge: if a rocket is bearing down on you, juke sideways and dash.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 130:
        return {"turn": steer(view, 90), "thrust": "forward", "special": True}

    if not view.enemies:
        return {"turn": 12}                       # no one in sight: turn and scan

    target = view.enemies[0]                       # enemies are sorted nearest-first
    aim = target.bearing                           # 0 = straight ahead, + = to the right
    action = {"turn": aim}                          # rotate to face the target

    # Free kill: an enemy just got flipped — close in and unload.
    if target.flipped:
        action["turn"] = steer(view, aim)
        action["thrust"] = "forward"
        if abs(aim) < me.weapon_arc and target.dist <= me.weapon_range and not me.overheated:
            action["fire"] = "laser"
        if me.special_ready and target.dist > me.weapon_range * 0.4:
            action["special"] = True
        return action

    # Anti-rammer: an enemy charging into melee, roughly head-on. Meet it with a
    # rocket (rockets FLIP) and dash to break the ram instead of trading crunches.
    if target.dist < me.weapon_range * 0.5 and abs(aim) < 45:
        if me.rocket_ready:
            action["fire"] = "rocket"
        elif abs(aim) < me.weapon_arc and not me.overheated:
            action["fire"] = "laser"
        if me.special_ready:
            action["special"] = True             # dash past/through, not a standing trade
        # Leave a mine right where the rusher is as we dash clear — it arms and
        # flips whoever keeps charging. We're moving away, so we don't eat it.
        if me.trap_ready and me.special_ready:
            action["drop_trap"] = True
        return action

    if target.dist > me.weapon_range:
        action["turn"] = steer(view, aim)
        action["thrust"] = "forward"               # too far: close in
    elif abs(aim) < me.weapon_arc and not me.overheated:
        action["fire"] = "laser"                    # lined up and in range: shoot

    # Proactive flip: a head-on charger inside rocket reach eats a rocket before
    # it can ram. Rockets flip; a flipped rusher is a free kill next tick.
    if abs(aim) < 10 and me.rocket_ready and target.dist < me.weapon_range * 1.3:
        action["fire"] = "rocket"

    return action
