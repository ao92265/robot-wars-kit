#!/usr/bin/env python3
"""Bundle the 3D arena into ONE self-contained arena.html — no CDN, no module
imports, no external assets — so it runs from file:// at an offline venue.

  python3 tournament/visual/build_arena.py [--match path/to/demo.jsonl]

Inlines (in this order, into the template's placeholder comments):
  <!--THREE-->  vendored three.min.js (global THREE)
  <!--MATCH-->  an optional default match, as window.__EMBEDDED_MATCH__ (auto-plays)
  <!--APP-->    the renderer (src/arena.app.js)
Writes tournament/visual/arena.html.
"""
import argparse
import base64
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.abspath(os.path.join(HERE, "..", ".."))
# Blender-built assets (see robot-models/build_robots.py + build_arenas.py)
MODELS_DEFAULT = os.path.join(GAME, "robot-models")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def logo_js():
    """Real Harris logos, inlined as data URIs so the offline page can draw them
    on the jumbotrons + header without fetching anything. White SVG for dark UI
    chrome; full-colour PNG for the arena billboards."""
    out = ""
    p = os.path.join(HERE, "src", "assets", "harris-logo-white.svg")
    if os.path.exists(p):
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        out += 'window.__HARRIS_LOGO__ = "data:image/svg+xml;base64,' + b64 + '";\n'
    p = os.path.join(HERE, "src", "assets", "harris-logo-color.png")
    if os.path.exists(p):
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        out += 'window.__HARRIS_LOGO_COLOR__ = "data:image/png;base64,' + b64 + '";\n'
    return out


def _lean_filter(match_txt):
    """What does THIS match actually use? Returns (robot names, spinner shapes,
    arena key) so --lean can drop everything else from the payload."""
    frame0 = json.loads(match_txt.split("\n", 1)[0])
    size_by_r = {12: "small", 16: "medium", 22: "large"}
    builds, shapes = set(), set()
    for r in frame0["robots"]:
        size = size_by_r.get(int(round(r.get("r", 16))), "medium")
        builds.add(f"robot_{size}_{r.get('gun', 'laser')}_{r.get('eng', 'standard')}")
        shapes.add(r.get("shape", "tank"))
    st = frame0.get("status", {})
    fp = (st.get("w"), st.get("h"),
          tuple(sorted(tuple(round(v) for v in wall) for wall in st.get("walls", []))))
    return builds, shapes, fp


def models_js(models_dir, lean_match=None):
    """Inline the Blender GLB assets: 36 robots + one arena per map preset,
    each arena tagged with its exact sim wall rects (from engine/maps.py) so
    the renderer can fingerprint-match a recorded layout to a model.
    lean_match: a match .jsonl text — embed ONLY the builds/arena it uses
    (other matches dropped onto the page fall back to procedural visuals)."""
    robots_dir = os.path.join(models_dir, "glb")
    arenas_dir = os.path.join(models_dir, "glb-arenas")
    if not os.path.isdir(robots_dir):
        return ""
    b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode("ascii")
    robots = {f[:-4]: b64(os.path.join(robots_dir, f))
              for f in sorted(os.listdir(robots_dir)) if f.endswith(".glb")}
    spinners_dir = os.path.join(models_dir, "glb-spinners")
    spinners = {}
    if os.path.isdir(spinners_dir):
        spinners = {f[8:-4]: b64(os.path.join(spinners_dir, f))   # spinner_<shape>.glb -> shape
                    for f in sorted(os.listdir(spinners_dir)) if f.endswith(".glb")}
    arenas = {}
    if os.path.isdir(arenas_dir):
        sys.path.insert(0, GAME)
        from engine import maps
        for name in maps.names():
            p = os.path.join(arenas_dir, f"arena_{name}.glb")
            if not os.path.exists(p):
                continue
            m = maps.get(name)
            arenas[name] = {"glb": b64(p), "w": m["w"], "h": m["h"], "walls": m["walls"]}
    if lean_match:
        builds, shapes, fp = _lean_filter(lean_match)
        robots = {k: v for k, v in robots.items() if k in builds}
        spinners = {k: v for k, v in spinners.items() if k in shapes}
        arenas = {k: v for k, v in arenas.items()
                  if (v["w"], v["h"], tuple(sorted(tuple(round(x) for x in wall)
                                                   for wall in v["walls"]))) == fp}
    payload = json.dumps({"robots": robots, "arenas": arenas, "spinners": spinners})
    gz = base64.b64encode(gzip.compress(payload.encode("utf-8"), 9)).decode("ascii")
    print(f"  models: {len(robots)} robots, {len(arenas)} arenas, {len(spinners)} spinners "
          f"({len(payload) / 1024 / 1024:.1f} MB -> {len(gz) / 1024 / 1024:.1f} MB gzipped)")
    return "<script>window.__RW_MODELS_GZ__ = \"" + gz + "\";</script>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default=os.path.join(HERE, "samples", "demo.jsonl"),
                    help="default match to embed (auto-plays on open); '' to embed none")
    ap.add_argument("--models", default=MODELS_DEFAULT,
                    help="robot-models dir with glb/ + glb-arenas/; '' to skip inlining")
    ap.add_argument("--out", default=os.path.join(HERE, "arena.html"))
    ap.add_argument("--lean", action="store_true",
                    help="embed ONLY the builds/arena the embedded match uses "
                         "(smallest possible share file; other dropped matches "
                         "fall back to procedural visuals)")
    args = ap.parse_args()

    tpl = read(os.path.join(HERE, "src", "arena.template.html"))
    three = read(os.path.join(HERE, "vendor", "three.min.js"))
    app = read(os.path.join(HERE, "src", "arena.app.js"))

    # no samples/demo.jsonl checked in — fall back to the repo-root match
    if args.match and not os.path.exists(args.match):
        root_match = os.path.join(GAME, "match.jsonl")
        if os.path.exists(root_match):
            args.match = root_match
    match_txt = read(args.match) if args.match and os.path.exists(args.match) else None

    three_tag = "<script>\n" + three + "\n</script>"
    gltf_path = os.path.join(HERE, "vendor", "gltfloader.global.js")
    if os.path.exists(gltf_path):
        three_tag += "\n<script>\n" + read(gltf_path) + "\n</script>"
    if args.models and os.path.isdir(args.models):
        three_tag += "\n" + models_js(args.models, lean_match=match_txt if args.lean else None)

    match_tag = ""
    if match_txt:
        gz = base64.b64encode(gzip.compress(match_txt.encode("utf-8"), 9)).decode("ascii")
        print(f"  match: {len(match_txt) / 1024 / 1024:.1f} MB -> {len(gz) / 1024 / 1024:.1f} MB gzipped")
        match_tag = "<script>window.__EMBEDDED_MATCH_GZ__ = \"" + gz + "\";</script>"

    # optional pre-baked announcer pack (P3); absent => browser-speech fallback
    voice_tag = ""
    voice_js = os.path.join(HERE, "src", "voice_clips.js")
    if os.path.exists(voice_js):
        voice_tag = "<script>\n" + read(voice_js) + "\n</script>"

    app_tag = "<script>\n" + logo_js() + app + "\n</script>"

    html = (tpl.replace("<!--THREE-->", three_tag)
               .replace("<!--MATCH-->", match_tag)
               .replace("<!--VOICE-->", voice_tag)
               .replace("<!--APP-->", app_tag))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    kb = len(html.encode("utf-8")) / 1024
    print(f"wrote {args.out}  ({kb:.0f} KB, self-contained)")
    if not match_tag:
        print("  (no embedded match — open then Load a .jsonl)")


if __name__ == "__main__":
    main()
