"""Ingest + validate submitted bots before the tournament.

Scans a folder of team_*.py, checks each exposes a legal LOADOUT + decide(),
and smoke-tests it in isolation (a few ticks vs a dummy) so a bot that crashes
or hangs on import/run is flagged here — never on stage. Writes manifest.json.
"""

import glob
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine.loadout import validate_loadout
from engine.dummies import sitting_duck
from tournament.isolation import IsolationPool


def _load(path):
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
        try:
            mod = _load(path)
        except Exception as e:
            rejected.append({"name": name, "reason": f"import failed: {e}"})
            continue
        if not hasattr(mod, "LOADOUT") or not hasattr(mod, "decide"):
            rejected.append({"name": name, "reason": "missing LOADOUT or decide()"})
            continue
        ok, msg = validate_loadout(mod.LOADOUT)
        if not ok:
            rejected.append({"name": name, "reason": msg})
            continue
        sok, sreason = _smoke(path, mod.LOADOUT)
        if not sok:
            rejected.append({"name": name, "reason": sreason})
            continue
        team, bot = _team_and_bot(name)
        accepted.append({"name": name, "path": path, "loadout": mod.LOADOUT,
                         "team": team, "bot": bot})

    manifest = {"accepted": accepted, "rejected": rejected,
                "n_accepted": len(accepted), "n_rejected": len(rejected)}
    with open(os.path.join(folder, "manifest.json"), "w") as f:
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
