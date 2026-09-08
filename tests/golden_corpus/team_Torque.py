# ============================================================================
#  "ANVIL" — large · cannon · tank.  Fight on the plane where the dice don't roll.
#
#  The whole game sells chaos as the equalizer — but every "goes wrong" bullet
#  (miss / jam / overheat / flip) hits GUNS and blasts on YOU. None of it touches
#  ramming. So Anvil wins with the RNG-proof stuff everyone else ignores:
#    * RAM for damage (mass = radius²; large+tank bulldozes, they bounce)
#    * MINES to flip + slow the small/sprint/hover bots (all "tossed easily")
#      while we "barely flip" and right 2× fast — flipping is a one-way trade
#    * the CANNON only in guaranteed windows (a flipped/point-blank target never
#      gets missed), so misses/jams/heat almost never bite us
#    * the SUDDEN-DEATH ring as our executioner — hold centre, let it shrink the
#      kiter's safe box; the ring kills hover too, so we needn't ever catch them.
# ============================================================================

# 12 pts: fat HP pool to reach sudden death, enough speed to herd, dash for
# ram-charges + ring escapes, a couple agility pts to aim the 8° cannon, damage
# for the finisher. Range 0 on purpose — we brawl, and short reach is fog-proof.
LOADOUT = {"hp": 4, "speed": 2, "damage": 2, "range": 0, "special": 2, "agility": 2,
           "size": "large", "gun": "cannon", "engine": "tank"}

# Gunmetal drum with a red stripe — looks like exactly what it is.
APPEARANCE = {"color": "#2b2f36", "shape": "spike", "accent": "#ff3b30"}

import math


def steer(view, turn):
    """Wall-aware steering: probe ahead; if a wall is in the way, swing wide.
    (From the examples — driving into walls is the #1 rookie death.)"""
    h = math.radians(view.self.heading + turn)
    px, py = view.self.x + math.cos(h) * 80, view.self.y + math.sin(h) * 80
    pad = view.self.radius + 4
    for (wx, wy, ww, wh) in view.arena.walls:
        if wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad:
            return turn + 55
    return turn


def bearing_to(view, tx, ty):
    """Relative bearing to an arbitrary point (0 = straight ahead, + = right),
    matching how enemy.bearing / turn are defined."""
    dx, dy = tx - view.self.x, ty - view.self.y
    ang = math.degrees(math.atan2(dy, dx)) - view.self.heading
    return (ang + 180) % 360 - 180


def decide(view):
    me = view.self
    W, H = view.arena.width, view.arena.height
    cx, cy = W / 2.0, H / 2.0

    # --- 0) SUDDEN DEATH trumps everything. If the molten ring is closing and
    #        I'm near the burning edge, drop the fight and drive to safe centre.
    #        The ring kills anything (hover included) — never let it have me.
    ring = view.arena.collapse
    if ring > 0:
        margin = me.radius + 12
        if (me.x < ring + margin or me.x > W - ring - margin or
                me.y < ring + margin or me.y > H - ring - margin):
            act = {"turn": steer(view, bearing_to(view, cx, cy)), "thrust": "forward"}
            if me.special_ready:
                act["special"] = True          # dash out of the fire
            return act

    # No target: sit on the centre (the only safe ground late) and scan.
    if not view.enemies:
        return {"turn": steer(view, bearing_to(view, cx, cy)), "thrust": "forward"}

    target = view.enemies[0]                    # nearest-first
    aim = target.bearing
    action = {"turn": steer(view, aim)}
    can_shoot = me.cooldown <= 0 and not me.jammed and not me.overheated

    # --- 1) FLIPPED ENEMY = free, guaranteed kill window. It's stationary and
    #        helpless: point-blank the cannon can't miss. Charge in and delete it.
    if target.flipped:
        action["thrust"] = "forward"
        if me.special_ready and target.dist > me.radius * 2:
            action["special"] = True            # dash the gap before it self-rights
        if can_shoot and target.dist <= me.weapon_range and abs(aim) < me.weapon_arc:
            action["fire"] = "laser"            # cannon on a sitting duck
        elif me.rocket_ready and abs(aim) < 12:
            action["fire"] = "rocket"           # keep it pinned down
        if me.trap_ready and target.dist < 120:
            action["drop_trap"] = True          # mine the spot it'll wake up on
        return action

    # --- 2) POINT-BLANK MINE. In-your-face brawl: drop a mine on the contested
    #        spot. They flip (they all flip easy); I barely do. One-way trade.
    if me.trap_ready and target.dist < 90:
        action["drop_trap"] = True

    # --- 3) RAM. Deterministic damage the chaos layer can't touch. Dash-charge
    #        when the gap is ram-sized and roughly lined up; mass does the rest.
    if me.special_ready and target.dist < 210 and abs(aim) < 40:
        action["special"] = True
        action["thrust"] = "forward"

    # --- 4) CANNON, but ONLY in a near-sure window: in reach, gun cool/unjammed,
    #        and well inside the narrow arc. A miss costs heat + a jam roll, so
    #        discipline beats spraying — we're not gun-dependent anyway.
    if can_shoot and target.dist <= me.weapon_range and abs(aim) < me.weapon_arc * 0.6:
        action["fire"] = "laser"
    # --- 5) Otherwise spend a rocket to FLIP an approaching / kiting bot and set
    #        up the window above (rockets flip; they're all easy to flip).
    elif me.rocket_ready and abs(aim) < 12 and 150 < target.dist < 460:
        action["fire"] = "rocket"

    # --- Movement: close to brawl range. In their face I hold — I win close.
    if target.dist > me.weapon_range:
        action["thrust"] = "forward"

    # --- Incoming rocket: I barely flip, so I don't panic out of position. A
    #        light angle bleeds the splash while I keep bearing down on the shooter.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 90:
        action["turn"] = steer(view, aim + 30)
        action["thrust"] = "forward"

    return action
