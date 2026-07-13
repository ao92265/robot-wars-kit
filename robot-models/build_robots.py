"""Generate Blender models for every visual permutation of a Robot Wars robot.

The game's loadout system (robot-wars/engine/config.py) defines three visual axes:
  size:   small | medium | large          (chassis radius 12 / 16 / 22 units)
  gun:    laser | cannon | shotgun        (turret hardware)
  engine: standard | sprint | tank | hover (drivetrain)
= 36 permutations. Stat points (hp/speed/...) don't change the physical build.

Run headless:
  blender --background --python build_robots.py -- --out <dir>
  blender --background --python build_robots.py -- --out <dir> --body-color "#ff6b6b"

--body-color bakes every chassis in a single custom colour (any #rrggbb, same
scheme as a match robot's `color` field) instead of the per-engine palette.
The arena viewer can also re-tint the neutral models live, without re-baking.

Outputs:
  <out>/glb/robot_<size>_<gun>_<engine>.glb   (36 files, Y-up, ready for three.js)
  <out>/robots_all.blend                      (all 36 arranged in a grid)
"""

import math
import os
import sys

import bpy
from mathutils import Matrix

# --- permutation space (mirrors engine/config.py) ---------------------------
SIZES = {"small": 1.2, "medium": 1.6, "large": 2.2}  # chassis half-width, metres
GUNS = ("laser", "cannon", "shotgun")
ENGINES = ("standard", "sprint", "tank", "hover")

BODY_COLOR = {
    "standard": (0.45, 0.47, 0.50, 1.0),
    "sprint":   (0.22, 0.42, 0.68, 1.0),
    "tank":     (0.32, 0.42, 0.28, 1.0),
    "hover":    (0.46, 0.34, 0.62, 1.0),
}
GUN_COLOR = {
    "laser":   (0.85, 0.10, 0.12, 1.0),
    "cannon":  (0.85, 0.42, 0.08, 1.0),
    "shotgun": (0.90, 0.78, 0.10, 1.0),
}
DARK = (0.07, 0.07, 0.08, 1.0)
STEEL = (0.55, 0.57, 0.60, 1.0)
GLOW_CYAN = (0.10, 0.90, 1.00, 1.0)
GLOW_RED = (1.00, 0.15, 0.10, 1.0)

_materials = {}


def get_mat(name, color, metallic=0.6, rough=0.45, emission=None, strength=0.0):
    if name in _materials:
        return _materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    # export single-sided — legacy three double-sided lighting mis-shades solids
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


def _new_obj(mat, smooth=False):
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    if smooth:
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(40))
        except Exception:
            try:
                bpy.ops.object.shade_smooth()
            except Exception:
                pass
    return obj


def box(size_xyz, loc, mat, rot=(0, 0, 0), bevel=0.0):
    # size=2 -> unit half-extents, so scale=size/2 yields true world dimensions
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rot)
    obj = _new_obj(mat)
    obj.scale = (size_xyz[0] / 2, size_xyz[1] / 2, size_xyz[2] / 2)
    if bevel > 0:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 2
    return obj


def cyl(radius, depth, loc, mat, rot=(0, 0, 0), verts=24):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=verts, radius=radius, depth=depth, location=loc, rotation=rot)
    return _new_obj(mat, smooth=True)


def cone(r1, r2, depth, loc, mat, rot=(0, 0, 0), verts=20):
    bpy.ops.mesh.primitive_cone_add(
        vertices=verts, radius1=r1, radius2=r2, depth=depth, location=loc, rotation=rot)
    return _new_obj(mat, smooth=True)


ROT_X = (math.radians(90), 0, 0)   # cylinder axis along Y (barrels; robot faces +Y)
ROT_Y = (0, math.radians(90), 0)   # cylinder axis along X (wheels)
# bake "+Z points forward (+Y)" into mesh data — muzzle-effect objects must keep
# identity rotation, or the glTF NLA baking corrupts the unkeyed rotation channel
MESH_FWD = Matrix.Rotation(math.radians(-90), 4, "X")


# --- part builders -----------------------------------------------------------

def build_chassis(r, z0, engine):
    """Main hull + front wedge + turret ring. Returns (parts, top_z)."""
    mat = get_mat(f"body_{engine}", BODY_COLOR[engine], metallic=0.75, rough=0.4)
    dark = get_mat("dark", DARK, metallic=0.3, rough=0.7)
    W, L, H = 1.8 * r, 2.2 * r, 0.85 * r
    zc = z0 + H / 2
    parts = [box((W, L, H), (0, 0, zc), mat, bevel=0.08 * r)]
    # front wedge (classic Robot Wars ram plate)
    parts.append(box((W * 0.96, 0.85 * r, H * 0.55), (0, L / 2 + 0.18 * r, z0 + H * 0.28),
                     mat, rot=(math.radians(-32), 0, 0), bevel=0.05 * r))
    # rear bumper
    parts.append(box((W * 0.8, 0.22 * r, H * 0.5), (0, -L / 2 - 0.08 * r, zc), dark))
    top = z0 + H
    # turret ring
    parts.append(cyl(0.62 * r, 0.30 * r, (0, 0.1 * r, top + 0.13 * r),
                     get_mat("steel", STEEL, metallic=0.9, rough=0.3)))
    return parts, top + 0.28 * r


def build_gun(gun, r, top_z):
    """Returns (parts, muzzle_y) — muzzle_y is where the flash spawns."""
    parts = []
    acc = get_mat(f"gun_{gun}", GUN_COLOR[gun], metallic=0.55, rough=0.35)
    dark = get_mat("dark", DARK)
    steel = get_mat("steel", STEEL, metallic=0.9, rough=0.3)
    zg = top_z + 0.18 * r
    if gun == "laser":
        # slim long barrel, emissive red tip, top sight rail
        parts.append(box((0.5 * r, 0.7 * r, 0.36 * r), (0, 0.15 * r, zg), acc, bevel=0.04 * r))
        parts.append(cyl(0.09 * r, 1.6 * r, (0, 0.5 * r + 0.8 * r, zg), dark, rot=ROT_X))
        parts.append(cyl(0.055 * r, 0.18 * r, (0, 0.5 * r + 1.62 * r, zg),
                         get_mat("glow_red", GLOW_RED, emission=GLOW_RED, strength=6.0), rot=ROT_X))
        parts.append(box((0.08 * r, 0.5 * r, 0.12 * r), (0, 0.3 * r, zg + 0.24 * r), steel))
        muzzle_y = 2.3 * r
    elif gun == "cannon":
        # fat short barrel, muzzle brake, heavy breech block
        parts.append(box((0.75 * r, 0.9 * r, 0.5 * r), (0, 0.05 * r, zg), acc, bevel=0.05 * r))
        parts.append(cyl(0.20 * r, 1.25 * r, (0, 0.5 * r + 0.6 * r, zg), dark, rot=ROT_X))
        parts.append(cyl(0.28 * r, 0.28 * r, (0, 0.5 * r + 1.2 * r, zg), steel, rot=ROT_X))
        parts.append(box((0.5 * r, 0.35 * r, 0.35 * r), (0, -0.5 * r, zg), dark, bevel=0.04 * r))
        muzzle_y = 1.9 * r
    else:  # shotgun — triple short barrels, wide muzzle plate
        parts.append(box((0.9 * r, 0.65 * r, 0.4 * r), (0, 0.1 * r, zg), acc, bevel=0.05 * r))
        for dx in (-0.26, 0.0, 0.26):
            parts.append(cyl(0.11 * r, 0.9 * r, (dx * r, 0.42 * r + 0.45 * r, zg), dark, rot=ROT_X))
        parts.append(box((0.95 * r, 0.1 * r, 0.42 * r), (0, 0.42 * r + 0.92 * r, zg), steel, bevel=0.03 * r))
        muzzle_y = 1.5 * r
    return parts, (0, muzzle_y, zg)


def build_drivetrain(engine, r):
    """Returns (parts, ground_clearance z0, wheel_groups).
    wheel_groups is [(objs, centre), ...] — each becomes its OWN node with the
    origin on its axle, so the renderer can roll them as the robot drives."""
    parts = []
    wheel_groups = []
    dark = get_mat("dark", DARK, metallic=0.3, rough=0.7)
    steel = get_mat("steel", STEEL, metallic=0.9, rough=0.3)
    W = 1.8 * r
    if engine == "standard":
        z0 = 0.38 * r
        wr = 0.40 * r
        for sx in (-1, 1):
            for sy in (-1, 1):
                # wheel inner face tucked 0.07r into the hull so they read as attached
                c = (sx * (W / 2 + 0.06 * r), sy * 0.68 * r, wr)
                wheel_groups.append(([cyl(wr, 0.26 * r, c, dark, rot=ROT_Y),
                                      cyl(0.10 * r, 0.34 * r, c, steel, rot=ROT_Y)], c))
    elif engine == "sprint":
        z0 = 0.40 * r
        for sx in (-1, 1):
            cr = (sx * (W / 2 + 0.06 * r), -0.7 * r, 0.46 * r)
            cf = (sx * (W / 2 + 0.05 * r), 0.75 * r, 0.34 * r)
            wheel_groups.append(([cyl(0.46 * r, 0.28 * r, cr, dark, rot=ROT_Y),
                                  cyl(0.12 * r, 0.32 * r, cr, steel, rot=ROT_Y)], cr))
            wheel_groups.append(([cyl(0.34 * r, 0.24 * r, cf, dark, rot=ROT_Y),
                                  cyl(0.09 * r, 0.28 * r, cf, steel, rot=ROT_Y)], cf))
            # exhaust pipes
            parts.append(cyl(0.09 * r, 0.6 * r, (sx * 0.45 * r, -1.35 * r, z0 + 0.55 * r), steel, rot=ROT_X))
        # rear spoiler
        parts.append(box((1.7 * r, 0.22 * r, 0.06 * r), (0, -1.15 * r, z0 + 1.25 * r),
                         get_mat(f"body_{engine}", BODY_COLOR[engine])))
        for sx in (-1, 1):
            parts.append(box((0.07 * r, 0.15 * r, 0.45 * r), (sx * 0.7 * r, -1.12 * r, z0 + 1.0 * r), dark))
    elif engine == "tank":
        z0 = 0.55 * r
        for sx in (-1, 1):
            x = sx * (W / 2 + 0.22 * r)  # track pod overlaps the hull side
            parts.append(box((0.55 * r, 2.5 * r, 0.75 * r), (x, 0, 0.42 * r), dark, bevel=0.14 * r))
            for wy in (-0.85, 0.0, 0.85):
                c = (x, wy * r, 0.30 * r)
                wheel_groups.append(([cyl(0.22 * r, 0.58 * r, c, steel, rot=ROT_Y)], c))
        # extra front armor plate
        parts.append(box((1.9 * r, 0.12 * r, 0.6 * r), (0, 1.35 * r, z0 + 0.35 * r),
                         steel, rot=(math.radians(-20), 0, 0), bevel=0.03 * r))
    else:  # hover
        z0 = 0.72 * r
        glow = get_mat("glow_cyan", GLOW_CYAN, emission=GLOW_CYAN, strength=8.0)
        # hover skirt
        skirt = cyl(1.0 * r, 0.28 * r, (0, 0, z0 - 0.12 * r), dark)
        skirt.scale = (1.05, 1.35, 1.0)
        parts.append(skirt)
        # downward thrusters
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = sx * 0.62 * r, sy * 0.75 * r
                parts.append(cone(0.26 * r, 0.14 * r, 0.3 * r, (x, y, z0 - 0.35 * r), steel,
                                  rot=(math.radians(180), 0, 0)))
                parts.append(cyl(0.13 * r, 0.05 * r, (x, y, z0 - 0.51 * r), glow))
    return parts, z0, wheel_groups


def join(parts, name):
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name
    # join() keeps the active part's transform (e.g. a wheel's offset/rotation);
    # bake it into the mesh so the object sits at the origin with identity
    # transform — parenting and recoil keyframes rely on that.
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def push_shoot_track(obj, action_name):
    """Move the object's freshly keyed action onto an NLA track named 'shoot'.
    Same-named tracks across objects merge into ONE glTF animation on export."""
    ad = obj.animation_data
    act = ad.action
    act.name = action_name
    ad.action = None
    track = ad.nla_tracks.new()
    track.name = "shoot"
    track.strips.new("shoot", 1, act)


def _muzzle_fx(name, hull, mat):
    """Name the active object and parent it to the hull as a muzzle effect."""
    obj = _new_obj(mat)
    obj.name = name
    obj.data.name = name
    obj.parent = hull
    return obj


def _key_scales(obj, keys):
    """Keyframe scale pops; the object rests invisible at scale 0."""
    for frame, s in keys:
        obj.scale = s if isinstance(s, tuple) else (s, s, s)
        obj.keyframe_insert("scale", frame=frame)
    obj.scale = (0.0, 0.0, 0.0)


def _key_gun(gun_obj, loc_keys, rot_keys=()):
    """Keyframe recoil as y-offsets from rest plus optional pitch (x-rotation)."""
    rest = gun_obj.location.copy()
    for frame, dy in loc_keys:
        gun_obj.location = (rest.x, rest.y + dy, rest.z)
        gun_obj.keyframe_insert("location", frame=frame)
    gun_obj.location = rest
    for frame, rx in rot_keys:
        gun_obj.rotation_euler = (rx, 0, 0)
        gun_obj.keyframe_insert("rotation_euler", frame=frame)
    gun_obj.rotation_euler = (0, 0, 0)


def animate_fire(name, gun, r, hull, gun_obj, muzzle):
    """Bake a per-weapon 'shoot' clip (24 fps).
    laser:   no recoil — a beam lance stabs out, holds, then fades (frames 1-8)
    cannon:  heavy kick + barrel pitch-up, big fireball, slow return (1-16)
    shotgun: pump double-kick with a wide pellet-spray cone (1-9)"""
    flash_mat = get_mat("flash", (1.0, 0.75, 0.30, 1.0), metallic=0.0, rough=0.5,
                        emission=(1.0, 0.75, 0.30, 1.0), strength=14.0)
    if gun == "laser":
        beam_len = 3.4 * r
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.055 * r, depth=beam_len,
                                            location=muzzle)
        # modest emission strength — higher values blow out to white under the
        # viewer's ACES tone mapping and the beam stops reading as red
        beam = _muzzle_fx(name + "_beam", hull,
                          get_mat("beam_red", (0.08, 0.01, 0.01, 1.0), metallic=0.0, rough=0.4,
                                  emission=(1.0, 0.02, 0.02, 1.0), strength=1.2))
        beam.data.transform(MESH_FWD @ Matrix.Translation((0, 0, beam_len / 2)))  # grow from the muzzle
        _key_scales(beam, ((1, (1, 0, 1)), (2, (1, 1, 1)), (5, (1, 1, 1)), (8, (0, 1, 0))))
        push_shoot_track(beam, name + "_beam_shoot")
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.14 * r, location=muzzle)
        flash = _muzzle_fx(name + "_flash", hull, flash_mat)
        _key_scales(flash, ((1, 0.0), (2, 1.0), (4, 0.0)))
        push_shoot_track(flash, name + "_flash_shoot")
        _key_gun(gun_obj, ((1, 0.0), (2, -0.05 * r), (4, 0.0)))  # barely a shiver
        push_shoot_track(gun_obj, name + "_gun_shoot")
    elif gun == "cannon":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=0.45 * r, location=muzzle)
        flash = _muzzle_fx(name + "_flash", hull, flash_mat)
        _key_scales(flash, ((1, 0.0), (2, 1.0), (3, 1.15), (6, 0.0)))
        push_shoot_track(flash, name + "_flash_shoot")
        _key_gun(gun_obj,
                 ((1, 0.0), (3, -0.40 * r), (10, -0.10 * r), (16, 0.0)),
                 ((1, 0.0), (3, math.radians(6)), (10, math.radians(1.5)), (16, 0.0)))
        push_shoot_track(gun_obj, name + "_gun_shoot")
    else:  # shotgun
        spray_len = 1.1 * r
        bpy.ops.mesh.primitive_cone_add(vertices=16, radius1=0.06 * r, radius2=0.55 * r,
                                        depth=spray_len, location=muzzle)
        spray = _muzzle_fx(name + "_spray", hull, flash_mat)
        spray.data.transform(MESH_FWD @ Matrix.Translation((0, 0, spray_len / 2)))  # cone apex at the muzzle
        _key_scales(spray, ((1, 0.0), (2, (1, 0.6, 1)), (3, 1.0), (5, 0.0)))
        push_shoot_track(spray, name + "_spray_shoot")
        _key_gun(gun_obj,
                 ((1, 0.0), (2, -0.22 * r), (4, -0.04 * r), (6, -0.13 * r), (9, 0.0)),
                 ((1, 0.0), (2, math.radians(3)), (5, 0.0)))
        push_shoot_track(gun_obj, name + "_gun_shoot")


def build_robot(size, gun, engine):
    """Build one robot at the origin facing +Y.
    Structure: hull (chassis+drivetrain) -> gun + muzzle effects, each carrying
    a baked per-weapon 'shoot' animation clip."""
    r = SIZES[size]
    name = f"robot_{size}_{gun}_{engine}"
    drive, z0, wheel_groups = build_drivetrain(engine, r)
    chassis, top_z = build_chassis(r, z0, engine)
    gun_parts, muzzle = build_gun(gun, r, top_z)
    hull = join(drive + chassis, name)
    # wheels stay separate nodes, origin on the axle, so playback can roll them
    for i, (objs, centre) in enumerate(wheel_groups):
        wobj = join(objs, f"{name}_wheel{i}")
        bpy.context.scene.cursor.location = centre
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        wobj.parent = hull
    bpy.context.scene.cursor.location = (0, 0, 0)
    gun_obj = join(gun_parts, name + "_gun")
    # pivot the gun at the turret ring so recoil pitch rocks it in place
    bpy.context.scene.cursor.location = (0, 0.1 * r, top_z)
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    bpy.context.scene.cursor.location = (0, 0, 0)
    gun_obj.parent = hull

    animate_fire(name, gun, r, hull, gun_obj, muzzle)
    return hull


# --- main --------------------------------------------------------------------

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv \
        else os.path.dirname(os.path.abspath(__file__))
    if "--body-color" in argv:
        hexcol = argv[argv.index("--body-color") + 1].lstrip("#")
        # sRGB hex -> linear, the space Blender's Base Color expects
        rgb = tuple((int(hexcol[i:i + 2], 16) / 255.0) ** 2.2 for i in (0, 2, 4))
        for engine in BODY_COLOR:
            BODY_COLOR[engine] = (*rgb, 1.0)
    glb_dir = os.path.join(out, "glb")
    os.makedirs(glb_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _materials.clear()
    bpy.context.scene.render.fps = 24
    bpy.context.scene.frame_end = 20

    combos = [(s, g, e) for e in ENGINES for s in SIZES for g in GUNS]
    spacing = 7.0
    cols = 9  # one row per engine, columns = size x gun
    for i, (size, gun, engine) in enumerate(combos):
        obj = build_robot(size, gun, engine)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        for child in obj.children:
            child.select_set(True)
        bpy.context.view_layer.objects.active = obj
        path = os.path.join(glb_dir, f"{obj.name}.glb")
        bpy.ops.export_scene.gltf(
            filepath=path, export_format="GLB", use_selection=True,
            export_apply=True, export_yup=True,
            export_animations=True, export_animation_mode="NLA_TRACKS")
        # park it in the showcase grid
        col, row = i % cols, i // cols
        obj.location = ((col - (cols - 1) / 2) * spacing, row * spacing * 1.4, 0)
        print(f"[{i + 1}/{len(combos)}] exported {path}")

    # simple stage for the .blend showcase
    bpy.ops.mesh.primitive_plane_add(size=90, location=(0, 15, 0))
    bpy.context.active_object.data.materials.append(
        get_mat("floor", (0.05, 0.05, 0.06, 1.0), metallic=0.1, rough=0.9))
    bpy.ops.object.light_add(type="SUN", location=(20, -20, 40), rotation=(math.radians(50), 0, math.radians(30)))
    bpy.context.active_object.data.energy = 4.5
    bpy.ops.object.light_add(type="AREA", location=(-25, -10, 30), rotation=(math.radians(35), math.radians(-25), 0))
    fill = bpy.context.active_object
    fill.data.energy = 3000.0
    fill.data.size = 30.0
    bpy.ops.object.camera_add(location=(0, -42, 34))
    cam = bpy.context.active_object
    cam.data.lens = 28
    bpy.ops.object.empty_add(location=(0, 14, 0))
    target = bpy.context.active_object
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    bpy.context.scene.camera = cam

    blend_path = os.path.join(out, "robots_all.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"saved {blend_path}")
    print(f"done: {len(combos)} GLB models in {glb_dir}")


main()
