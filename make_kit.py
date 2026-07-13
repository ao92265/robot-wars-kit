#!/usr/bin/env python3
"""Package the workshop into shareable zips (both go to dist/):

  python3 make_kit.py            ->  robot-wars-player-kit.zip  +  robot-wars-dev-kit.zip
  python3 make_kit.py --player   ->  just the player kit
  python3 make_kit.py --dev      ->  just the dev kit

PLAYER KIT — for people writing bots. The game plus a prebuilt, self-contained
3D viewer (all Blender assets baked in). Needs only Python 3 — no installs.
  unzip -> edit robot-wars/my_bot.py -> python3 arena.py [--record m.jsonl]
        -> open tournament/visual/arena.html and drop the recording on it.
Ships kit-CLAUDE.md as the kit's CLAUDE.md (league rules for AI pit crews).

DEV KIT — for people working on the engine, arenas, and models. The whole
studio: engine + tournament sources, the Blender build scripts and their GLB
outputs (robot-models/), and the showcase viewer source (arena-viewer/).

Both kits are built from `git archive HEAD` — a committed, reproducible tree —
never from the working directory, so local scratch files and uncommitted edits
can't leak into a kit. The freshly rebuilt arena.html is overlaid on top.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

SKIP_FILES = {".DS_Store"}

# player kit: players get the game, not the studio or organizer tooling
PLAYER_SKIP_DIRS = {"robot-models", "arena-viewer", "docs"}
PLAYER_SKIP_FILES = SKIP_FILES | {"README-DEV.md", "make_kit.py", "kit-CLAUDE.md"}


def export_head(dest):
    """Materialise the committed tree (git archive HEAD) into dest/."""
    tar = os.path.join(dest, "_head.tar")
    with open(tar, "wb") as f:
        subprocess.run(["git", "-C", HERE, "archive", "HEAD"], stdout=f, check=True)
    subprocess.run(["tar", "-xf", tar, "-C", dest], check=True)
    os.remove(tar)


def _zip_tree(z, root, arc_prefix, skip_dirs=(), skip_files=()):
    n = 0
    for cur, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(cur, root)
        dirs[:] = [d for d in dirs
                   if not (rel_dir == "." and d in skip_dirs) and d != "__pycache__"]
        for f in sorted(files):
            if f in skip_files:
                continue
            rel = os.path.relpath(os.path.join(cur, f), root)
            z.write(os.path.join(cur, f), os.path.join(arc_prefix, rel))
            n += 1
    return n


def build_player_kit(out_dir, tree):
    out = os.path.join(out_dir, "robot-wars-player-kit.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        n = _zip_tree(z, tree, "robot-wars", PLAYER_SKIP_DIRS, PLAYER_SKIP_FILES)
        # league rules for AI pit crews ship as the kit's CLAUDE.md
        claude_md = os.path.join(tree, "kit-CLAUDE.md")
        if os.path.exists(claude_md):
            z.write(claude_md, os.path.join("robot-wars", "CLAUDE.md"))
            n += 1
    print(f"wrote {out}  ({os.path.getsize(out) / 1024 / 1024:.1f} MB, {n} files)")
    return out


def build_dev_kit(out_dir, tree):
    out = os.path.join(out_dir, "robot-wars-dev-kit.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        n = _zip_tree(z, tree, "robot-wars-dev", skip_files=SKIP_FILES)
    print(f"wrote {out}  ({os.path.getsize(out) / 1024 / 1024:.1f} MB, {n} files)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", action="store_true", help="build only the player kit")
    ap.add_argument("--dev", action="store_true", help="build only the dev kit")
    args = ap.parse_args()
    both = not (args.player or args.dev)
    out_dir = os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)

    # make sure the bundled viewer is current (full build: all 36 robot models,
    # so any loadout a participant records renders with the Blender assets)
    print("rebuilding tournament/visual/arena.html ...")
    subprocess.run([sys.executable,
                    os.path.join(HERE, "tournament", "visual", "build_arena.py")],
                   check=True)

    tmp = tempfile.mkdtemp(prefix="robot-wars-kit-")
    try:
        export_head(tmp)
        # the committed arena.html may lag the one just rebuilt — overlay it
        shutil.copy2(os.path.join(HERE, "tournament", "visual", "arena.html"),
                     os.path.join(tmp, "tournament", "visual", "arena.html"))
        if args.player or both:
            build_player_kit(out_dir, tmp)
        if args.dev or both:
            build_dev_kit(out_dir, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
