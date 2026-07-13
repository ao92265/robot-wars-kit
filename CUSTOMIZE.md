# CUSTOMIZE.md — every knob on your robot

You edit **one file**: `my_bot.py`. It has three things — `LOADOUT` (the machine),
`APPEARANCE` (the paint), `decide(view)` (the brain). This page is the complete
reference for all three. Working examples live in `examples/`.

> **The point of all this:** there is no "best build" — every pick has a counter, and
> chaos (misses, jams, flips) punishes one-trick plans. The teams that win think
> outside the box, test against the counter, and iterate. That's also exactly how you
> work with Claude Code: idea → build → verify → refine.

```
python3 arena.py            # watch it fight
python3 arena.py --check    # is my loadout legal?
python3 arena.py --submit "Team Name"
```
Windows: use `python` instead of `python3` everywhere.

---

## 1 · LOADOUT — build the machine

```python
LOADOUT = {"hp": 2, "speed": 6, "damage": 3, "range": 0, "special": 1,
           "size": "small", "gun": "shotgun", "engine": "sprint"}
```

### Point budget — 12 points, max 6 per stat

| Stat | 0 pts | each point | buys you |
|------|-------|-----------|----------|
| `hp`      | 200 HP | +15 HP | staying power (200..290) |
| `speed`   | 7/tick | +3.2 | straight-line pace (7..26) |
| `damage`  | 8/hit  | +5 | gun punch (8..38, scaled by your gun) |
| `range`   | 110    | +60 | gun reach (110..470, scaled by your gun) |
| `special` | —      | enables **dash** | speed burst; more points = shorter cooldown |
| `agility` | 34°/tick | +3° | turn rate (34..52) — out-turn them, dodge better |

### Chassis size (free pick, real trade-offs)

| `size` | hitbox | HP | speed | flip risk |
|--------|--------|----|----|-----------|
| `"small"`  | tiny target | ×0.82 | ×1.18 | tossed easily (×1.3) |
| `"medium"` | normal | ×1.0 | ×1.0 | ×1.0 |
| `"large"`  | big splash magnet | ×1.26 | ×0.84 | planted (×0.7) |

### Gun archetype (free pick) — HOW you kill

| `gun` | damage | reach | aim arc | reload | jam risk | identity |
|-------|--------|-------|---------|--------|----------|----------|
| `"laser"`   | ×1.0 | ×1.0 | 16° | ×1.0 | 1.2% | the reliable all-rounder |
| `"cannon"`  | ×2.0 | ×1.15 | **8°** | **×2.0** | 4.5% | huge single hits; misses hurt YOU |
| `"shotgun"` | ×0.55 | ×0.45 | **44°** | ×1.25 | 3% | short cone that hits **EVERY enemy in it** |

All guns fire with `{"fire": "laser"}` — "laser" means *pull the trigger*, whatever you mounted.

### Engine archetype (free pick) — HOW you move

| `engine` | HP | speed | turn | perk / cost |
|----------|----|----|------|-------------|
| `"standard"` | ×1.0 | ×1.0 | ×1.0 | no surprises |
| `"sprint"`   | ×0.88 | ×1.18 | ×1.08 | fast + twitchy, thin plating |
| `"tank"`     | ×1.18 | ×0.85 | ×0.85 | slows wear off 2× fast, **barely flips**, rights 2× fast |
| `"hover"`    | ×0.85 | ×1.0 | ×1.05 | **pits/water/ice don't exist for you** (lava still burns); tossed easily |

---

## 2 · Physics of a fight — what can go wrong (and right)

- **Shots can MISS.** Hit chance = gun base − long-shot risk − target motion − snap-shot
  penalty. Point-blank at a stationary target ≈ never misses; max-range at a sprinting
  small bot while you're spinning ≈ a coin flip (never below 20%). Fast + agile bots
  survive by being *unhittable*, not just by dodging rockets.
- **Guns JAM.** Every trigger-pull risks a jam (see table). A jammed gun smokes and is
  dead for ~2s — but rockets and mines still work. Cannons jam most: pack a plan B.
- **Guns build HEAT.** Every shot adds heat (laser 22 · shotgun 30 · cannon 45 per shot,
  ceiling 100, sheds 1.2/tick); hit the ceiling and the gun force-VENTS for ~2s, steaming
  and useless. Unlike a jam this is fully deterministic and visible — `view.self.heat` is
  your gauge, `view.self.overheated` the alarm. Fire discipline is a skill: an enemy
  venting at 100 heat is a free attack window.
- **Blasts can FLIP you.** Rockets and mines can toss a bot wheels-up: no moving, no
  shooting, no thinking until you self-right (~2.5s; tanks ~1.2s). Small and hover
  chassis get tossed easiest. A flipped enemy is free damage — `view.enemies[i].flipped`
  tells you the moment it happens. **GO.**
- **RAMMING is real.** Slam an enemy above ~10 units/tick closing speed and you both
  CRUNCH — damage scales with speed and splits by mass (radius²): the heavy one
  bulldozes, the light one bounces. Capped per hit, cooldown between crunches, and a
  hard ram can FLIP the victim. A dash-charge is a weapon; a large tank-engine
  battering ram is a build.
- **SUDDEN DEATH.** Past ~55% of the clock, a molten ring creeps in from every wall and
  keeps shrinking the arena. It burns like lava — **no mercy floor, hover included** —
  so stalemates end in fire, not spreadsheets. `view.arena.collapse` is the ring's
  current width: stay inside `[collapse, width-collapse] × [collapse, height-collapse]`
  or force your enemy out of it.
- **Splash hurts YOU too.** Point-blank rockets and your own mines bite their owner.
- **Terrain**: lava burns · water drags + rusts · pits chip + drag · ice slides.
  Hover skips pits/water/ice (lava still burns — it's heat, not floor). Pits, water
  and mines never score the kill (they floor you at 1 HP; only a robot can finish
  you) — but **lava has no such mercy**: stand in it long enough and it WILL kill you.
- **Weather** (rolled per match): fog shortens reach · wind bends rockets.
- **Pickups** respawn: rocket crates, mine crates, repair kits. Control them.
- **POWERUPS** (big maps, ~6s each, first robot there takes it — then the crate
  respawns, so the spot stays worth fighting over):

| crate | effect while active |
|-------|---------------------|
| ⚡ `overdrive` | your gun + blast damage ×1.5 |
| 🛡 `shield` | incoming combat damage ×0.5 (terrain ignores it) |
| 💨 `haste` | move speed ×1.35 |

  Your bot sees its own timers: `view.self.overdrive / .shield / .haste` (ticks left,
  0 = off). On the colosseum the ⚡ sits dead-centre in the brawl pit — walking in
  there is a choice.

## 3 · Arsenal (everyone carries these)

- **Main gun** — your archetype above. `{"fire": "laser"}`.
- **Rockets ×3** — travel along your heading, splash on impact, dodgeable, wall-stopped,
  can FLIP. `{"fire": "rocket"}`.
- **Mines ×5** — arm after a moment, enemy-triggered, hurt + slow + can FLIP (never kill).
  `{"drop_trap": True}`.
- **Dash** — speed burst if you bought `special`. `{"special": True}`.

---

## 4 · APPEARANCE — the paint job (never affects the fight)

```python
APPEARANCE = {"color": "#ff5d3a", "shape": "speeder", "accent": "#ffd93d"}
```

| key | what it is |
|-----|------------|
| `color`  | your hull + beam colour (`#RRGGBB`) |
| `accent` | racing stripe + roundel colour (`#RRGGBB`) |
| `shape`  | your spinning weapon's look: `"tank"` (drum) · `"speeder"` (bar spinner) · `"orb"` (buzzsaw) · `"spike"` (spiked drum) |

Your gun and engine change the 3D model too — cannon tube, shotgun cluster, treads,
hover glow — so a build is recognisable from the back row.

---

## 5 · decide(view) — the brain

Called every tick. Return a dict (any subset):

```python
return {"thrust": "forward",   # or "back" (slower)
        "turn": 45,            # degrees, clamped to your turn rate; negative = left
        "fire": "laser",       # or "rocket"
        "drop_trap": True,
        "special": True}       # dash
```

What you can see:

- `view.self` — `x, y, heading, hp, max_hp, radius, size, gun, engine, speed, turn_rate,
  weapon_range, weapon_arc, cooldown, rockets_left, traps_left, rocket_ready, trap_ready,
  special_ready, slowed, flipped, jammed, heat, overheated, overdrive, shield, haste, team`
- `view.enemies` — nearest first: `x, y, hp, dist, bearing, flipped`
- `view.allies` — same, your side
- `view.incoming_rockets` — dodge these: `x, y, vx, vy, dist, bearing`
- `view.mines` — `x, y, mine` (True = yours), `armed, dist, bearing`
- `view.arena` — `width, height, walls` (rects that block movement, lasers, rockets),
  `collapse` (SUDDEN DEATH ring width; 0 = not started)
- `view.tick`, `view.rng` (seeded — never `import random`)

Rules: edit only `my_bot.py` · return fast · no files/network/os imports.
A crash or slow tick = you idle that tick. You can't break the match — probe the edges.

---

## 6 · Example builds (copy + tweak, in `examples/`)

| file | build | one-liner |
|------|-------|-----------|
| `brawler_shotgun.py`   | shotgun · sprint · small | "Riot" — dash in, delete everything in the cone |
| `juggernaut_cannon.py` | cannon · tank · large | "Bastion" — walking siege gun, wades through traps |
| `skirmisher_hover.py`  | laser · hover · small | "Wraith" — kites ACROSS hazards others must route around |
| `artillery_sprint.py`  | cannon · sprint · medium | "Longshot" — fire, dash to a new angle, fire again |

The counter-triangle: brawlers catch snipers · snipers shred juggernauts at range ·
juggernauts win every trade a brawler forces. Build for the bracket, not one fight.
