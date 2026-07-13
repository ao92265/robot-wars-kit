# Robot Wars — 3D robot models + WebGL arena viewer

Blender models for **every visual permutation** of a Robot Wars loadout, plus a
standalone browser arena to view them.

The game (robot-wars/engine/config.py) defines three visual axes:

| axis   | options                          |
|--------|----------------------------------|
| size   | small, medium, large             |
| gun    | laser, cannon, shotgun           |
| engine | standard, sprint, tank, hover    |

= **36 permutations**. Stat points (hp/speed/damage/range/special/agility) don't
change the physical robot, so 36 models covers every distinct build.

## What's here

- `build_robots.py` — Blender script that generates everything procedurally
- `glb/robot_<size>_<gun>_<engine>.glb` — 36 exported models (Y-up, three.js-ready)
- `robots_all.blend` — all 36 arranged in a grid (rows = engine, columns = size x gun)
- `preview.png` — rendered contact sheet
- `../arena-viewer/dist/robot-arena.html` — **the arena: just double-click it** (self-contained, ~6 MB)
- `../tools/blender-4.5.9-windows-x64/` — portable Blender used to build (no install needed)

## Model anatomy

Each GLB has three nodes so the viewer can animate them:

- `robot_*` — hull: chassis (scaled by size) + drivetrain (wheels / racing wheels
  + spoiler / tank treads + armor / hover skirt + glowing thrusters)
- `robot_*_gun` — turret hardware: slim glowing laser, fat cannon with muzzle
  brake, or triple-barrel shotgun
- `robot_*_flash` — emissive muzzle-flash burst, scale 0 at rest

Each file carries one baked animation clip, **`shoot`**: gun recoil (kick back
fast, return slow) + muzzle flash pop, 9 frames @ 24 fps.

Chassis colour is per-engine (grey/blue/green/purple); the material is named
`body_*` so it can be re-tinted at runtime — same idea as a match robot's
`color` field (`#rrggbb`).

## Rebuilding the models

```powershell
& "..\tools\blender-4.5.9-windows-x64\blender.exe" --background --python build_robots.py -- --out .

# bake every chassis in a custom colour instead of the engine palette:
& "..\tools\blender-4.5.9-windows-x64\blender.exe" --background --python build_robots.py -- --out . --body-color "#ff6b6b"
```

## The arena viewer (arena-viewer/)

`src/main.js` (three.js app) + `src/template.html` are bundled by `build.mjs`
(esbuild) together with all 36 GLBs base64-inlined into **one HTML file** —
no server, no network, works from `file://`.

```powershell
cd ..\arena-viewer
npm install        # first time only
node build.mjs     # -> dist/robot-arena.html
```

Features:
- all 36 robots in a config.py-styled arena (real WALLS layout, glowing perimeter)
- orbit / zoom, click a robot → camera glide + spec sheet (stats resolved with
  the exact `resolve_stats` formulas at 0 loadout points)
- filter by size / gun / engine
- **labels on/off** checkbox
- **colour picker + game palette swatches** — tints the selected robot (or all
  visible robots when nothing is selected); `reset colours` restores defaults
- **FIRE** — button on the spec sheet, or spacebar (fires the selected robot,
  or a full 36-robot volley when nothing is selected); plays the baked `shoot` clip
- hover engines bob over the floor; selected robot slowly turntables
