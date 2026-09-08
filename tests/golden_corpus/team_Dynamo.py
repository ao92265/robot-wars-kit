# Submitted by: RobA
# ============================================================================
#  THIS IS THE ONLY FILE YOU EDIT.  Three things: LOADOUT, APPEARANCE, decide().
#  Run it:   python3 arena.py            (watch it fight the dummies)
#  Tune it:  python3 arena.py --vs sniper --fast --best-of 20
#  Check it: python3 arena.py --check     (is my loadout legal?)
#  Submit:   python3 arena.py --submit "Your Team Name"
#  See GUIDELINES.md for the full view + action reference.
# ============================================================================
#
#  "JUGGERNAUT" — large/tank/cannon walking siege gun.
#  Idea: hp and damage maxed out (6/6, the full 12-point budget) — nothing
#  left for speed, range, special or agility. Large chassis + tank engine
#  stack for the biggest HP pool and best flip resistance in the game; the
#  cannon roughly doubles damage per hit. decide() grinds forward, holds
#  ground once in range, and only fires DEAD centred (well inside the
#  already-narrow cannon arc) — a whiffed cannon shot means a 2x reload
#  wasted, so patience matters more here than with any other gun. No dash:
#  this build doesn't run from anything, it just keeps coming.
#  Weak to: kiters who out-turn the narrow arc and never let it close, and
#  anything that punishes the slow turn rate / lack of mobility.

LOADOUT = {"hp": 6, "speed": 0, "damage": 6, "range": 0, "special": 0, "agility": 0,
           "size": "large", "gun": "cannon", "engine": "tank"}

APPEARANCE = {"color": "#4d6bff", "shape": "tank", "accent": "#9fdcff"}

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


def pick_target(view, self_):
    """Focus down whoever's actually engaging us: among enemies close enough
    to be a live threat, finish the lowest-HP one first to shrink the number
    of attackers fastest. Nobody in striking range yet: just track nearest."""
    threats = [e for e in view.enemies if e.dist <= self_.weapon_range * 1.3]
    if threats:
        return min(threats, key=lambda e: e.hp)
    return view.enemies[0]


def decide(view):
    self_ = view.self

    if not view.enemies:
        return {"turn": 8, "thrust": "forward"}          # slow scan — you're the anvil

    # A flipped enemy is risk-free damage — always worth the shot.
    flipped = [e for e in view.enemies if e.flipped]
    if flipped:
        target = min(flipped, key=lambda e: e.dist)
        aim = target.bearing
        action = {"turn": steer(view, aim), "thrust": "forward"}
        if target.dist <= self_.weapon_range and abs(aim) < self_.weapon_arc and not self_.jammed:
            action["fire"] = "laser"
        return action

    target = pick_target(view, self_)
    aim = target.bearing
    action = {"turn": steer(view, aim)}

    # Too far to threaten? Advance. In range? Hold ground and trade.
    if target.dist > self_.weapon_range * 0.9:
        action["thrust"] = "forward"

    # The cannon punishes wasted shots hard (2x reload on a whiff): only fire
    # DEAD centred, well inside the already-narrow arc. Patience is the build.
    if (target.dist <= self_.weapon_range and abs(aim) < self_.weapon_arc * 0.6
            and not self_.jammed and not self_.overheated):
        action["fire"] = "laser"

    # Seed mines in the ground we're grinding through — free chip damage for
    # anyone dumb enough to chase this slowly.
    if self_.trap_ready and target.dist < 300:
        action["drop_trap"] = True

    # Can't outrun a rocket at this speed — walk through it toward the shooter.
    if view.incoming_rockets and view.incoming_rockets[0].dist < 100:
        action["thrust"] = "forward"

    # SUDDEN DEATH: bias movement toward the arena centre once the ring closes.
    if view.arena.collapse > 0:
        cx, cy = view.arena.width / 2, view.arena.height / 2
        dx, dy = cx - self_.x, cy - self_.y
        edge = view.arena.collapse + self_.radius + 30
        if dx * dx + dy * dy > edge * edge:
            to_centre = (math.degrees(math.atan2(dy, dx)) - self_.heading + 180) % 360 - 180
            action["turn"] = steer(view, (aim + to_centre) / 2)

    return action
