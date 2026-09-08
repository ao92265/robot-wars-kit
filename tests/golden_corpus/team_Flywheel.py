# ============================================================================
#  "BULLDOZER" — Ram + Flip Battering build
#
#  Large + tank chassis (~1.5x HP, barely flips, self-rights 2x fast).
#  The loop:  rocket to flip  ->  dash-charge the helpless bot  ->  shotgun the
#  corpse in the 44 degree cone  ->  drop a mine on anyone who chases.
#  We win trades because we're the heavy mass and we shrug off the flips/traps
#  that wreck everyone else. Weakness: we're SLOW, so a kiting sniper is the
#  bad matchup -- rockets-on-approach are our only long-range threat.
#
#  Run it (YOUR terminal, not mine):  python arena.py
#  Check legality:                    python arena.py --check
# ============================================================================

# hp 2 = HP cushion for the slow approach (chassis already gives a big base)
# speed 3 = close the gap; ram damage scales with closing speed
# damage 3 = pumps the shotgun finisher (only x0.55 base) -- NOT the ram
# special 2 = dash is the engine of the build; more pts = ram more often
# agility 2 = offsets tank's slow turn so we can line up rams / track jukers
# range 0 = shotgun wants point-blank; the wide cone matters more than reach
LOADOUT = {"hp": 2, "speed": 3, "damage": 3, "special": 2, "agility": 2, "range": 0,
           "size": "large", "gun": "shotgun", "engine": "tank"}

# Blood-red brawler with a spiked drum. Cosmetic only.
APPEARANCE = {"color": "#b0201c", "shape": "spike", "accent": "#f4d03f"}


def decide(view):
    """Called once per tick. See GUIDELINES.md / CUSTOMIZE.md for the full view."""
    me = view.self

    # Flipped = helpless anyway; tank rights fast, just wait it out.
    if me.flipped:
        return {}

    # Light juke if a rocket is about to flip us mid-approach. We're tanky and
    # barely flip, so we only bother for very close ones -- and never if we're
    # already lining up a kill (see flip-window below). (Tuning dial: distance.)
    if view.incoming_rockets and view.incoming_rockets[0].dist < 90:
        action = {"turn": 70, "thrust": "forward"}
        if me.special_ready:
            action["special"] = True
        return action

    if not view.enemies:
        return {"turn": 22}                    # no one in sight: rotate and scan

    target = view.enemies[0]                    # enemies are sorted nearest-first
    aim = target.bearing                        # 0 = straight ahead, + = to the right
    action = {"turn": aim}                       # always rotate to face the target

    in_range = target.dist <= me.weapon_range
    lined_up = abs(aim) < me.weapon_arc
    can_fire = not me.overheated and not me.jammed and me.cooldown == 0

    # 1. FLIP WINDOW -- the enemy is down. Commit everything, this is the kill.
    if target.flipped:
        action["thrust"] = "forward"
        if me.special_ready and target.dist < me.weapon_range * 2.5:
            action["special"] = True             # dash-charge the helpless bot
        if in_range and lined_up and can_fire:
            action["fire"] = "laser"             # empty the shotgun into it
        return action

    # 2. APPROACH -- too far for the shotgun. Close in, soften with a rocket.
    if not in_range:
        action["thrust"] = "forward"
        if lined_up and me.rocket_ready and target.dist < me.weapon_range * 4:
            action["fire"] = "rocket"            # try to flip them before contact
        elif me.special_ready and lined_up and target.dist < me.weapon_range * 2:
            action["special"] = True             # final lunge -> ram at full speed
        return action

    # 3. POINT-BLANK -- in the cone. Ram + shotgun, mine the chasers.
    action["thrust"] = "forward"                 # keep pressing in (ram pressure)
    if me.special_ready and lined_up:
        action["special"] = True                 # dash-ram: crunch + maybe flip them
    if lined_up and can_fire:
        action["fire"] = "laser"                 # shotgun (fire discipline via can_fire)
    if me.trap_ready and abs(aim) > 120:
        action["drop_trap"] = True               # enemy is behind us -> mine the chaser
    return action
