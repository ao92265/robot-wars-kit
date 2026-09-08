#!/usr/bin/env python3
"""Voice coverage gate.

Cross-checks three things that drift apart silently:
  1. every event kind engine/game.py emits has a renderer branch,
  2. every pickup kind the engine can drop has a clip key,
  3. every clip key arena.app.js asks for is actually baked into BOTH built
     pages (the arena and the tournament shell each inline their own copy).

Exits non-zero on any gap. Run after editing lines.py, the event handlers, or
the engine's event set:

    python3 -m tournament.voice.check_coverage
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ENGINE = os.path.join(ROOT, "engine", "game.py")
APP = os.path.join(ROOT, "tournament", "visual", "src", "arena.app.js")
VISUAL = os.path.join(ROOT, "tournament", "visual")
# both built pages inline their own copy of the pack; the tournament shell is
# the one the show is driven from, and it is the one that has silently missed
# the bake step before
BUILT = [os.path.join(VISUAL, "arena.html"), os.path.join(VISUAL, "tournament.html")]


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def engine_event_kinds(src):
    return set(re.findall(r'"kind":\s*"([a-z_]+)"', src))


def engine_pickup_kinds(src):
    return set(re.findall(r'p\.kind == "([a-z_]+)"', src))


def baked_keys(path):
    """Keys inlined into a built page (the thing that actually ships)."""
    m = re.search(r"window\.__VOICE_CLIPS__ = (\{.*?\});", read(path), re.S)
    return set(json.loads(m.group(1))) if m else set()


def ui_table_keys(src):
    """Event kinds routed through a UI lookup table rather than an explicit
    `ev.kind === "x"` branch (PICKUP_UI, HAZARD_UI)."""
    keys = set()
    for name in ("PICKUP_UI", "HAZARD_UI"):
        m = re.search(r"const %s = \{(.*?)\};" % name, src, re.S)
        if m:
            keys |= set(re.findall(r"(\w+):", m.group(1)))
    return keys


def main():
    engine, app = read(ENGINE), read(APP)
    pages = [(p, baked_keys(p)) for p in BUILT if os.path.exists(p)]
    fails = []

    kinds = engine_event_kinds(engine)
    routed = ui_table_keys(app)
    for k in sorted(kinds):
        if k in ("pickup",):
            continue
        if not re.search(r'ev\.kind === "%s"' % k, app) and k not in routed:
            fails.append(f"engine emits event kind {k!r} but arena.app.js has no branch for it")

    for k in sorted(engine_pickup_kinds(engine)):
        for path, baked in pages:
            if "pickup_" + k not in baked:
                fails.append(f"pickup kind {k!r} has no baked clip 'pickup_{k}' in {os.path.basename(path)}")

    # Static clip keys the renderer asks for. Per-bot keys are built by
    # concatenation ("elim_" + slug(...)); those are the bake's job to cover
    # since it derives them from the same roster, so skip any literal that is
    # immediately followed by a '+'.
    # The array form nests one level — playClip(["flip_" + voiceKey[ev.id], "flip"])
    # — so a plain [^\]]* stops at the index expression's ']' and never sees the
    # generic fallback that follows it.
    asked = set()
    for expr in re.findall(r"playClip\(\s*(\[(?:[^\[\]]|\[[^\]]*\])*\]|\"[a-z0-9_]+\")", app):
        for m in re.finditer(r'"([a-z0-9_]+)"(\s*\+)?', expr):
            if not m.group(2):
                asked.add(m.group(1))
    for k in sorted(asked):
        for path, baked in pages:
            if k not in baked:
                fails.append(f"arena.app.js asks for clip {k!r}, not baked into {os.path.basename(path)}")

    if fails:
        print("VOICE COVERAGE FAILURES:")
        for f in fails:
            print("  -", f)
        return 1
    pages_desc = ", ".join(f"{os.path.basename(p)}: {len(b)}" for p, b in pages)
    print(f"voice coverage OK — {len(kinds)} engine event kinds, {len(asked)} static clip keys "
          f"({pages_desc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
