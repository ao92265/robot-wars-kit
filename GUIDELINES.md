# Robot Wars — your cheat sheet

> **Deep reference: [CUSTOMIZE.md](CUSTOMIZE.md)** — every stat curve, the gun/engine/size
> trade-off tables, misses/jams/flips/powerups, and the full `view`/action contract.
> Copyable example builds live in **`examples/`**. This page is the 2-minute version.

## The 3 commands
```
python3 arena.py            # watch your robot fight the dummies
python3 arena.py --check    # is my loadout legal?
python3 arena.py --submit "Team Name"   # hand it in
```
Windows: use `python` instead of `python3` everywhere.
Iterate: `python3 arena.py --vs sniper --best-of 20` (win-rate vs one dummy).
Practice dummies: `duck`, `chaser`, `sniper`, `bomber` (rockets), `trapper` (mines).

## The deal
Edit **only `my_bot.py`**: `LOADOUT` (the machine), `APPEARANCE` (the paint),
`decide(view)` (the brain). **12 points**, max 6 per stat, across six stats —
`hp, speed, damage, range, special` (dash), `agility` (turn rate) — **plus three
free archetype picks** with real trade-offs:

- `size`: `"small"` | `"medium"` | `"large"`
- `gun`: `"laser"` | `"cannon"` (huge hits, tiny arc, slow reload) | `"shotgun"` (short wide cone, hits everyone in it)
- `engine`: `"standard"` | `"sprint"` | `"tank"` (barely flips) | `"hover"` (skims pits/water/ice; lava still burns)

> The real game is finding the dirtiest **legal** strategy. The stat sheet doesn't win
> fights — in our testing the "biggest" build loses 7/10 to a mid-size bot with smarter
> zone control. Probe the edges. You can't crash the match — go on, try.

## Your arsenal (everyone carries these)
- **Main gun** — your archetype; fire with `{"fire": "laser"}` whatever you mounted.
- **Rocket ×3** — travels, splash, dodgeable, wall-stopped, can FLIP the victim.
- **Mine ×5** — arms after a moment, enemy-triggered, hurts + slows + can FLIP (never kills).
- **Dash** — speed burst (needs `special` ≥ 1).

## Things GO WRONG (that's the show)
- Shots **miss** more when the target's fast/far or you're snap-turning.
- Guns **jam** (~2s, cannon worst) — rockets/mines still work.
- Guns build **heat** (cannon worst) — hit 100 and you force-VENT ~2s. Watch
  `view.self.heat`; punish enemies who spray (`♨` = free hits).
- Blasts can **flip** you wheels-up: helpless till you self-right. `view.enemies[0].flipped`
  is your kill window.
- **Ramming hurts** — slam an enemy at speed and you both crunch, split by weight
  (heavy bulldozes, light bounces). Hard rams can flip. A dash-charge is a weapon.
- **SUDDEN DEATH** — past ~55% of the clock a molten ring closes in from the walls and
  it CAN kill (hover too). `view.arena.collapse` = ring width. Hold the centre.
- **Powerup crates** on the big maps: ⚡ overdrive (harder hits) · 🛡 shield (half damage) ·
  💨 haste (faster) — ~6s each, first robot there takes it.
- Terrain: lava burns · water/pits drag · ice slides (hover skips all but lava).
  Big maps also hide a **floor flipper** (drive over an armed one: hurled across the
  arena, wheels-up, minus some HP — it launches hover bots too) and a **turntable**
  (spinning disc that carries you round and wrecks your aim; hover skims it).
  Both appear in `view.arena.hazards` by type — dodging or baiting them is legal.
  Weather per match: fog (short reach) or wind (rockets drift).

## What your bot sees — `view` (full list in CUSTOMIZE.md §5)
`view.self` (your stats + `flipped/jammed/overdrive/shield/haste`), `view.enemies`
(nearest first, each with `dist, bearing, hp, flipped`), `view.allies`,
`view.incoming_rockets`, `view.mines`, `view.arena` (walls!), `view.tick`, `view.rng`.

## What you can do — return a dict (any subset)
```python
return {"thrust": "forward",   # or "back"
        "turn":   45,          # degrees this tick (clamped); negative = left
        "fire":   "laser",     # or "rocket"
        "drop_trap": True,     # mine where you stand
        "special": True}       # dash
```
Leave a key out = you don't do it. A crash or slow `decide` = you idle that tick.

## Rules
- One file: `my_bot.py`. Don't touch `engine/`.
- `decide(view)` must return fast. No files, network, or `os`/`socket` imports.
- Budget + picks enforced on submit — `--check` tells you if you're legal.

## How to win
Beat the dummies consistently and you'll make the bracket. Use `PROMPT.md` to have
Claude Code build and tune your strategy from a one-sentence idea — including the
cheesy ones. Then test the counter: whatever beat you, build its counter next.
