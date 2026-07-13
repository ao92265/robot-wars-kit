"""Generate Blender models for every Robot Wars arena map preset.

Reads the map definitions straight from the game engine (engine/maps.py in the
robot-wars repo) so the modelled walls and hazard basins line up EXACTLY with
the sim's collision rects — same numbers, no eyeballing.

Run headless:
  blender --background --python build_arenas.py -- --out <dir> [--game <robot-wars dir>]

Outputs:
  <out>/glb-arenas/arena_<map>.glb   (one per preset, Y-up, ready for three.js)
  <out>/glb-arenas/arena_<map>.png   (preview render)

Conventions (matching build_robots.py + the three.js viewers):
  1 m = 10 game units. Game (x, y) top-left origin -> Blender (x - w/2, -(y - h/2)),
  so after Y-up glTF export, world z = y - h/2 — the same mapping the arena
  renderer uses (gx/gz). Floor top surface sits at z = 0.

The GLB carries the STATIC set: floor + markings, kerbs, cover walls, hazard
basins/rims, pickup pads, corner pylons. Animated surfaces (molten lava flow,
water caustics, sudden-death collapse) stay app-side as shader planes layered
~0.1 m above the basins.
"""

import importlib.util
import math
import os
import sys

import bpy
from mathutils import Matrix

S = 0.1  # game units -> metres

# palette mirrors tournament/visual/src/arena.app.js
FLOOR = (0.055, 0.065, 0.085, 1.0)
KERB = (0.075, 0.095, 0.14, 1.0)
WALL = (0.16, 0.20, 0.27, 1.0)
TRIM_CYAN = (0.25, 0.82, 0.79, 1.0)
BEACON = (1.0, 0.66, 0.13, 1.0)
HAZARD_STYLE = {
    #        basin colour              rough  metal  emission (colour, strength)
    "lava":  ((0.20, 0.04, 0.01, 1.0), 0.6, 0.0, ((1.0, 0.35, 0.08, 1.0), 2.5)),
    "water": ((0.04, 0.32, 0.55, 1.0), 0.12, 0.0, None),
    "ice":   ((0.81, 0.93, 1.00, 1.0), 0.07, 0.0, None),
    "pit":   ((0.004, 0.01, 0.02, 1.0), 1.0, 0.0, None),
    "flipper": ((0.45, 0.48, 0.53, 1.0), 0.35, 0.9, None),   # steel launch plate
    "turntable": ((0.28, 0.30, 0.34, 1.0), 0.4, 0.8, None),  # platter housing
}
RIM_STYLE = {
    "lava":  ((1.0, 0.65, 0.20, 1.0), 0.8),
    "water": ((0.30, 0.75, 1.00, 1.0), 0.6),
    "ice":   ((0.80, 1.00, 1.00, 1.0), 0.5),
    "pit":   ((1.00, 0.20, 0.25, 1.0), 0.8),
    "flipper": ((1.00, 0.85, 0.24, 1.0), 0.6),               # warning-yellow rim
    "turntable": ((0.25, 0.82, 0.79, 1.0), 0.5),             # cyan ring
}
PICKUP_COL = {
    "rockets":   (1.00, 0.62, 0.26, 1.0),
    "traps":     (0.73, 0.51, 1.00, 1.0),
    "repair":    (0.42, 0.80, 0.47, 1.0),
    "overdrive": (1.00, 0.85, 0.24, 1.0),
    "shield":    (0.30, 0.59, 1.00, 1.0),
    "haste":     (1.00, 0.36, 0.56, 1.0),
}

_materials = {}


def get_mat(name, color, metallic=0.3, rough=0.6, emission=None, strength=0.0):
    if name in _materials:
        return _materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    # export single-sided (doubleSided:false) — three r150's double-sided lit
    # path shades closed-box interiors through the front faces
    mat.use_backface_culling = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = emission
        bsdf.inputs["Emission Strength"].default_value = strength
    _materials[name] = mat
    return mat


def box(size_xyz, loc, mat):
    # size=2 -> unit half-extents, so scale=size/2 yields true world dimensions
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    obj.scale = (size_xyz[0] / 2, size_xyz[1] / 2, size_xyz[2] / 2)
    return obj


def cyl(radius, depth, loc, mat, verts=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth, location=loc)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def join(parts, name):
    if not parts:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


BANNER_COLS = [(0.25, 0.82, 0.79, 1.0), (1.0, 0.42, 0.42, 1.0), (1.0, 0.85, 0.24, 1.0),
               (0.42, 0.80, 0.47, 1.0), (0.73, 0.51, 1.00, 1.0), (1.0, 0.62, 0.26, 1.0),
               (0.30, 0.59, 1.00, 1.0), (1.0, 0.36, 0.56, 1.0)]


def build_colosseum(W, H, KT):
    """Stadium bowl around the pit: tiered stands with seat rows, a colonnade
    with a lintel ring and hanging team banners, gate tunnels at the four
    mid-sides, and corner floodlight towers. All decorative — the play area
    (floor/kerbs/walls/hazards) is untouched."""
    stone = get_mat("stone", (0.085, 0.095, 0.125, 1.0), metallic=0.2, rough=0.85)
    seat_mat = get_mat("seat", (0.045, 0.055, 0.075, 1.0), metallic=0.1, rough=0.9)
    steel_mat = get_mat("steel", (0.55, 0.57, 0.60, 1.0), metallic=0.9, rough=0.3)
    col_mat = get_mat("column", (0.13, 0.145, 0.19, 1.0), metallic=0.35, rough=0.6)
    gate_mat = get_mat("gate", (0.008, 0.012, 0.02, 1.0), metallic=0.1, rough=0.95)
    beacon_mat = get_mat("beacon", BEACON, metallic=0.0, rough=0.4,
                         emission=BEACON, strength=1.8)
    flood_mat = get_mat("floodlight", (0.95, 0.97, 1.0, 1.0), metallic=0.0, rough=0.3,
                        emission=(0.95, 0.97, 1.0, 1.0), strength=2.2)

    TIERS, DEPTH, RISE = 5, 5.0, 2.6
    base = KT + 1.6            # stands begin just outside the kerbs
    gate_w = 9.0               # tunnel opening carved through the lower tiers

    # tiered stands: rectangular rings of stepped bands, split around the
    # mid-side gates on the lower three tiers
    stands, seats = [], []
    for t in range(TIERS):
        off = base + t * DEPTH
        z0, z1 = t * RISE, t * RISE + RISE + 0.5
        lenx = W + 2 * off + 2 * DEPTH
        leny = H + 2 * off
        h = z1 - z0
        gap = gate_w if t < 3 else 0.0   # upper tiers run continuous
        for sy in (-1, 1):               # north/south bands (split at centre gate)
            y = sy * (H / 2 + off + DEPTH / 2)
            if gap:
                seg = (lenx - gap) / 2
                for sx in (-1, 1):
                    stands.append(box((seg, DEPTH, h), (sx * (gap / 2 + seg / 2), y, z0 + h / 2), stone))
            else:
                stands.append(box((lenx, DEPTH, h), (0, y, z0 + h / 2), stone))
        for sx in (-1, 1):               # east/west bands
            x = sx * (W / 2 + off + DEPTH / 2)
            if gap:
                seg = (leny - gap) / 2
                for sy in (-1, 1):
                    stands.append(box((DEPTH, seg, h), (x, sy * (gap / 2 + seg / 2), z0 + h / 2), stone))
            else:
                stands.append(box((DEPTH, leny, h), (x, 0, z0 + h / 2), stone))
        # two seat rows grooved into each tier top
        for k in (0.28, 0.68):
            zr = z1 + 0.09
            ry = H / 2 + off + DEPTH * k
            rx = W / 2 + off + DEPTH * k
            seats.append(box((lenx - 2 * DEPTH * (1 - k), 0.45, 0.18), (0, -ry, zr), seat_mat))
            seats.append(box((lenx - 2 * DEPTH * (1 - k), 0.45, 0.18), (0, ry, zr), seat_mat))
            seats.append(box((0.45, leny - 2 * DEPTH * (1 - k), 0.18), (-rx, 0, zr), seat_mat))
            seats.append(box((0.45, leny - 2 * DEPTH * (1 - k), 0.18), (rx, 0, zr), seat_mat))
    join(stands, "Stands")
    join(seats, "Seats")

    # gate tunnels: dark mouths recessed into the lower stands + steel lintels
    gates = []
    z_gate = 3 * RISE
    for sy in (-1, 1):
        y = sy * (H / 2 + base + 1.5 * DEPTH)
        gates.append(box((gate_w, 3 * DEPTH, z_gate), (0, y, z_gate / 2 - 0.2), gate_mat))
        gates.append(box((gate_w + 1.6, DEPTH * 0.6, 0.9), (0, sy * (H / 2 + base + 0.3 * DEPTH), z_gate + 0.45), steel_mat))
    for sx in (-1, 1):
        x = sx * (W / 2 + base + 1.5 * DEPTH)
        gates.append(box((3 * DEPTH, gate_w, z_gate), (x, 0, z_gate / 2 - 0.2), gate_mat))
        gates.append(box((DEPTH * 0.6, gate_w + 1.6, 0.9), (sx * (W / 2 + base + 0.3 * DEPTH), 0, z_gate + 0.45), steel_mat))
    join(gates, "Gates")

    # colonnade on the top rim: columns + a continuous lintel ring
    rim = base + TIERS * DEPTH
    z_top = TIERS * RISE + 0.5
    col_h, lintel_h = 6.5, 1.1
    cols, lintels, banners = [], [], []
    ox, oy = W / 2 + rim + 1.2, H / 2 + rim + 1.2
    def col_run(axis, fixed, lo, hi):
        n = max(2, int((hi - lo) / 11.0))
        xs = [lo + (hi - lo) * i / n for i in range(n + 1)]
        for i, v in enumerate(xs):
            p = (v, fixed, z_top + col_h / 2) if axis == "x" else (fixed, v, z_top + col_h / 2)
            cols.append(cyl(0.55, col_h, p, col_mat, verts=12))
            if i < len(xs) - 1 and i % 2 == 0:   # banner in every other bay
                mid = v + (xs[1] - xs[0]) / 2
                bc = BANNER_COLS[(i // 2) % len(BANNER_COLS)]
                bmat = get_mat(f"banner_{(i // 2) % len(BANNER_COLS)}", bc, metallic=0.05, rough=0.7,
                               emission=bc, strength=0.25)
                bp = (mid, fixed, z_top + col_h - 2.6) if axis == "x" else (fixed, mid, z_top + col_h - 2.6)
                size = (2.4, 0.14, 4.4) if axis == "x" else (0.14, 2.4, 4.4)
                banners.append(box(size, bp, bmat))
        return xs
    col_run("x", -oy, -ox, ox)
    col_run("x", oy, -ox, ox)
    col_run("y", -ox, -oy + 11, oy - 11)
    col_run("y", ox, -oy + 11, oy - 11)
    lintels.append(box((2 * ox + 2.4, 2.0, lintel_h), (0, -oy, z_top + col_h + lintel_h / 2), col_mat))
    lintels.append(box((2 * ox + 2.4, 2.0, lintel_h), (0, oy, z_top + col_h + lintel_h / 2), col_mat))
    lintels.append(box((2.0, 2 * oy - 2, lintel_h), (-ox, 0, z_top + col_h + lintel_h / 2), col_mat))
    lintels.append(box((2.0, 2 * oy - 2, lintel_h), (ox, 0, z_top + col_h + lintel_h / 2), col_mat))
    # glowing trim along the lintel ring
    trim_mat = _materials["trim_cyan"]
    lintels.append(box((2 * ox + 2.4, 0.5, 0.14), (0, -oy, z_top + col_h + lintel_h + 0.08), trim_mat))
    lintels.append(box((2 * ox + 2.4, 0.5, 0.14), (0, oy, z_top + col_h + lintel_h + 0.08), trim_mat))
    lintels.append(box((0.5, 2 * oy - 2, 0.14), (-ox, 0, z_top + col_h + lintel_h + 0.08), trim_mat))
    lintels.append(box((0.5, 2 * oy - 2, 0.14), (ox, 0, z_top + col_h + lintel_h + 0.08), trim_mat))
    join(cols, "Colonnade")
    join(lintels, "Lintel")
    join(banners, "Banners")

    # corner floodlight towers with warning beacons
    floods, glows = [], []
    tz = z_top + col_h + 5.5
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (ox + 2.5), sy * (oy + 2.5)
            floods.append(cyl(0.5, tz, (x, y, tz / 2), steel_mat, verts=10))
            floods.append(box((3.6, 1.1, 1.6), (x - sx * 1.2, y - sy * 1.2, tz + 0.8), col_mat))
            glows.append(box((3.1, 0.5, 0.9), (x - sx * 1.35, y - sy * 1.35, tz + 0.7), flood_mat))
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.45, location=(x, y, tz + 1.9))
            b = bpy.context.active_object
            b.data.materials.append(beacon_mat)
            glows.append(b)
    join(floods, "Floodlights")
    join(glows, "FloodGlow")


def build_arena(m):
    """Build one arena preset at the origin. Game rect -> centred metres."""
    w, h = m["w"], m["h"]
    gx = lambda x: (x - w / 2) * S
    gy = lambda y: -(y - h / 2) * S   # blender +Y = game -y (north)
    W, H = w * S, h * S
    KT, KH = 1.4, 2.6                 # kerb thickness/height (14/26 game units)
    WH = 6.4                          # cover wall height (64 game units)

    floor_mat = get_mat("floor", FLOOR, metallic=0.15, rough=0.85)
    kerb_mat = get_mat("kerb", KERB, metallic=0.5, rough=0.7)
    wall_mat = get_mat("wall", WALL, metallic=0.6, rough=0.5)
    trim_mat = get_mat("trim_cyan", TRIM_CYAN, metallic=0.2, rough=0.4,
                       emission=TRIM_CYAN, strength=0.55)

    # floor slab (covers play area + kerbs + a 4 m apron)
    apron = KT + 4.0
    join([box((W + 2 * apron, H + 2 * apron, 0.5), (0, 0, -0.25), floor_mat)], "Floor")

    # painted markings: centre ring + halfway line (subtle emissive)
    mark_mat = get_mat("markings", TRIM_CYAN, metallic=0.1, rough=0.5,
                       emission=TRIM_CYAN, strength=0.35)
    bpy.ops.mesh.primitive_torus_add(location=(0, 0, 0.02), major_radius=7.8, minor_radius=0.22,
                                     major_segments=64, minor_segments=8)
    ring = bpy.context.active_object
    ring.scale = (1, 1, 0.18)
    ring.data.materials.append(mark_mat)
    line = box((0.35, H, 0.05), (0, 0, 0.025), mark_mat)
    join([ring, line], "Markings")

    # perimeter kerbs + glowing top strip
    kerbs, glow = [], []
    for sx, sy, kw, kh in ((0, -1, W + 2 * KT, KT), (0, 1, W + 2 * KT, KT),
                           (-1, 0, KT, H), (1, 0, KT, H)):
        x = sx * (W / 2 + KT / 2)
        y = sy * (H / 2 + KT / 2)
        kerbs.append(box((kw, kh, KH), (x, y, KH / 2), kerb_mat))
        glow.append(box((kw, kh, 0.12), (x, y, KH + 0.07), trim_mat))
    join(kerbs, "Kerbs")
    join(glow, "KerbGlow")

    build_colosseum(W, H, KT)

    # cover walls (exact sim rects) + glowing top caps
    walls, caps = [], []
    for (x, y, ww, wh) in m["walls"]:
        cx, cy = gx(x + ww / 2), gy(y + wh / 2)
        walls.append(box((ww * S, wh * S, WH), (cx, cy, WH / 2), wall_mat))
        caps.append(box((ww * S + 0.08, wh * S + 0.08, 0.1), (cx, cy, WH + 0.06), trim_mat))
    join(walls, "Walls")
    join(caps, "WallTrim")

    # hazard basins + rims, grouped per type so the app can overlay shaders
    by_type = {}
    for hz in m["hazards"]:
        by_type.setdefault(hz["type"], []).append(hz)
    for t, items in by_type.items():
        col, rough, metal, emis = HAZARD_STYLE[t]
        basin_mat = get_mat(f"hz_{t}", col, metallic=metal, rough=rough,
                            emission=emis[0] if emis else None,
                            strength=emis[1] if emis else 0.0)
        rim_col, rim_str = RIM_STYLE[t]
        rim_mat = get_mat(f"rim_{t}", rim_col, metallic=0.2, rough=0.5,
                          emission=rim_col, strength=rim_str)
        basins, rims = [], []
        for hz in items:
            cx, cy = gx(hz["x"] + hz["w"] / 2), gy(hz["y"] + hz["h"] / 2)
            hw, hh = hz["w"] * S, hz["h"] * S
            if t == "turntable":
                # recessed circular housing + raised ring — the spinning platter
                # itself is rendered by the app so it can turn with the sim
                basins.append(cyl(hw / 2, 0.16, (cx, cy, -0.06), basin_mat, verts=48))
                bpy.ops.mesh.primitive_torus_add(location=(cx, cy, 0.10),
                                                 major_radius=hw / 2 + 0.25, minor_radius=0.18,
                                                 major_segments=48, minor_segments=8)
                ring = bpy.context.active_object
                ring.data.materials.append(rim_mat)
                rims.append(ring)
                continue
            basins.append(box((hw, hh, 0.16), (cx, cy, -0.06), basin_mat))  # top at +0.02
            for rx, ry, rw, rh in ((0, -1, hw + 0.8, 0.4), (0, 1, hw + 0.8, 0.4),
                                   (-1, 0, 0.4, hh), (1, 0, 0.4, hh)):
                rims.append(box((rw, rh, 0.3),
                                (cx + rx * (hw / 2 + 0.2), cy + ry * (hh / 2 + 0.2), 0.15),
                                rim_mat))
        join(basins, f"Hazard_{t}")
        join(rims, f"HazardRim_{t}")

    # pickup pads (spawn markers)
    pads = []
    for p in m["pickups"]:
        pad_mat = get_mat(f"pad_{p['kind']}", PICKUP_COL[p["kind"]], metallic=0.3, rough=0.4,
                          emission=PICKUP_COL[p["kind"]], strength=0.5)
        pads.append(cyl(1.5, 0.06, (gx(p["x"]), gy(p["y"]), 0.03), pad_mat, verts=28))
    join(pads, "Pads")


def preview(path, W, H):
    """Drop in a sun + camera and render a bird's-eye 3/4 view."""
    bpy.ops.object.light_add(type="SUN", location=(30, -30, 60),
                             rotation=(math.radians(45), 0, math.radians(35)))
    bpy.context.active_object.data.energy = 3.5
    dist = max(W, H) * 1.3   # pull back far enough to frame the colosseum bowl
    bpy.ops.object.camera_add(location=(0, -dist * 0.85, dist * 0.62))
    cam = bpy.context.active_object
    cam.data.lens = 32
    bpy.ops.object.empty_add(location=(0, 0, 0))
    target = bpy.context.active_object
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    bpy.context.scene.camera = cam
    bpy.context.scene.render.resolution_x = 1280
    bpy.context.scene.render.resolution_y = 800
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv else here
    game = os.path.abspath(argv[argv.index("--game") + 1]) if "--game" in argv \
        else os.path.join(here, "..", "robot-wars", "robot-wars")

    # load engine/maps.py directly (pure stdlib, no engine/__init__ side effects)
    spec = importlib.util.spec_from_file_location("maps", os.path.join(game, "engine", "maps.py"))
    maps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maps)

    glb_dir = os.path.join(out, "glb-arenas")
    os.makedirs(glb_dir, exist_ok=True)

    for name in maps.names():
        m = maps.get(name)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _materials.clear()
        build_arena(m)
        path = os.path.join(glb_dir, f"arena_{name}.glb")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB",
                                  export_apply=True, export_yup=True)
        preview(os.path.join(glb_dir, f"arena_{name}.png"), m["w"] * S, m["h"] * S)
        print(f"exported {path}")

    print(f"done: {len(maps.names())} arenas in {glb_dir}")


main()
