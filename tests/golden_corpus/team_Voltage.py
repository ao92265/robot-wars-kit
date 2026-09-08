# ============================================================================
#  "Wraith" — sprint · small · laser speed-kiter
#  Fastest bot on the field: sprint×small gives ×1.39 speed multiplier.
#  Outpaces every dummy. Stays outside the chaser's 110-unit range while
#  firing freely from 120-210 units. Closes on the sniper before it can
#  maintain its preferred gap. Rockets used for flip-windows, not wasted.
#
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs sniper --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
# ============================================================================

# 12 points. sprint×small = ×1.39 speed multiplier (≈23 units/tick at speed 3).
# Laser: steady fire, 16° arc, 22 heat/shot, 1.2% jam — minimal heat issues.
# agility:1 → snappier aim with the narrow arc.  special:1 → emergency dash.
LOADOUT = {"hp": 2, "speed": 3, "damage": 3, "range": 2, "agility": 1, "special": 1,
           "size": "small", "gun": "laser", "engine": "sprint"}

APPEARANCE = {"color": "#ff4757", "shape": "speeder", "accent": "#ffd32a"}

import math


def _wall_blocked(view, turn):
    """True if heading (current + turn) will hit a wall within 100 units."""
    h = math.radians(view.self.heading + turn)
    px = view.self.x + math.cos(h) * 100
    py = view.self.y + math.sin(h) * 100
    pad = view.self.radius + 6
    return any(
        wx - pad <= px <= wx + ww + pad and wy - pad <= py <= wy + wh + pad
        for wx, wy, ww, wh in view.arena.walls
    )


def decide(view):
    s = view.self

    # ── 1. Sudden-death: escape the lava ring ────────────────────────────
    c = view.arena.collapse
    if c > 0:
        margin = c + 55
        if (s.x < margin or s.x > view.arena.width - margin or
                s.y < margin or s.y > view.arena.height - margin):
            cx, cy = view.arena.width / 2, view.arena.height / 2
            to_ctr = math.degrees(math.atan2(cy - s.y, cx - s.x)) - s.heading
            act = {"turn": to_ctr, "thrust": "forward"}
            if s.special_ready:
                act["special"] = True
            return act

    # ── 2. Dodge incoming rockets ─────────────────────────────────────────
    if view.incoming_rockets:
        r = view.incoming_rockets[0]
        if r.dist < 165:
            side = 82 if r.bearing >= 0 else -82
            act = {"turn": side, "thrust": "forward"}
            if s.special_ready:
                act["special"] = True
            return act

    # ── 3. No enemies: sweep ──────────────────────────────────────────────
    if not view.enemies:
        return {"turn": 18, "thrust": "forward"}

    target = view.enemies[0]
    b = target.bearing        # relative bearing to target; 0 = dead ahead
    r = s.weapon_range        # our laser reach (230 units at range:2)

    # ── 4. Exploit flipped enemy ─────────────────────────────────────────
    if target.flipped:
        act = {"turn": b}
        if target.dist > r:
            act["thrust"] = "forward"
        if abs(b) < s.weapon_arc and not s.overheated:
            if s.rocket_ready and abs(b) < 20 and target.dist > 55:
                act["fire"] = "rocket"
            else:
                act["fire"] = "laser"
        return act

    # ── 5. Range management ───────────────────────────────────────────────
    # opt_lo: stay outside this — we're faster so we CAN hold the gap
    # opt_hi: approach until within this — good accuracy, good evasion room
    opt_lo = r * 0.62      # ~143 units — safely outside chaser's 110 reach
    opt_hi = r * 0.88      # ~202 units

    act = {"turn": b}      # face the target for accurate narrow-arc laser

    if target.dist > opt_hi:
        turn = b if not _wall_blocked(view, b) else b + 65
        act["turn"] = turn
        act["thrust"] = "forward"
        # Dash only when clearly out of range (preserve for dodging otherwise)
        if s.special_ready and target.dist > r * 1.15:
            act["special"] = True

    elif target.dist < opt_lo:
        # Too close — dash-sidestep away and leave a mine in our wake
        act["turn"] = b + 130
        act["thrust"] = "forward"
        if s.special_ready:
            act["special"] = True
        if s.trap_ready:
            act["drop_trap"] = True

    else:
        # Sweet spot: face target and thrust BACK — maintain range against rushers.
        # Backward movement keeps the gap open; laser arc covers the target while retreating.
        weave = 10 if (view.tick // 38) % 2 == 0 else -10
        act["turn"] = b + weave
        act["thrust"] = "back"

    # ── 6. Fire ───────────────────────────────────────────────────────────
    # Firing uses the CURRENT bearing `b`, regardless of the turn we chose above.
    if target.dist <= r and abs(b) < s.weapon_arc and not s.overheated:
        # Rockets: prefer when perfectly lined up — splash can flip the target
        if s.rocket_ready and abs(b) < 14 and target.dist > opt_lo * 0.7:
            act["fire"] = "rocket"
        else:
            act["fire"] = "laser"

    # ── 7. Mine drop when enemy is pressing ──────────────────────────────
    if s.trap_ready and target.dist < opt_lo * 1.35 and "drop_trap" not in act:
        act["drop_trap"] = True

    return act
