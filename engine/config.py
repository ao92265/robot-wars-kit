"""All tunable game knobs in one place. Edit these in playtest — no code changes
needed elsewhere. (Locked engine file in spirit, but THIS is the dial board.)"""

# --- loadout ---------------------------------------------------------------
BUDGET = 12          # total points a team may spend
STAT_CAP = 6         # max points in any single stat

# stat -> effect curves:  value = base + points * per
CURVES = {
    "hp":      {"base": 200, "per": 15},    # max HP        200..290 (a point-blank brawl lasts ~18-22s and still ends by KO, not the clock)
    "speed":   {"base": 7.0, "per": 3.2},   # units/tick    7..26
    "damage":  {"base": 8,   "per": 5},     # per hit       8..38   (softened vs the original 9/6 but with enough throughput to end fights by KO, not the clock)
    "range":   {"base": 110, "per": 60},    # reach (units) 110..470
    "agility": {"base": 34.0, "per": 3.0},  # turn deg/tick 34..52  (0 points = the locked classic turn rate)
}
WEAPON_ARC_DEG = 16.0     # firing arc half-angle (rewards aim)
COOLDOWN_TICKS = 16       # reload between shots (lengthened to slow the damage race)
TURN_RATE_DEG = 34.0      # legacy constant (agility base mirrors it; kept for older callers)

# --- accuracy: shots can MISS ------------------------------------------------
# Every gunshot rolls a deterministic hit chance (per-robot mishap RNG — bots'
# view.rng streams are untouched). Stationary point-blank ≈ never misses; a fast
# small target at max range while you're snap-turning is a genuine gamble.
ACCURACY = {"laser": 0.97, "cannon": 0.88, "shotgun": 0.95}   # base chance per gun (shotgun: per victim)
ACC_RANGE_PENALTY = 0.25   # × (dist/range)^2 — long shots are risky
ACC_TARGET_MOTION = 0.012  # × target's last-tick displacement (units) — movers dodge
ACC_SNAP_PENALTY = 0.10    # you turned hard (>20°) while pulling the trigger
ACC_MIN = 0.2              # even the wildest shot has a 1-in-5 chance

# --- flips: blasts can toss you (classic Robot Wars) -------------------------
# A rocket/mine blast that damages you may flip you wheels-up: helpless (no move,
# no guns, no brain) until you self-right. Heavy tank engines barely flip and
# right fast; small chassis and hovers get tossed like pancakes.
FLIP_CHANCE = 0.35         # at blast centre, scaled by (1 - d/blast_r)
FLIP_TICKS = 45            # ticks spent wheels-up (tank engines right at 2x speed)
FLIP_SIZE = {"small": 1.3, "medium": 1.0, "large": 0.7}
FLIP_ENGINE = {"standard": 1.0, "sprint": 1.1, "tank": 0.45, "hover": 1.25}

# --- ramming: the spinner is REAL — collisions hurt --------------------------
# When two ENEMY robots slam together fast enough, both take crunch damage split
# by mass (radius²): the heavy one bulldozes, the light one bounces. Damage scales
# with closing speed, so a dash-charge is a weapon and a gentle nudge is nothing.
# A hard ram can even FLIP the victim. Deterministic (no dice on the damage).
RAM_MIN_SPEED = 10.0      # min closing speed (units/tick) before contact counts as a ram
RAM_DAMAGE_SCALE = 0.8    # damage per unit of closing speed over the minimum
RAM_MAX = 30              # cap per single crunch
RAM_COOLDOWN = 14         # ticks per robot between ram hits (no grind-to-death)
RAM_FLIP_CHANCE = 0.3     # at max-damage ram, scaled by dmg + size/engine flip factors

# --- sudden death: the arena COLLAPSES ---------------------------------------
# Past SUDDEN_DEATH_FRAC of the time cap, a ring of molten floor creeps in from
# the walls, shrinking the arena until someone dies. No mercy floor — the ring
# CAN kill (hover included; it's heat). Ends stalemates with drama instead of a
# spreadsheet tiebreak. Bots see it coming via view.arena.collapse.
SUDDEN_DEATH_FRAC = 0.55  # collapse starts at this fraction of TIME_CAP
COLLAPSE_STEP_TICKS = 24  # ring advances every N ticks...
COLLAPSE_STEP = 16.0      # ...by this many units
COLLAPSE_MAX_FRAC = 0.35  # ring stops at this fraction of the short axis (centre stays)
COLLAPSE_DPS = 6          # burn per tick inside the ring

# --- house robot: GATEKEEPER (classic Robot Wars menace) ---------------------
# A neutral, engine-owned bruiser that patrols the perimeter of big showcase maps
# and CHARGES anything that strays into its zone. It never fires — it rams. It
# belongs to team "house": it can't win, doesn't count for the end condition, and
# collects no pickups. It's terrain with a grudge. Deterministic brain
# (engine/house.py): patrol corners on a tick clock, charge the nearest intruder.
HOUSE_HP = 500                # tough enough to bully, killable with commitment
HOUSE_LOADOUT = {"hp": 6, "speed": 3, "damage": 0, "range": 0, "special": 2,
                 "agility": 4, "size": "large", "gun": "laser", "engine": "tank"}
HOUSE_ZONE = 300.0            # aggro radius: come this close, get charged
HOUSE_WAYPOINT_TICKS = 220    # patrol clock: ticks per corner waypoint
HOUSE_PATROL_MARGIN = 0.12    # corner waypoints inset (fraction of arena size)

# --- jams: guns are machines, machines break ---------------------------------
# Each trigger-pull rolls a jam. A jammed gun smokes and won't fire until it
# clears — rockets and mines still work, so a jam is a scramble, not a death.
JAM_CHANCE = {"laser": 0.012, "cannon": 0.045, "shotgun": 0.03}
JAM_TICKS = 35

# --- heat: fire discipline is a skill ----------------------------------------
# Every shot builds heat; heat bleeds off every tick. Hit the ceiling and the gun
# force-VENTS: disabled and steaming until the vent ends (rockets/mines still
# work). Unlike a jam (random mishap), heat is fully deterministic and visible —
# manage it (view.self.heat) or spam yourself into a shutdown mid-brawl.
# Tuned so SUSTAINED fire vents (~7 consecutive laser shots, ~5 shotgun, ~4
# cannon) while paced fire never does — heat fully decays across a ~35-tick gap.
HEAT_MAX = 100
HEAT_PER_SHOT = {"laser": 26, "cannon": 45, "shotgun": 33}
HEAT_COOL = 0.8           # heat shed per tick
VENT_TICKS = 40           # forced vent duration at HEAT_MAX (~2s of silence)

# --- gun archetypes (free pick, real trade-offs; multipliers over the stat curves) --
# All three are hitscan variants resolved in loadout.resolve_stats — game.py just
# reads the resolved numbers, so "laser" stays byte-identical to the locked engine.
#   laser   — the all-rounder. Exactly the classic numbers.
#   cannon  — huge single hits, but half the aim arc and double the reload. Miss = pain.
#   shotgun — short reach, WIDE arc, and it hits EVERY enemy in the cone. Brawler's pick.
GUNS = {
    "laser":   {"dmg": 1.0,  "range": 1.0,  "arc": 1.0,  "cd": 1.0,  "turn": 1.0,  "multi": False},
    "cannon":  {"dmg": 2.0,  "range": 1.15, "arc": 0.5,  "cd": 2.0,  "turn": 0.88, "multi": False},
    "shotgun": {"dmg": 0.55, "range": 0.45, "arc": 2.75, "cd": 1.25, "turn": 1.0,  "multi": True},
}
DEFAULT_GUN = "laser"

# --- engine archetypes (free pick, real trade-offs) -------------------------
#   standard — the classic drivetrain. Exactly the locked numbers.
#   sprint   — faster legs, thinner plating.
#   tank     — slower + tougher, and traps/terrain drag on it for HALF as long
#              (slow_ticks burn off at 2/tick).
#   hover    — skims the floor: pits, water and ice physics don't touch it
#              (lava still burns from heat), but the light frame costs HP.
ENGINES = {
    "standard": {"hp": 1.0,  "speed": 1.0,  "turn": 1.0,  "slow_resist": False, "hover": False},
    "sprint":   {"hp": 0.88, "speed": 1.18, "turn": 1.08, "slow_resist": False, "hover": False},
    "tank":     {"hp": 1.18, "speed": 0.85, "turn": 0.85, "slow_resist": True,  "hover": False},
    "hover":    {"hp": 0.85, "speed": 1.0,  "turn": 1.05, "slow_resist": False, "hover": True},
}
DEFAULT_ENGINE = "standard"

# --- chassis size (a strategic axis, NOT from the point budget) ------------
# Build the physical robot: small = hard to hit + nippy but fragile;
# large = tanky but a fat target for lasers, rockets and splash.
SIZES = {
    "small":  {"radius": 12.0, "hp_mult": 0.82, "speed_mult": 1.18},
    "medium": {"radius": 16.0, "hp_mult": 1.00, "speed_mult": 1.00},
    "large":  {"radius": 22.0, "hp_mult": 1.26, "speed_mult": 0.84},
}
DEFAULT_SIZE = "medium"

# --- arena / match ---------------------------------------------------------
ARENA_W = 1280
ARENA_H = 768
TIME_CAP = 1200           # ticks before HP-tiebreak ends the match (bigger HP pools need more clock to finish by KO; a kiting stalemate still resolves on HP rather than dragging forever)
ROBOT_RADIUS = 16.0

# --- movement / combat -----------------------------------------------------
BACK_SPEED_FACTOR = 0.7   # reverse speed multiplier (enables kiting)
DASH_FACTOR = 2.5         # special dash speed multiplier
DASH_COOLDOWN_BASE = 60   # dash cooldown ticks = max(20, BASE - level*8)
DAMAGE_FALLOFF = 0.45     # long-range shots do (1 - this) of full damage at max range

# --- rockets (travel-time projectile, splash, limited ammo) ----------------
# Universal tool: every bot gets the same rockets, so strategy is in HOW you
# use them, not how you spec them. They travel along your heading -> aiming
# matters, walls stop them, and enemies can SEE and dodge them.
ROCKET_AMMO = 3           # rockets per robot per match
ROCKET_SPEED = 17.0       # units/tick (fast, but dodgeable)
ROCKET_RANGE = 540.0      # max travel before self-detonate
ROCKET_RADIUS = 7.0       # projectile collision size
ROCKET_COOLDOWN = 20      # ticks between launches
ROCKET_BLAST_RADIUS = 72.0
ROCKET_BLAST_DAMAGE = 15  # at the centre; falls linearly to 0 at the edge (a rocket volley hurts and can finish, but no longer instant-deletes)

# --- mines / traps (deployable, proximity-triggered, zone control) ---------
TRAP_COUNT = 5            # mines per robot per match
TRAP_ARM_TICKS = 12       # ticks before a freshly dropped mine goes live
TRAP_TRIGGER_RADIUS = 30.0  # an ENEMY this close to a live mine sets it off
TRAP_BLAST_RADIUS = 84.0
TRAP_BLAST_DAMAGE = 8     # at the centre; falls linearly to 0 at the edge (a light hurt+slow nuisance, not a match-decider)
TRAP_DROP_COOLDOWN = 10
# Mines are traps too: a blast HURTS + SLOWS but never scores the kill. Damage is
# floored at HAZARD_MIN_HP and the victim is slowed, so a minefield softens and
# pins enemies for your robots to finish — the trap itself can't execute.
TRAP_SLOW_TICKS = 30

# Splash hits EVERY robot in radius, including the owner — point-blank rockets
# and standing on your own mine will hurt you. (Deliberate: teaches "test your
# clever exploit before the show".)

# --- terrain hazards (typed floor zones, see engine/maps.py) ---------------
# Each hazard is {"type": "lava"|"ice"|"pit"|"water", "x","y","w","h"} (top-left rect).
# Effects fire after movement each tick; a robot's CENTRE decides what it stands on.
LAVA_DPS = 5              # damage per tick while standing in lava
# Water = a wading zone: it SLOWS you (drag) and lightly rusts your chassis, but
# never kills (floored at HAZARD_MIN_HP, like the other traps). Big rivers/moats
# you must cross or route around — terrain that shapes the fight, not an executioner.
WATER_DPS = 2            # damage per tick while a robot's centre is in water (light rust; non-lethal)
WATER_SLOW_TICKS = 18    # ticks of movement-slow applied each water tick (wading drag)
# Pits no longer instant-kill. A robot whose centre is in a pit takes PIT_DAMAGE
# per tick and is SLOWED for a few ticks — it can still crawl out. Like mines, a
# pit is a TRAP, not an executioner: it never drops a robot below HAZARD_MIN_HP,
# so only a real robot can score the elimination.
PIT_DAMAGE = 10          # damage per tick while a robot's centre is in a pit (a nuisance, not a near-kill)
PIT_SLOW_TICKS = 30      # ticks of movement-slow applied each pit tick
HAZARD_SLOW_MULT = 0.45  # movement-speed multiplier while slowed (pit or mine)
HAZARD_MIN_HP = 1        # a trap (pit/mine) never drops a robot below this HP

# --- floor flipper: the classic arena surprise --------------------------------
# A steel paddle set into the floor. Roll over an ARMED one and it fires: hurls
# the robot across the arena, guarantees a flip, chips some HP, then re-arms
# after a cooldown. Launch direction = away from the paddle centre, jittered by
# the robot's own seeded RNG (replays stay byte-exact).
FLIPPER_THROW = 170        # launch distance, game units
FLIPPER_DAMAGE = 6         # landing chip damage (floored at HAZARD_MIN_HP)
FLIPPER_COOLDOWN = 140     # ticks before the paddle re-arms

# --- turntable: the spinning floor platter ------------------------------------
# A rotating disc set into the floor. Anything GROUNDED on it is carried around
# the centre and spun with it — position and heading both rotate, wrecking your
# aim and your escape line. Hover engines skim it (no grip on the platter).
# Fully deterministic: constant angular velocity, no RNG.
TURNTABLE_DEG_PER_TICK = 8.0   # full rotation every 45 ticks — a real whirl
TURNTABLE_FLING = 2.4          # outward drift per tick: the platter slings you off
# Ice = slippery: you keep momentum from last tick and steer worse. Only applies
# while on ice, so hazard-free maps move exactly as before (byte-identical).
ICE_CONTROL = 0.5         # fraction of the move you actually command on ice
ICE_SLIP = 0.80           # fraction of last tick's displacement that carries over
ICE_TURN_MULT = 0.5       # turn-rate multiplier on ice (harder to steer)

# --- pickups (deployable crates: refill ammo, repair, or POWER UP, then respawn) --
# Each pickup is {"x","y","kind": "rockets"|"traps"|"repair"|"overdrive"|"shield"|"haste"}.
# First robot to touch it collects it; it goes dormant then respawns, so the map
# stays contested.
PICKUP_RADIUS = 24.0
PICKUP_RESPAWN = 220      # ticks dormant before a collected pickup returns
PICKUP_ROCKETS = 2        # rockets granted by a "rockets" crate
PICKUP_TRAPS = 2          # mines granted by a "traps" crate
PICKUP_REPAIR = 28        # HP restored by a "repair" crate (capped at max_hp)

# --- powerups (mid-match buffs from crates; timed, visible, fight-swinging) --
# overdrive = your hits land harder · shield = incoming combat damage halved ·
# haste = faster legs. Terrain (lava/water/pits) ignores shields — the floor
# doesn't care about your force field.
POWERUP_TICKS = 120        # duration of any buff (~6.5s at broadcast speed)
OVERDRIVE_MULT = 1.5       # outgoing laser/cannon/shotgun + blast damage
SHIELD_MULT = 0.5          # incoming combat damage multiplier
HASTE_MULT = 1.35          # move-speed multiplier

# --- weather (global, rolled once per match from the seed) -----------------
# "clear" (default, no effect, backward-compatible), "fog" (shorter weapon
# reach + sight), "wind" (rockets drift sideways in flight).
# Fog is cut from the roll pool: the haze reads badly on the big screen. The
# mechanic stays forceable (weather="fog") for tests and old recordings.
WEATHER_POOL = ["clear", "wind"]
WEATHER_FOG_RANGE = 0.62  # weapon/rocket range multiplier under fog
WEATHER_WIND_DRIFT = 1.5  # px/tick sideways rocket drift under wind

# --- walls / cover (axis-aligned rectangles: x, y, w, h) -------------------
# Static obstacles: block movement, stop rockets, and break laser line-of-sight.
# Cover is what makes rockets + traps tactical instead of a damage race.
WALLS = [
    (ARENA_W * 0.5 - 100, ARENA_H * 0.5 - 18, 200, 36),   # centre bar
    (ARENA_W * 0.26 - 18, ARENA_H * 0.28 - 70, 36, 140),  # left pillar
    (ARENA_W * 0.74 - 18, ARENA_H * 0.72 - 70, 36, 140),  # right pillar
]
