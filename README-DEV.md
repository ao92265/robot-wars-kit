# Robot Wars — Developer Kit

The full studio for the Robot Wars workshop: game engine, arena/robot models,
and the viewers. If you only want to **write a bot**, grab the player kit
instead — it's 2.4 MB and needs nothing but Python.

## Layout

```
.  (repo root)           the game
  engine/                sim engine (pure stdlib): game.py, config.py, maps.py,
                         dummies/ (stock bots), house.py (the Gatekeeper)
  arena.py               CLI: run/record matches, --exhibition, --best-of
  my_bot.py              the participant file (loadout + decide())
  tournament/            bracket runner, submit server, voice pack
  tournament/visual/     the 3D match viewer (three.js, single-file build)
    src/arena.app.js     renderer source
    build_arena.py       bundles arena.html (self-contained, offline)
  make_kit.py            packages the player + dev zips into dist/

robot-models/            Blender asset pipeline (scripts are the source of truth)
  build_robots.py        36 robot GLBs (size x gun x engine) + shoot animations
  build_arenas.py        one arena GLB per map preset, built FROM engine/maps.py
  build_spinners.py      the 4 melee spinner attachments
  glb*/                  built GLB outputs (committed so you rarely need Blender)

arena-viewer/            standalone 36-robot showcase (drive-around demo)
```

## Prerequisites

- **Python 3.9+** — engine, build scripts, packaging (no pip installs)
- **Blender 4.5+ / 5.x** — only when changing models (`brew install --cask blender`)
- **Node 18+** — only for the arena-viewer showcase bundle

## Common commands

Run / record matches:

```bash
cd .  # repo root
python3 arena.py --map colosseum                      # you vs the 5 stock bots
python3 arena.py --exhibition chaser,sniper --fast    # stock bot 1v1
python3 arena.py --record match.jsonl --fast          # capture a replay
```

Matches roll a fresh seed each run; every run prints `--seed N` to reproduce it.

Rebuild the 3D viewer after engine/model changes (embeds models + the demo match):

```bash
python3 tournament/visual/build_arena.py
open tournament/visual/arena.html
```

Rebuild models after editing the Blender scripts:

```bash
cd robot-models
blender --background --python build_robots.py   -- --out .
blender --background --python build_arenas.py   -- --out .
blender --background --python build_spinners.py -- --out .
```

Rebuild the showcase viewer:

```bash
cd arena-viewer && npm install && node build.mjs && open dist/robot-arena.html
```

Package the kits:

```bash
python3 make_kit.py     # dist/robot-wars-player-kit.zip + -dev-kit.zip
```

## Conventions worth knowing

- **Scale**: 1 m (Blender) = 10 game units. Game coords are top-left origin,
  y-down; world maps x -> x, y -> z. Robots face -Z in glTF; the replay app
  wraps them so front = +X.
- **Arena/sim alignment**: `build_arenas.py` imports `engine/maps.py`, so wall
  and hazard geometry always matches the sim rects. The viewer picks the arena
  GLB by fingerprinting the recorded wall layout — custom maps fall back to
  procedural rendering.
- **Hazard types**: lava, water, ice, pit, flipper (launch paddle), turntable
  (spinning platter). Tunables live in `engine/config.py`; per-map placement in
  `engine/maps.py`. New hazard = engine behaviour + `HAZARD_STYLE`/`RIM_STYLE`
  in `build_arenas.py` + a visual branch in `arena.app.js`.
- **Determinism**: the engine is fully seeded — same seed + same bots = byte-
  identical match. Keep it that way (draw randomness from the per-robot RNGs).
- **Legacy three.js**: the replay viewer uses the vendored global three r150
  build. Two gotchas we hit: materials must be *created with* their texture
  (maps attached later never upload), and materials must export single-sided
  (double-sided lighting shades box interiors through front faces).
