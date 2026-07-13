"""Generate the four spinner melee weapons as attachable Blender models.

The game's `shape` axis (tank | speeder | orb | spike) is the robot's melee
identity — a big spinning weapon. The 36 robot GLBs cover size/gun/engine, so
the spinner ships as a separate attachment the renderer parents onto the hull
and spins every frame.

Built at MEDIUM reference scale (r = 1.6 m, matching robot_medium_*); the
renderer scales the attachment by (robot_radius / 16). Each file has a static
"mount" root and a child node named "spin" whose origin is the rotation pivot:
  tank    -> heavy horizontal drum across the nose         (spin axis glTF x)
  speeder -> overhead sweeping bar on a centre post        (spin axis glTF y)
  orb     -> vertical buzzsaw disc at the nose             (spin axis glTF z)
  spike   -> spinning spiked drum at the nose              (spin axis glTF x)

Run headless:
  blender --background --python build_spinners.py -- --out <dir>
Outputs <out>/glb-spinners/spinner_<shape>.glb
"""

import math
import os
import sys

import bpy

R = 1.6  # medium chassis half-width, metres

DARK = (0.07, 0.07, 0.08, 1.0)
STEEL = (0.55, 0.57, 0.60, 1.0)

_materials = {}


def get_mat(name, color, metallic=0.6, rough=0.45):
    if name in _materials:
        return _materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_backface_culling = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    _materials[name] = mat
    return mat


def _new(mat):
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def box(size_xyz, loc, mat, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rot)
    obj = _new(mat)
    obj.scale = (size_xyz[0] / 2, size_xyz[1] / 2, size_xyz[2] / 2)
    return obj


def cyl(radius, depth, loc, mat, rot=(0, 0, 0), verts=20):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth,
                                        location=loc, rotation=rot)
    return _new(mat)


def cone(r1, r2, depth, loc, mat, rot=(0, 0, 0), verts=8):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2, depth=depth,
                                    location=loc, rotation=rot)
    return _new(mat)


def join(parts, name, origin=None):
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if origin is not None:
        bpy.context.scene.cursor.location = origin
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        bpy.context.scene.cursor.location = (0, 0, 0)
    return obj


ROT_Y = (0, math.radians(90), 0)   # cylinder axis along X (drums)
ROT_X = (math.radians(90), 0, 0)   # cylinder axis along Y (saw disc)


def spinner_tank():
    """Heavy horizontal DRUM across the nose, spin axis X."""
    dark, steel = get_mat("dark", DARK, 0.3, 0.7), get_mat("steel", STEEL, 0.9, 0.3)
    c = (0, 1.30 * R, 0.55 * R)
    drum = [cyl(0.40 * R, 1.30 * R, c, steel, rot=ROT_Y, verts=18)]
    for i in range(3):
        a = i * 2.1
        dx = (i - 1) * 0.42 * R
        drum.append(box((0.16 * R, 0.5 * R, 0.16 * R), (dx, c[1], c[2]), dark,
                        rot=(a, 0, 0)))
    spin = join(drum, "spin", origin=c)
    arms = [box((0.10 * R, 0.55 * R, 0.10 * R), (sx * 0.5 * R, 1.02 * R, 0.62 * R), dark,
                rot=(math.radians(25), 0, 0)) for sx in (-1, 1)]
    mount = join(arms, "spinner_tank")
    spin.parent = mount
    return mount


def spinner_speeder():
    """Overhead sweeping BAR on a centre post, spin axis Z-up."""
    dark, steel = get_mat("dark", DARK, 0.3, 0.7), get_mat("steel", STEEL, 0.9, 0.3)
    top = (0, 0, 1.62 * R)
    post = [cyl(0.10 * R, 0.5 * R, (0, 0, 1.40 * R), dark, verts=12)]
    mount = join(post, "spinner_speeder")
    bar = [box((2.5 * R, 0.24 * R, 0.13 * R), top, steel)]
    for sx in (-1, 1):
        bar.append(box((0.30 * R, 0.32 * R, 0.30 * R), (sx * 1.15 * R, 0, top[2]), dark))
    spin = join(bar, "spin", origin=top)
    spin.parent = mount
    return mount


def spinner_orb():
    """Vertical BUZZSAW disc at the nose, spin axis Y-forward."""
    dark, steel = get_mat("dark", DARK, 0.3, 0.7), get_mat("steel", STEEL, 0.9, 0.3)
    c = (0, 1.32 * R, 0.62 * R)
    disc = [cyl(0.72 * R, 0.09 * R, c, steel, rot=ROT_X, verts=24)]
    for i in range(8):
        a = i / 8 * math.pi * 2
        disc.append(cone(0.09 * R, 0.0, 0.24 * R,
                         (c[0] + math.sin(a) * 0.78 * R, c[1], c[2] + math.cos(a) * 0.78 * R),
                         steel, rot=(0, a, 0)))
    spin = join(disc, "spin", origin=c)
    arm = [box((0.10 * R, 0.5 * R, 0.10 * R), (0, 1.05 * R, 0.45 * R), dark,
               rot=(math.radians(-30), 0, 0))]
    mount = join(arm, "spinner_orb")
    spin.parent = mount
    return mount


def spinner_spike():
    """Spinning SPIKED DRUM at the nose, spin axis X."""
    dark, steel = get_mat("dark", DARK, 0.3, 0.7), get_mat("steel", STEEL, 0.9, 0.3)
    c = (0, 1.28 * R, 0.52 * R)
    drum = [cyl(0.34 * R, 1.10 * R, c, dark, rot=ROT_Y, verts=14)]
    for i in range(10):
        b = i / 10 * math.pi * 2
        dx = ((i % 3) - 1) * 0.34 * R
        drum.append(cone(0.09 * R, 0.0, 0.30 * R,
                         (c[0] + dx, c[1] + math.sin(b) * 0.42 * R, c[2] + math.cos(b) * 0.42 * R),
                         steel, rot=(-b, 0, 0)))
    spin = join(drum, "spin", origin=c)
    arms = [box((0.10 * R, 0.5 * R, 0.10 * R), (sx * 0.45 * R, 1.0 * R, 0.58 * R), dark,
                rot=(math.radians(25), 0, 0)) for sx in (-1, 1)]
    mount = join(arms, "spinner_spike")
    spin.parent = mount
    return mount


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv \
        else os.path.dirname(os.path.abspath(__file__))
    glb_dir = os.path.join(out, "glb-spinners")
    os.makedirs(glb_dir, exist_ok=True)

    builders = {"tank": spinner_tank, "speeder": spinner_speeder,
                "orb": spinner_orb, "spike": spinner_spike}
    for shape, fn in builders.items():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _materials.clear()
        root = fn()
        bpy.ops.object.select_all(action="DESELECT")
        root.select_set(True)
        for child in root.children:
            child.select_set(True)
        bpy.context.view_layer.objects.active = root
        path = os.path.join(glb_dir, f"spinner_{shape}.glb")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                  export_apply=True, export_yup=True)
        print(f"exported {path}")
    print(f"done: {len(builders)} spinners in {glb_dir}")


main()
