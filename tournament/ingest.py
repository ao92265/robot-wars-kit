"""Ingest + validate submitted bots before the tournament.

Scans a folder of team_*.py, checks each exposes a legal LOADOUT + decide(),
and smoke-tests it in isolation (a few ticks vs a dummy) so a bot that crashes
or hangs on import/run is flagged here — never on stage. Writes manifest.json.

Submitted code NEVER executes in this (the organiser's) process: an AST import
allowlist screens each file before any execution, then LOADOUT/decide are
extracted inside a throwaway subprocess with a timeout, and the smoke test
already runs in isolation. The allowlist is an anti-footgun for accidents,
not a hostile-code security boundary (see isolation.py).
"""

import ast
import glob
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine.loadout import validate_loadout
from engine.dummies import sitting_duck
from tournament.isolation import IsolationPool

ALLOWED_IMPORTS = {
    "math", "cmath", "random", "itertools", "functools", "collections",
    "typing", "dataclasses", "enum", "heapq", "bisect", "copy", "statistics",
    "operator", "string", "abc", "array", "fractions", "decimal",
}

EXTRACT_TIMEOUT_S = 20  # module-level code must finish well inside this

# Runs inside a throwaway child interpreter: import the bot, report LOADOUT +
# decide() presence as JSON into argv[2]. Never trusted with parent state.
_EXTRACT_SRC = """
import importlib.util, json, sys

def emit(d):
    with open(sys.argv[2], "w", newline="\\n") as f:
        json.dump(d, f)

try:  # best-effort memory cap (POSIX only), mirrors isolation.py
    import resource
    lim = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
except Exception:
    pass
try:
    spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
except BaseException as e:
    emit({"ok": False, "reason": "import failed: %r" % (e,)})
    sys.exit(0)
loadout = getattr(m, "LOADOUT", None)
if loadout is None or not callable(getattr(m, "decide", None)):
    emit({"ok": False, "reason": "missing LOADOUT or decide()"})
    sys.exit(0)
try:
    emit({"ok": True, "loadout": loadout})
except (TypeError, ValueError):
    emit({"ok": False, "reason": "LOADOUT must be plain data (dict of numbers/strings)"})
"""


def _ast_check(path):
    """Pre-execution screen: only allowlisted imports. Returns (ok, reason)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return False, f"syntax error: {e}"
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    bad.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                bad.add("(relative import)")
            else:
                root = (node.module or "").split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    bad.add(root)
    if bad:
        return False, (f"disallowed import(s): {', '.join(sorted(bad))} — "
                       f"bots may only import: {', '.join(sorted(ALLOWED_IMPORTS))}")
    return True, "ok"


def _extract_loadout(path):
    """Import the bot in a throwaway subprocess and pull out its LOADOUT.
    Returns (loadout_or_None, reason)."""
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        try:
            # PYTHONDONTWRITEBYTECODE: don't drop __pycache__ into submission dirs
            subprocess.run([sys.executable, "-c", _EXTRACT_SRC, path, out],
                           timeout=EXTRACT_TIMEOUT_S,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return None, (f"import hung (>{EXTRACT_TIMEOUT_S}s) — module-level code "
                          f"must finish instantly")
        try:
            with open(out) as f:
                res = json.load(f)
        except (OSError, ValueError):
            return None, "import failed: interpreter died before reporting"
        if not res.get("ok"):
            return None, res.get("reason", "import failed")
        return res["loadout"], "ok"
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def _team_and_bot(name):
    """Map a submission name to (team, bot_label). `team_<Team>__<Bot>` groups
    several bots under one team; a plain `team_<Name>` is a one-bot team named
    after itself (back-compatible with single-bot entries)."""
    base = name[5:] if name.startswith("team_") else name
    if "__" in base:
        team, bot = base.split("__", 1)
        return team, bot
    return base, base


def _smoke(path, loadout):
    """Run ~30 isolated ticks of this bot vs a sitting duck. Returns (ok, reason)."""
    entries = [("Candidate", None, loadout),
               ("Duck", None, sitting_duck.LOADOUT)]
    duck_path = os.path.join(ROOT, "engine", "dummies", "sitting_duck.py")
    specs = [(0, path, 1), (1, duck_path, 2)]
    pool = IsolationPool(specs)
    try:
        g = Game(entries, seed=1, time_cap=30, decider=pool.decider)
        g.run(collect_frames=False)
        if pool.workers[0].dead:
            return False, "bot crashed or hung during smoke test"
        return True, "ok"
    except Exception as e:
        return False, f"engine error: {e}"
    finally:
        pool.close()


def ingest(folder):
    accepted, rejected = [], []
    paths = sorted(glob.glob(os.path.join(folder, "team_*.py")))
    for path in paths:
        name = os.path.basename(path)[:-3]
        aok, areason = _ast_check(path)
        if not aok:
            rejected.append({"name": name, "reason": areason})
            continue
        loadout, lreason = _extract_loadout(path)
        if loadout is None:
            rejected.append({"name": name, "reason": lreason})
            continue
        ok, msg = validate_loadout(loadout)
        if not ok:
            rejected.append({"name": name, "reason": msg})
            continue
        sok, sreason = _smoke(path, loadout)
        if not sok:
            rejected.append({"name": name, "reason": sreason})
            continue
        team, bot = _team_and_bot(name)
        accepted.append({"name": name, "path": path, "loadout": loadout,
                         "team": team, "bot": bot})

    from tournament.recmeta import RESULTS_SCHEMA, run_meta
    manifest = {"schema_version": RESULTS_SCHEMA, "run_meta": run_meta(),
                "accepted": accepted, "rejected": rejected,
                "n_accepted": len(accepted), "n_rejected": len(rejected)}
    with open(os.path.join(folder, "manifest.json"), "w", newline="\n") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "submissions")
    m = ingest(folder)
    print(f"ACCEPTED ({m['n_accepted']}):")
    for a in m["accepted"]:
        print(f"  {a['name']}  {a['loadout']}")
    print(f"REJECTED ({m['n_rejected']}):")
    for r in m["rejected"]:
        print(f"  {r['name']}: {r['reason']}")
    print(f"\nmanifest -> {os.path.join(folder, 'manifest.json')}")
