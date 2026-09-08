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
LOADOUT = {"hp": 3, "speed": 3, "damage": 3, "range": 2, "special": 1, "size": "medium"}

# Branding — your colours on the big screen. color/accent are #RRGGBB hex; shape is
# your spinner's look: "tank", "speeder", "orb", "spike". Cosmetic only.
APPEARANCE = {"color": "#3fd0c9", "shape": "tank", "accent": "#f4f7fb"}


def decide(view):
    """Called once per tick. Return what you want to do this tick. Any key you
    leave out = you don't do it. Returning {} = do nothing.

      {"thrust": "forward"|"back",   # move
       "turn": <degrees>,            # rotate toward something (auto-clamped)
       "fire": "laser"|"rocket",     # laser = instant; rocket = travels + splash (3 ammo)
       "drop_trap": True,            # leave a proximity mine where you stand (2 traps)
       "special": True}              # dash (needs special >= 1)
    """
    # Dodge: if a rocket is bearing down on you, juke sideways and dash.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 130:
        return {"turn": 90, "thrust": "forward", "special": True}

    if not view.enemies:
        return {"turn": 12}                       # no one in sight: turn and scan

    target = view.enemies[0]                       # enemies are sorted nearest-first
    aim = target.bearing                           # 0 = straight ahead, + = to the right
    action = {"turn": aim}                          # rotate to face the target

    if target.dist > view.self.weapon_range:
        action["thrust"] = "forward"               # too far: close in
    elif abs(aim) < view.self.weapon_arc:
        action["fire"] = "laser"                    # lined up and in range: shoot

    return action
