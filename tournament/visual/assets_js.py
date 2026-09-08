"""Brand art, inlined as data URIs so the offline page draws it without fetching.

Shared by build_arena.py and build_tournament.py. It lives in its own module
because it used to be copy-pasted into both, and the copies drifted: the
tournament page — the one the show is actually driven from — only ever emitted
the white SVG, so every ad board on it rendered as a blank white plate.
"""
import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "src", "assets")


def _data_uri(path, mime):
    with open(path, "rb") as f:
        return "data:" + mime + ";base64," + base64.b64encode(f.read()).decode("ascii")


def logo_js():
    """White SVG for dark UI chrome, full-colour PNG for the arena billboards,
    and the per-unit marks keyed by slug for the pitch-side hoardings.

    All three are optional. Ship none and the viewer draws plain plates."""
    out = ""
    p = os.path.join(ASSETS, "brand-logo-white.svg")
    if os.path.exists(p):
        out += 'window.__BRAND_LOGO__ = "' + _data_uri(p, "image/svg+xml") + '";\n'
    p = os.path.join(ASSETS, "brand-logo-color.png")
    if os.path.exists(p):
        out += 'window.__BRAND_LOGO_COLOR__ = "' + _data_uri(p, "image/png") + '";\n'

    bu_dir = os.path.join(ASSETS, "bu-logos")
    if os.path.isdir(bu_dir):
        # One assignment per line rather than a JSON blob: these are ~300KB of
        # base64 between them and a single line that long is unreadable in the
        # generated page and unreviewable in a diff.
        out += "window.__BU_LOGOS__ = {};\n"
        for name in sorted(os.listdir(bu_dir)):
            if not name.endswith(".png"):
                continue
            slug = name[:-4]
            uri = _data_uri(os.path.join(bu_dir, name), "image/png")
            out += 'window.__BU_LOGOS__["' + slug + '"] = "' + uri + '";\n'
    return out
