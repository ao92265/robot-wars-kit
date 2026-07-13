"""Deterministic robot-combat engine. Headless-first; emits one frame per tick.
Locked engine file — participants never edit this.

Tick order: snapshot views -> decide -> move (wall-aware) -> lasers (simultaneous,
LoS-blocked by walls) -> launch rockets -> drop mines -> advance rockets -> trigger
mines -> cooldowns/deaths -> win check -> emit frame.

Same seed + same bots => byte-identical match (no wall-clock; per-robot seeded RNG;
entities processed in stable id order). Splash hits everyone in radius, owner
included — point-blank rockets and your own mines can kill you.
"""

import math
import random
import hashlib
import json
import re

from . import config
from . import house as house_ai
from .loadout import resolve_stats
from .view import build_view, _bearing_deg
from .sandbox import safe_decide, normalise_action
from . import geom

SHAPES = ("tank", "speeder", "orb", "spike")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean_appearance(a):
    """Cosmetic only — never touches physics. color None => renderer picks by id.
    accent = secondary livery color (stripe/roundel); None => renderer default."""
    a = a if isinstance(a, dict) else {}
    color = a.get("color")
    if not (isinstance(color, str) and _HEX.match(color)):
        color = None
    shape = a.get("shape")
    if shape not in SHAPES:
        shape = "tank"
    accent = a.get("accent")
    if not (isinstance(accent, str) and _HEX.match(accent)):
        accent = None
    return {"color": color, "shape": shape, "accent": accent}


class Robot:
    def __init__(self, rid, name, decide_fn, loadout, seed, appearance=None, team=None):
        self.id = rid
        self.name = name
        # Team label. Robots on the same team don't damage each other (no friendly
        # fire), share the win condition, and spawn together. A soloist's team is
        # its own id, so a free-for-all is just N teams of one (behaviour unchanged).
        self.team = team if team is not None else f"solo:{rid}"
        self.decide_fn = decide_fn
        self.loadout = dict(loadout)
        self.stats = resolve_stats(loadout)
        self.radius = self.stats["radius"]
        self.appearance = _clean_appearance(appearance)
        self.max_hp = self.stats["max_hp"]
        self.hp = self.max_hp
        self.x = 0.0
        self.y = 0.0
        self.heading_deg = 0.0
        self.cooldown = 0
        self.special_cd = 0
        self.rockets_left = config.ROCKET_AMMO
        self.traps_left = config.TRAP_COUNT
        self.rocket_cd = 0
        self.trap_cd = 0
        self.alive = True
        self.damage_dealt = 0
        self.death_tick = None
        self.last_dx = 0.0       # displacement applied last tick (ice momentum)
        self.last_dy = 0.0
        self.slow_ticks = 0      # >0 = movement slowed by a trap (pit/mine) this many ticks
        self.flipped_ticks = 0   # >0 = wheels-up: no move, no guns, no brain
        self.jam_ticks = 0       # >0 = main gun jammed (rockets/mines still work)
        self.od_ticks = 0        # >0 = overdrive powerup (hits land harder)
        self.shield_ticks = 0    # >0 = shield powerup (incoming combat damage halved)
        self.haste_ticks = 0     # >0 = haste powerup (faster legs)
        self.ram_cd = 0          # ticks until this robot can crunch/be crunched again
        self.heat = 0.0          # gun heat (0..HEAT_MAX); each shot adds, each tick sheds
        self.vent_ticks = 0      # >0 = overheated: gun force-venting (rockets/mines fine)
        self.rng = random.Random(seed)
        # Mishap RNG (misses / jams / flips): a SEPARATE stream so engine rolls
        # never shift the numbers a bot's decide() draws from view.rng.
        self.mishap_rng = random.Random(seed * 61 + 13)


class Rocket:
    __slots__ = ("id", "owner", "x", "y", "vx", "vy", "traveled")

    def __init__(self, eid, owner, x, y, vx, vy):
        self.id = eid
        self.owner = owner
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.traveled = 0.0


class Mine:
    __slots__ = ("id", "owner", "x", "y", "arm_in", "armed")

    def __init__(self, eid, owner, x, y):
        self.id = eid
        self.owner = owner
        self.x = x
        self.y = y
        self.arm_in = config.TRAP_ARM_TICKS
        self.armed = False


class Pickup:
    """A map crate that refills ammo or repairs HP, then respawns after a delay
    so the spot stays worth fighting over."""
    __slots__ = ("id", "x", "y", "kind", "active", "respawn_in")

    def __init__(self, pid, x, y, kind):
        self.id = pid
        self.x = x
        self.y = y
        self.kind = kind        # "rockets" | "traps" | "repair"
        self.active = True
        self.respawn_in = 0


class Arena:
    def __init__(self, width, height, time_cap, walls, hazards=None, weather="clear", wind=(0.0, 0.0)):
        self.width = width
        self.height = height
        self.tick = 0
        self.time_cap = time_cap
        self.walls = walls
        self.hazards = hazards or []     # [{"type","x","y","w","h"}, ...]
        self.weather = weather           # "clear" | "fog" | "wind"
        self.wind = wind                 # (wx, wy) drift applied to rockets under "wind"

    def collapse_width(self):
        """SUDDEN DEATH: width of the molten ring creeping in from every wall.
        0 until SUDDEN_DEATH_FRAC of the time cap, then grows stepwise, capped so
        the centre survives. Pure function of tick — deterministic, replay-safe."""
        start = int(self.time_cap * config.SUDDEN_DEATH_FRAC)
        if self.tick < start:
            return 0.0
        steps = (self.tick - start) // config.COLLAPSE_STEP_TICKS + 1
        return min(steps * config.COLLAPSE_STEP,
                   min(self.width, self.height) * config.COLLAPSE_MAX_FRAC)


def _pt_in_rect(px, py, rx, ry, rw, rh):
    return rx <= px <= rx + rw and ry <= py <= ry + rh


def _seg_point_dist(ax, ay, bx, by, px, py):
    """Shortest distance from point P to segment AB."""
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


class Game:
    def __init__(self, entries, seed=1, width=config.ARENA_W, height=config.ARENA_H,
                 time_cap=config.TIME_CAP, decider=None, walls=None,
                 hazards=None, pickups=None, weather=None, house=False):
        """entries: list of (name, decide_fn, loadout[, appearance[, team]]).
        decider: optional callable(robot, view) -> action dict (tournament mode).
        hazards: list of typed floor zones; pickups: list of crate dicts (see
        engine/maps.py). weather: None/"clear" (no effect, backward-compatible),
        a specific kind, or "roll" to pick one from the seed. house: add the
        GATEKEEPER house robot (team "house": can't win, patrols + rams)."""
        self.seed = seed
        wl = [tuple(w) for w in (walls if walls is not None else config.WALLS)]
        hz = [dict(h) for h in (hazards or [])]
        kind, wind = self._roll_weather(weather, seed)
        self.arena = Arena(width, height, time_cap, wl, hz, kind, wind)
        self.decider = decider
        self.robots = []
        for i, e in enumerate(entries):
            name, fn, loadout = e[0], e[1], e[2]
            appearance = e[3] if len(e) > 3 else None
            team = e[4] if len(e) > 4 else None
            self.robots.append(Robot(i, name, fn, loadout, seed * 1000 + i, appearance, team))
        if house:
            hid = len(self.robots)
            hr = Robot(hid, "Gatekeeper", house_ai.decide, dict(config.HOUSE_LOADOUT),
                       seed * 1000 + hid, {"color": "#ffb400", "shape": "spike"}, "house")
            hr.max_hp = hr.hp = config.HOUSE_HP     # engine-owned: budget rules don't apply
            self.robots.append(hr)
        self.pickups = [Pickup(j, p["x"], p["y"], p["kind"]) for j, p in enumerate(pickups or [])]
        self.rockets = []
        self.mines = []
        self.explosions = []   # FX events for the current tick only
        self.events = []       # one-shot terrain/pickup FX for the current tick
        self._eid = 0
        self._flipper_cd = {}   # hazard index -> tick when the paddle re-arms
        self._place()

    @staticmethod
    def _roll_weather(weather, seed):
        """Resolve the requested weather into (kind, wind_vector). Deterministic:
        'roll' draws from a dedicated seeded RNG so replays stay byte-exact."""
        if weather in (None, "clear"):
            return "clear", (0.0, 0.0)
        rng = random.Random(seed * 7919 + 101)
        kind = rng.choice(config.WEATHER_POOL) if weather == "roll" else weather
        wind = (0.0, 0.0)
        if kind == "wind":
            ang = rng.random() * 2 * math.pi
            wind = (math.cos(ang) * config.WEATHER_WIND_DRIFT,
                    math.sin(ang) * config.WEATHER_WIND_DRIFT)
        return kind, wind

    def _zone_at(self, x, y, kind):
        for h in self.arena.hazards:
            if h["type"] == kind and _pt_in_rect(x, y, h["x"], h["y"], h["w"], h["h"]):
                return True
        return False

    def _new_eid(self):
        self._eid += 1
        return self._eid

    def _spawn_clear(self, x, y, pad):
        if not (pad <= x <= self.arena.width - pad and pad <= y <= self.arena.height - pad):
            return False
        for h in self.arena.hazards:
            if _pt_in_rect(x, y, h["x"] - pad, h["y"] - pad, h["w"] + 2 * pad, h["h"] + 2 * pad):
                return False
        return True

    def _ring_spawn(self, cx, cy, radius, ang, pad):
        # A ring slot nudged off hazard rects (padded by the robot radius): try
        # the slot itself, then step radially (outward first — inward drifts
        # toward the colosseum lava), then fan the angle. Pure slot geometry,
        # no RNG — on hazard-free maps the dr=0/da=0 try wins immediately, so
        # placement there stays byte-identical to the locked behaviour.
        for da in (0.0, 0.05, -0.05, 0.10, -0.10, 0.15, -0.15):
            for dr in (0, 24, -24, 48, -48, 72, -72, 96, -96, 120, -120):
                x = cx + (radius + dr) * math.cos(ang + da)
                y = cy + (radius + dr) * math.sin(ang + da)
                if self._spawn_clear(x, y, pad):
                    return x, y
        return cx + radius * math.cos(ang), cy + radius * math.sin(ang)

    def _end_spawn(self, x, y, cx, cy, pad, horiz):
        # An opposite-ends slot nudged off floor traps — the event-day bug:
        # colosseum's water pools sit exactly under the 0.10/0.90 ends. March
        # TOWARD the centre (clears the trap AND keeps the lane to the opponent
        # open — nudging outward would leave the pool between the two robots),
        # fanning across the short axis as a fallback. Pure slot geometry, no
        # RNG — on hazard-free maps the first try wins immediately, so
        # placement there stays byte-identical to the locked behaviour.
        pad += 12.0     # grace: "clear" must not mean grazing the trap edge
        for side in (0.0, 24.0, -24.0, 48.0, -48.0, 72.0, -72.0):
            for i in range(13):
                if horiz:
                    nx = x + (24.0 * i if x <= cx else -24.0 * i)
                    ny = y + side
                else:
                    nx = x + side
                    ny = y + (24.0 * i if y <= cy else -24.0 * i)
                if self._spawn_clear(nx, ny, pad):
                    return nx, ny
        return x, y

    def _place(self):
        # The house robot (if any) starts fixed at the top-centre wall, facing the
        # pit — it never joins the contestant spawn logic, so adding it can't shift
        # anyone else's placement (or the RNG draws behind it).
        bots = []
        for r in self.robots:
            if r.team == "house":
                r.x, r.y = self.arena.width * 0.5, self.arena.height * 0.10
                r.heading_deg = 90.0
            else:
                bots.append(r)
        n = len(bots)
        cx, cy = self.arena.width / 2, self.arena.height / 2
        radius = min(self.arena.width, self.arena.height) * 0.40
        rng = random.Random(self.seed * 31 + 17)
        offset = rng.random() * 2 * math.pi
        # Real teams (a team with >1 member)? Then spawn teammates in a contiguous
        # arc so each side reads as a group. Pure free-for-all keeps the original
        # shuffled ring untouched (byte-identical to the locked behaviour).
        team_order = []
        for r in bots:
            if r.team not in team_order:
                team_order.append(r.team)
        # Two explicit teams (a 1v1 or team-vs-team)? Charge from OPPOSITE ENDS of
        # the longer axis — a dramatic approach across the arena, not an orbit near
        # the centre. All-solo free-for-all keeps the original ring (byte-identical).
        solo_only = all(str(r.team).startswith("solo:") for r in bots)
        if len(team_order) == 2 and not solo_only:
            horiz = self.arena.width >= self.arena.height
            end = {team_order[0]: 0.10, team_order[1]: 0.90}
            for team in team_order:
                members = sorted((r for r in bots if r.team == team), key=lambda r: r.id)
                k = len(members)
                for j, r in enumerate(members):
                    frac = (j + 1) / (k + 1)          # spread teammates across the short axis
                    if horiz:
                        x, y = self.arena.width * end[team], self.arena.height * (0.22 + 0.56 * frac)
                    else:
                        x, y = self.arena.width * (0.22 + 0.56 * frac), self.arena.height * end[team]
                    r.x, r.y = self._end_spawn(x, y, cx, cy, r.radius, horiz)
                    r.heading_deg = math.degrees(math.atan2(cy - r.y, cx - r.x)) % 360.0
            return
        has_teams = len(team_order) < n
        if has_teams:
            ordered = sorted(bots, key=lambda r: (team_order.index(r.team), r.id))
            for slot, r in enumerate(ordered):
                ang = offset + 2 * math.pi * slot / max(1, n)
                r.x, r.y = self._ring_spawn(cx, cy, radius, ang, r.radius)
                r.heading_deg = math.degrees(math.atan2(cy - r.y, cx - r.x)) % 360.0
            return
        slots = list(range(n))
        rng.shuffle(slots)
        for r, slot in zip(bots, slots):
            ang = offset + 2 * math.pi * slot / max(1, n)
            r.x, r.y = self._ring_spawn(cx, cy, radius, ang, r.radius)
            r.heading_deg = math.degrees(math.atan2(cy - r.y, cx - r.x)) % 360.0

    # ---- tick phases -------------------------------------------------
    def _decide_all(self):
        actions = {}
        for r in self.robots:
            if not r.alive:
                continue
            if r.flipped_ticks > 0:
                # wheels-up: the brain is offline until you self-right
                actions[r.id] = normalise_action({})
                continue
            view = build_view(r, self.robots, self.arena, self.rockets, self.mines, self.pickups)
            if self.decider is not None and r.team != "house":
                # tournament mode: decider output comes from a subprocess, so
                # normalise it here too — never trust it to be a full action dict.
                # (The house robot is engine-owned: always in-process.)
                actions[r.id] = normalise_action(self.decider(r, view))
            else:
                actions[r.id] = safe_decide(r.decide_fn, view)
        return actions

    def _clamp_to_arena(self, r):
        r.x = max(r.radius, min(self.arena.width - r.radius, r.x))
        r.y = max(r.radius, min(self.arena.height - r.radius, r.y))

    def _apply_movement(self, actions):
        for r in self.robots:
            if not r.alive:
                continue
            # hover engines skim the surface: ice physics never grabs them
            on_ice = (not r.stats["hover"]) and self._zone_at(r.x, r.y, "ice")   # decided at the pre-move spot
            a = actions[r.id]
            turn = max(-r.stats["turn_rate_deg"], min(r.stats["turn_rate_deg"], a["turn"]))
            if on_ice:
                turn *= config.ICE_TURN_MULT          # less grip = sloppier steering
            r.heading_deg = (r.heading_deg + turn) % 360.0
            speed = 0.0
            if a["special"] and r.stats["special_level"] > 0 and r.special_cd == 0:
                speed = r.stats["move_speed"] * config.DASH_FACTOR
                r.special_cd = max(20, config.DASH_COOLDOWN_BASE - r.stats["special_level"] * 8)
            elif a["thrust"] == "forward":
                speed = r.stats["move_speed"]
            elif a["thrust"] == "back":
                speed = -config.BACK_SPEED_FACTOR * r.stats["move_speed"]
            if r.slow_ticks > 0:
                speed *= config.HAZARD_SLOW_MULT      # a trap (pit/mine) is dragging on you
            if r.haste_ticks > 0:
                speed *= config.HASTE_MULT            # haste powerup: faster legs
            rad = math.radians(r.heading_deg)
            dx, dy = speed * math.cos(rad), speed * math.sin(rad)
            if on_ice:
                # slide: keep most of last tick's motion, only partly command the new one
                dx = dx * config.ICE_CONTROL + r.last_dx * config.ICE_SLIP
                dy = dy * config.ICE_CONTROL + r.last_dy * config.ICE_SLIP
            px, py = r.x, r.y
            r.x += dx
            r.y += dy
            self._clamp_to_arena(r)
            for w in self.arena.walls:
                r.x, r.y = geom.circle_pushout(r.x, r.y, r.radius, w)
            self._clamp_to_arena(r)
            # Ice momentum carries the ACTUAL post-collision displacement, so a bot
            # that got shoved back by a wall doesn't slide next tick on motion it
            # never really made. (Off ice last_dx is never read, so this is a no-op
            # for hazard-free maps — FFA stays byte-identical.)
            r.last_dx, r.last_dy = r.x - px, r.y - py
        self._separate()

    def _separate(self):
        alive = [r for r in self.robots if r.alive]
        for _ in range(2):
            for i in range(len(alive)):
                for j in range(i + 1, len(alive)):
                    a, b = alive[i], alive[j]
                    dx, dy = b.x - a.x, b.y - a.y
                    d = math.hypot(dx, dy) or 0.001
                    overlap = (a.radius + b.radius) - d
                    if overlap > 0:
                        nx, ny = dx / d, dy / d
                        self._ram(a, b, nx, ny)
                        a.x -= nx * overlap / 2; a.y -= ny * overlap / 2
                        b.x += nx * overlap / 2; b.y += ny * overlap / 2
            for r in alive:
                self._clamp_to_arena(r)

    def _ram(self, a, b, nx, ny):
        """Enemy robots slamming together CRUNCH. Damage scales with closing speed
        (this tick's actual displacements) and splits by mass (radius²) — the heavy
        one bulldozes, the light one bounces. Both go on ram cooldown so contact is
        a hit, not a grinder. A hard crunch can flip the victim (mishap RNG, so bot
        decision streams stay untouched). Deterministic damage, replay-safe."""
        if a.team == b.team or a.ram_cd > 0 or b.ram_cd > 0:
            return
        closing = ((a.last_dx - b.last_dx) * nx + (a.last_dy - b.last_dy) * ny)
        if closing < config.RAM_MIN_SPEED:
            return
        base = min(config.RAM_MAX, (closing - config.RAM_MIN_SPEED) * config.RAM_DAMAGE_SCALE)
        if base <= 0:
            return
        ma, mb = a.radius ** 2, b.radius ** 2
        total = 0
        for victim, attacker, share in ((b, a, ma / (ma + mb)), (a, b, mb / (ma + mb))):
            dmg = base * share * 2.0
            if attacker.od_ticks > 0:
                dmg *= config.OVERDRIVE_MULT       # overdrive: every hit lands harder
            if victim.shield_ticks > 0:
                dmg *= config.SHIELD_MULT          # shield absorbs combat damage
            dmg = int(round(dmg))
            if dmg <= 0:
                continue
            hp_before = victim.hp
            victim.hp -= dmg
            attacker.damage_dealt += hp_before - max(0, victim.hp)
            total += dmg
            # a hard crunch can toss the victim wheels-up
            if victim.hp > 0 and victim.flipped_ticks == 0:
                chance = (config.RAM_FLIP_CHANCE * (dmg / config.RAM_MAX)
                          * config.FLIP_SIZE[victim.stats["size"]]
                          * config.FLIP_ENGINE[victim.stats["engine"]])
                if victim.mishap_rng.random() < chance:
                    victim.flipped_ticks = config.FLIP_TICKS
                    victim.last_dx = victim.last_dy = 0.0
                    self.events.append({"kind": "flip", "id": victim.id,
                                        "x": round(victim.x, 1), "y": round(victim.y, 1)})
        if total > 0:
            # cooldown only on a crunch that actually landed (zero-damage taps stay live)
            a.ram_cd = b.ram_cd = config.RAM_COOLDOWN
            self.events.append({"kind": "ram", "dmg": total,
                                "x": round((a.x + b.x) / 2, 1), "y": round((a.y + b.y) / 2, 1)})

    def _spin_turntables(self):
        """Turntables: rotating floor discs (the rect's inscribed circle).
        Grounded robots on the platter are carried around its centre and spun —
        position and heading rotate together. Hover bots skim it."""
        w = math.radians(config.TURNTABLE_DEG_PER_TICK)
        for h in self.arena.hazards:
            if h["type"] != "turntable":
                continue
            cx, cy = h["x"] + h["w"] / 2.0, h["y"] + h["h"] / 2.0
            rad = min(h["w"], h["h"]) / 2.0
            for r in self.robots:
                if not r.alive or r.stats["hover"]:
                    continue
                dx, dy = r.x - cx, r.y - cy
                d = math.hypot(dx, dy)
                if d > rad:
                    continue
                # carried around the centre + slung outward (centrifugal drift)
                rx = cx + dx * math.cos(w) - dy * math.sin(w)
                ry = cy + dx * math.sin(w) + dy * math.cos(w)
                if d > 1e-6:
                    rx += (dx / d) * config.TURNTABLE_FLING
                    ry += (dy / d) * config.TURNTABLE_FLING
                r.x, r.y = rx, ry
                r.heading_deg = (r.heading_deg + config.TURNTABLE_DEG_PER_TICK) % 360.0

    def _fire_flippers(self):
        """Floor flippers: armed steel paddles set into the floor. The first
        robot over one gets hurled FLIPPER_THROW units (away from the paddle,
        jittered), lands wheels-up, and the paddle goes on cooldown."""
        for i, h in enumerate(self.arena.hazards):
            if h["type"] != "flipper" or self._flipper_cd.get(i, 0) > self.arena.tick:
                continue
            for r in self.robots:
                if not r.alive or not _pt_in_rect(r.x, r.y, h["x"], h["y"], h["w"], h["h"]):
                    continue
                self._flipper_cd[i] = self.arena.tick + config.FLIPPER_COOLDOWN
                cx, cy = h["x"] + h["w"] / 2.0, h["y"] + h["h"] / 2.0
                ang = math.atan2(r.y - cy, r.x - cx) + (r.rng.random() - 0.5) * 1.1
                r.x += math.cos(ang) * config.FLIPPER_THROW
                r.y += math.sin(ang) * config.FLIPPER_THROW
                self._clamp_to_arena(r)
                for w in self.arena.walls:
                    r.x, r.y = geom.circle_pushout(r.x, r.y, r.radius, w)
                self._clamp_to_arena(r)
                r.hp = max(config.HAZARD_MIN_HP, r.hp - config.FLIPPER_DAMAGE)
                r.flipped_ticks = max(r.flipped_ticks, config.FLIP_TICKS)
                self.events.append({"kind": "flipper", "id": r.id,
                                    "x": round(cx, 1), "y": round(cy, 1)})
                self.events.append({"kind": "flip", "id": r.id,
                                    "x": round(r.x, 1), "y": round(r.y, 1)})
                break                       # one launch per firing

    def _apply_terrain(self):
        """Lava burns; a pit or water hurts + slows (but never kills). Decided by
        each robot's centre. In SUDDEN DEATH the collapsing molten ring burns
        EVERYONE inside it — hover included (it's heat), no mercy floor."""
        self._fire_flippers()
        self._spin_turntables()
        cw = self.arena.collapse_width()
        for r in self.robots:
            if not r.alive:
                continue
            if cw > 0 and (r.x < cw or r.x > self.arena.width - cw
                           or r.y < cw or r.y > self.arena.height - cw):
                r.hp -= config.COLLAPSE_DPS
                self.events.append({"kind": "lava", "x": round(r.x, 1), "y": round(r.y, 1)})
            if r.stats["hover"]:
                # hover skims over pits and water entirely; lava still burns (heat).
                if self._zone_at(r.x, r.y, "lava"):
                    r.hp -= config.LAVA_DPS
                    self.events.append({"kind": "lava", "x": round(r.x, 1), "y": round(r.y, 1)})
                continue
            if self._zone_at(r.x, r.y, "pit"):
                # A trap, not an executioner: chip HP but floor at HAZARD_MIN_HP,
                # and drag the bot so it can't just power straight through.
                r.hp = max(config.HAZARD_MIN_HP, r.hp - config.PIT_DAMAGE)
                r.slow_ticks = max(r.slow_ticks, config.PIT_SLOW_TICKS)
                self.events.append({"kind": "pit", "x": round(r.x, 1), "y": round(r.y, 1)})
            if self._zone_at(r.x, r.y, "water"):
                # Wading: heavy drag + light rust, but never a kill (floored like a trap).
                r.hp = max(config.HAZARD_MIN_HP, r.hp - config.WATER_DPS)
                r.slow_ticks = max(r.slow_ticks, config.WATER_SLOW_TICKS)
                self.events.append({"kind": "water", "x": round(r.x, 1), "y": round(r.y, 1)})
            if self._zone_at(r.x, r.y, "lava"):
                r.hp -= config.LAVA_DPS
                self.events.append({"kind": "lava", "x": round(r.x, 1), "y": round(r.y, 1)})

    def _collect_pickups(self):
        """First robot within reach of an active crate collects it; the crate then
        goes dormant and respawns later. Repair is capped at max HP."""
        for p in self.pickups:
            if not p.active:
                continue
            for r in self.robots:
                if not r.alive or r.team == "house":
                    continue    # the Gatekeeper doesn't loot — crates are for contestants
                if math.hypot(r.x - p.x, r.y - p.y) <= config.PICKUP_RADIUS + r.radius:
                    if p.kind == "rockets":
                        r.rockets_left += config.PICKUP_ROCKETS
                    elif p.kind == "traps":
                        r.traps_left += config.PICKUP_TRAPS
                    elif p.kind == "repair":
                        r.hp = min(r.max_hp, r.hp + config.PICKUP_REPAIR)
                    elif p.kind == "overdrive":
                        r.od_ticks = config.POWERUP_TICKS
                    elif p.kind == "shield":
                        r.shield_ticks = config.POWERUP_TICKS
                    elif p.kind == "haste":
                        r.haste_ticks = config.POWERUP_TICKS
                    p.active = False
                    p.respawn_in = config.PICKUP_RESPAWN
                    self.events.append({"kind": "pickup", "x": round(p.x, 1),
                                        "y": round(p.y, 1), "type": p.kind})
                    break

    def _tick_pickups(self):
        for p in self.pickups:
            if not p.active:
                p.respawn_in -= 1
                if p.respawn_in <= 0:
                    p.active = True

    def _fog_mult(self):
        return config.WEATHER_FOG_RANGE if self.arena.weather == "fog" else 1.0

    def _resolve_lasers(self, actions):
        """All three gun archetypes are hitscan variants of the same resolve: the
        numbers (range/arc/damage/cooldown) come pre-multiplied from resolve_stats.
        A multi gun (shotgun) damages EVERY valid enemy in the cone; single guns
        (laser/cannon) hit the nearest — the locked laser path is byte-identical."""
        fired = []
        pending = []
        fog = self._fog_mult()
        for r in self.robots:
            if not r.alive or actions[r.id]["fire"] != "laser" or r.cooldown > 0:
                continue
            if r.jam_ticks > 0 or r.vent_ticks > 0:
                continue                              # jammed or overheated — trigger does nothing
            # jam roll: guns are machines, machines break (deterministic mishap RNG)
            if r.mishap_rng.random() < config.JAM_CHANCE[r.stats["gun"]]:
                r.jam_ticks = config.JAM_TICKS
                self.events.append({"kind": "jam", "id": r.id,
                                    "x": round(r.x, 1), "y": round(r.y, 1)})
                continue
            # heat: every shot warms the gun; hit the ceiling -> forced VENT
            r.heat += config.HEAT_PER_SHOT[r.stats["gun"]]
            if r.heat >= config.HEAT_MAX:
                r.heat = float(config.HEAT_MAX)
                r.vent_ticks = config.VENT_TICKS
                self.events.append({"kind": "vent", "id": r.id,
                                    "x": round(r.x, 1), "y": round(r.y, 1)})
                # this trigger-pull still fires (the OVERHEAT is the price)
            wr = r.stats["weapon_range"] * fog       # fog shortens your reach
            target, best = None, None
            in_cone = []                             # (dist, enemy), every valid target
            for e in self.robots:
                if e.id == r.id or not e.alive or e.team == r.team:
                    continue  # never lock onto self or a teammate
                dist = math.hypot(e.x - r.x, e.y - r.y)
                if dist > wr:
                    continue
                if abs(_bearing_deg(r, e.x, e.y)) > r.stats["weapon_arc_deg"]:
                    continue
                if geom.los_blocked(r.x, r.y, e.x, e.y, self.arena.walls):
                    continue
                in_cone.append((dist, e))
                if best is None or dist < best:
                    best, target = dist, e
            r.cooldown = r.stats["cooldown_ticks"]
            landed_any = False
            miss_pt = None
            if target is not None:
                victims = in_cone if r.stats["gun_multi"] else [(best, target)]
                snap = abs(actions[r.id]["turn"]) > 20.0     # snap-shooting while turning hard
                for dist, e in victims:
                    ratio = dist / wr
                    # accuracy: base per gun − long-shot risk − target motion − snap penalty
                    chance = (config.ACCURACY[r.stats["gun"]]
                              - config.ACC_RANGE_PENALTY * ratio * ratio
                              - config.ACC_TARGET_MOTION * math.hypot(e.last_dx, e.last_dy)
                              - (config.ACC_SNAP_PENALTY if snap else 0.0))
                    if r.mishap_rng.random() >= max(config.ACC_MIN, chance):
                        # MISS — tracer sails past the target (jitter for the renderer)
                        if miss_pt is None:
                            ux, uy = (e.x - r.x) / (dist or 1.0), (e.y - r.y) / (dist or 1.0)
                            over = min(wr, dist + 90.0)                    # sail past the target
                            jit = (r.mishap_rng.random() - 0.5) * 50.0     # sideways scatter
                            miss_pt = (r.x + ux * over - uy * jit,
                                       r.y + uy * over + ux * jit)
                        continue
                    landed_any = True
                    dmg = max(1, int(round(r.stats["weapon_damage"] * (1.0 - config.DAMAGE_FALLOFF * ratio))))
                    pending.append((r, e, dmg))
            entry = {"f": r.id, "t": (target.id if target else None), "hit": landed_any,
                     "gun": r.stats["gun"]}
            if target is not None and not landed_any and miss_pt is not None:
                entry["mx"], entry["my"] = round(miss_pt[0], 1), round(miss_pt[1], 1)
            fired.append(entry)
        for shooter, target, dmg in pending:
            # powerups: shooter overdrive amplifies, target shield absorbs (floor 1)
            if shooter.od_ticks > 0:
                dmg = int(round(dmg * config.OVERDRIVE_MULT))
            if target.shield_ticks > 0:
                dmg = max(1, int(round(dmg * config.SHIELD_MULT)))
            hp_before = target.hp
            target.hp -= dmg
            # credit only HP actually removed (overkill doesn't pad the tiebreak)
            shooter.damage_dealt += hp_before - max(0, target.hp)
        return fired

    def _launch_rockets(self, actions):
        for r in self.robots:
            if not r.alive or r.flipped_ticks > 0 or actions[r.id]["fire"] != "rocket":
                continue
            if r.rockets_left <= 0 or r.rocket_cd > 0:
                continue
            r.rockets_left -= 1
            r.rocket_cd = config.ROCKET_COOLDOWN
            rad = math.radians(r.heading_deg)
            ux, uy = math.cos(rad), math.sin(rad)
            spawn = r.radius + config.ROCKET_RADIUS + 1.0
            rk = Rocket(self._new_eid(), r.id,
                        r.x + ux * spawn, r.y + uy * spawn,
                        ux * config.ROCKET_SPEED, uy * config.ROCKET_SPEED)
            self.rockets.append(rk)

    def _drop_mines(self, actions):
        for r in self.robots:
            if not r.alive or r.flipped_ticks > 0 or not actions[r.id]["drop_trap"]:
                continue
            if r.traps_left <= 0 or r.trap_cd > 0:
                continue
            r.traps_left -= 1
            r.trap_cd = config.TRAP_DROP_COOLDOWN
            self.mines.append(Mine(self._new_eid(), r.id, r.x, r.y))

    def _explode(self, x, y, blast_r, blast_dmg, owner_id, nonlethal=False, slow=0):
        """Radial splash. Credits the owner only for damage dealt to OTHERS.
        No friendly fire: a teammate (different robot, same team) takes no splash.
        The owner still hurts ITSELF (point-blank rockets / own mines bite) — this
        keeps a free-for-all byte-identical and preserves the 'test your exploit'
        lesson. `nonlethal` floors victims at HAZARD_MIN_HP (traps hurt, don't
        execute); `slow` drags each victim for that many ticks."""
        self.explosions.append({"x": round(x, 1), "y": round(y, 1), "r": blast_r})
        owner = self.robots[owner_id] if 0 <= owner_id < len(self.robots) else None
        for rb in self.robots:
            if not rb.alive:
                continue
            if owner is not None and rb.id != owner_id and rb.team == owner.team:
                continue  # teammate shielded from friendly splash
            d = math.hypot(rb.x - x, rb.y - y)
            if d >= blast_r:
                continue
            dmg = int(round(blast_dmg * (1.0 - d / blast_r)))
            # powerups: owner overdrive amplifies the blast, victim shield absorbs
            if owner is not None and owner.od_ticks > 0:
                dmg = int(round(dmg * config.OVERDRIVE_MULT))
            if rb.shield_ticks > 0:
                dmg = int(round(dmg * config.SHIELD_MULT))
            if dmg <= 0:
                continue
            hp_before = rb.hp
            rb.hp -= dmg
            if nonlethal:
                rb.hp = max(config.HAZARD_MIN_HP, rb.hp)
            if slow:
                rb.slow_ticks = max(rb.slow_ticks, slow)
            if owner is not None and rb.id != owner_id:
                owner.damage_dealt += hp_before - rb.hp   # credit only HP actually removed
            # FLIP roll: a close blast can toss you wheels-up (classic Robot Wars).
            # Proximity × chassis size × engine stability; deterministic mishap RNG.
            if rb.alive and rb.hp > 0 and rb.flipped_ticks == 0:
                chance = (config.FLIP_CHANCE * (1.0 - d / blast_r)
                          * config.FLIP_SIZE[rb.stats["size"]]
                          * config.FLIP_ENGINE[rb.stats["engine"]])
                if rb.mishap_rng.random() < chance:
                    rb.flipped_ticks = config.FLIP_TICKS
                    # tossed airborne: ground momentum dies (no ice-sliding on your back)
                    rb.last_dx = rb.last_dy = 0.0
                    self.events.append({"kind": "flip", "id": rb.id,
                                        "x": round(rb.x, 1), "y": round(rb.y, 1)})

    def _advance_rockets(self):
        alive_targets = [r for r in self.robots if r.alive]
        wx, wy = self.arena.wind            # (0,0) unless weather == "wind"
        rng_cap = config.ROCKET_RANGE * self._fog_mult()   # fog shortens flight too
        survivors = []
        for rk in self.rockets:
            nx, ny = rk.x + rk.vx + wx, rk.y + rk.vy + wy
            step = math.hypot(rk.vx, rk.vy)
            impact = None  # (x, y)
            # wall hit?
            for w in self.arena.walls:
                if geom.seg_intersects_rect(rk.x, rk.y, nx, ny, w):
                    impact = (nx, ny)
                    break
            # robot hit (nearest along the step), excluding the owner and its team
            owner_team = self.robots[rk.owner].team if 0 <= rk.owner < len(self.robots) else None
            best_d = None
            for rb in alive_targets:
                if rb.id == rk.owner or rb.team == owner_team:
                    continue  # rockets pass through the owner and teammates
                d = _seg_point_dist(rk.x, rk.y, nx, ny, rb.x, rb.y)
                if d <= rb.radius + config.ROCKET_RADIUS:
                    md = math.hypot(rb.x - rk.x, rb.y - rk.y)
                    if best_d is None or md < best_d:
                        best_d = md
                        impact = (rb.x, rb.y)
            rk.x, rk.y = nx, ny
            rk.traveled += step
            out = not (0 <= rk.x <= self.arena.width and 0 <= rk.y <= self.arena.height)
            if impact is not None:
                self._explode(impact[0], impact[1], config.ROCKET_BLAST_RADIUS,
                              config.ROCKET_BLAST_DAMAGE, rk.owner)
            elif rk.traveled >= rng_cap or out:
                self._explode(rk.x, rk.y, config.ROCKET_BLAST_RADIUS,
                              config.ROCKET_BLAST_DAMAGE, rk.owner)
            else:
                survivors.append(rk)
        self.rockets = survivors

    def _trigger_mines(self):
        survivors = []
        for mn in self.mines:
            if mn.arm_in > 0:
                mn.arm_in -= 1
                if mn.arm_in == 0:
                    mn.armed = True
                survivors.append(mn)
                continue
            triggered = False
            mn_team = self.robots[mn.owner].team if 0 <= mn.owner < len(self.robots) else None
            for rb in self.robots:
                if not rb.alive or rb.id == mn.owner or rb.team == mn_team:
                    continue  # only an ENEMY sets off the mine, never a teammate
                if math.hypot(rb.x - mn.x, rb.y - mn.y) <= config.TRAP_TRIGGER_RADIUS + rb.radius:
                    triggered = True
                    break
            if triggered:
                self._explode(mn.x, mn.y, config.TRAP_BLAST_RADIUS,
                              config.TRAP_BLAST_DAMAGE, mn.owner,
                              nonlethal=True, slow=config.TRAP_SLOW_TICKS)
            else:
                survivors.append(mn)
        self.mines = survivors

    def _status_and_deaths(self):
        for r in self.robots:
            r.heat = max(0.0, r.heat - config.HEAT_COOL)   # guns shed heat every tick
            for attr in ("cooldown", "special_cd", "rocket_cd", "trap_cd", "slow_ticks", "ram_cd", "vent_ticks"):
                v = getattr(r, attr)
                if v > 0:
                    setattr(r, attr, v - 1)
            # tank engines shrug drag off twice as fast (slow_ticks burn at 2/tick)
            if r.stats["slow_resist"] and r.slow_ticks > 0:
                r.slow_ticks -= 1
            # self-righting: flipped bots wind down; jams clear; tanks right at 2x
            if r.flipped_ticks > 0:
                r.flipped_ticks -= 2 if r.stats["slow_resist"] else 1
                if r.flipped_ticks < 0:
                    r.flipped_ticks = 0
            if r.jam_ticks > 0:
                r.jam_ticks -= 1
            for attr in ("od_ticks", "shield_ticks", "haste_ticks"):
                v = getattr(r, attr)
                if v > 0:
                    setattr(r, attr, v - 1)
            if r.alive and r.hp <= 0:
                r.hp = 0
                r.alive = False
                r.death_tick = self.arena.tick

    def _frame(self, fired):
        # alive counts CONTESTANTS only — the house robot is scenery with teeth
        alive_n = sum(1 for r in self.robots if r.alive and r.team != "house")
        return {
            "tick": self.arena.tick,
            "robots": [{"id": r.id, "name": r.name, "x": round(r.x, 1), "y": round(r.y, 1),
                        "heading": round(r.heading_deg, 1), "hp": r.hp, "max_hp": r.max_hp,
                        "alive": r.alive, "dmg": r.damage_dealt, "r": round(r.radius, 1),
                        "rkt": r.rockets_left, "trp": r.traps_left, "team": r.team,
                        "slow": r.slow_ticks, "flip": r.flipped_ticks, "jam": r.jam_ticks,
                        "od": r.od_ticks, "sh": r.shield_ticks, "hs": r.haste_ticks,
                        "heat": int(round(r.heat)), "vent": r.vent_ticks,
                        "gun": r.stats["gun"], "eng": r.stats["engine"],
                        "color": r.appearance["color"], "shape": r.appearance["shape"],
                        "accent": r.appearance["accent"]}
                       for r in self.robots],
            "fired": fired,
            "rockets": [{"id": rk.id, "x": round(rk.x, 1), "y": round(rk.y, 1), "owner": rk.owner}
                        for rk in self.rockets],
            "mines": [{"id": mn.id, "x": round(mn.x, 1), "y": round(mn.y, 1),
                       "owner": mn.owner, "armed": mn.armed} for mn in self.mines],
            "pickups": [{"id": p.id, "x": round(p.x, 1), "y": round(p.y, 1),
                         "kind": p.kind, "active": p.active} for p in self.pickups],
            "explosions": list(self.explosions),
            "events": list(self.events),
            "status": {"alive": alive_n, "time_left": self.arena.time_cap - self.arena.tick,
                       "w": self.arena.width, "h": self.arena.height, "walls": self.arena.walls,
                       "hazards": self.arena.hazards, "weather": self.arena.weather,
                       "collapse": round(self.arena.collapse_width(), 1),
                       "wind": [round(self.arena.wind[0], 2), round(self.arena.wind[1], 2)]},
        }

    def step(self):
        self.explosions = []
        self.events = []
        actions = self._decide_all()
        self._apply_movement(actions)
        self._apply_terrain()      # lava burns, pits eliminate (post-move)
        self._collect_pickups()    # grab any crate you reached
        fired = self._resolve_lasers(actions)
        self._launch_rockets(actions)
        self._drop_mines(actions)
        self._advance_rockets()
        self._trigger_mines()
        self._status_and_deaths()
        self._tick_pickups()       # respawn timers
        frame = self._frame(fired)
        self.arena.tick += 1
        return frame

    # ---- match driver ------------------------------------------------
    def run(self, collect_frames=True):
        frames = []
        while True:
            frame = self.step()
            if collect_frames:
                frames.append(frame)
            alive = [r for r in self.robots if r.alive and r.team != "house"]
            # Match ends when at most one CONTESTANT team has robots left (a soloist
            # is a team of one, so a free-for-all ends exactly as before; the house
            # robot never keeps a match alive) or the clock runs out.
            if len({r.team for r in alive}) <= 1 or self.arena.tick >= self.arena.time_cap:
                break
        return self._result(frames)

    def _result(self, frames):
        # The house robot is not a contestant: it can't win, rank, or appear in
        # standings — it exists to be avoided (or heroically slain, for nothing).
        contestants = [r for r in self.robots if r.team != "house"]
        alive = [r for r in contestants if r.alive]
        alive_teams = {r.team for r in alive}
        if len(alive_teams) == 1:
            winner_team = next(iter(alive_teams))
            reason = "last standing"
        elif len(alive_teams) == 0:
            winner_team, reason = None, "mutual KO"
        else:
            # Time cap: the team with the most surviving HP wins (then total damage).
            by_team = {}
            for r in alive:
                t = by_team.setdefault(r.team, {"hp": 0, "dmg": 0})
                t["hp"] += r.hp
                t["dmg"] += r.damage_dealt
            winner_team = max(by_team, key=lambda t: (by_team[t]["hp"], by_team[t]["dmg"]))
            reason = "time cap"
        # Lead robot of the winning team (top HP) fills the single-winner fields so
        # the renderer and older callers keep working.
        winner = None
        if winner_team is not None:
            members = [r for r in contestants if r.team == winner_team]
            winner = max(members, key=lambda r: (r.alive, r.hp, r.damage_dealt, -r.id))
        ranking = sorted(
            contestants,
            key=lambda r: (r.alive, r.death_tick or -1, r.hp, r.damage_dealt, -r.id),
            reverse=True,
        )
        standings = [{"id": r.id, "name": r.name, "team": r.team, "alive": r.alive, "hp": r.hp,
                      "damage_dealt": r.damage_dealt, "death_tick": r.death_tick}
                     for r in ranking]
        return {
            "winner_id": (winner.id if winner else None),
            "winner_name": (winner.name if winner else None),
            "winner_team": winner_team,
            "ranking": [r.id for r in ranking],
            "standings": standings,
            "reason": reason,
            "seed": self.seed,
            "ticks": self.arena.tick,
            "hash": self.state_hash(),
            "frames": frames,
        }

    def state_hash(self):
        blob = json.dumps(
            [[r.id, r.hp, round(r.x, 3), round(r.y, 3), r.death_tick, r.damage_dealt,
              r.rockets_left, r.traps_left] for r in self.robots]
            + [["rk", rk.id, round(rk.x, 3), round(rk.y, 3)] for rk in self.rockets]
            + [["mn", mn.id, round(mn.x, 3), round(mn.y, 3), mn.armed] for mn in self.mines],
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
