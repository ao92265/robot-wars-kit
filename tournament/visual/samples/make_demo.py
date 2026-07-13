#!/usr/bin/env python3
"""Regenerate samples/demo.jsonl — the default match baked into arena.html by
build_arena.py. (Recordings are .gitignored, so this reproduces it after clone.)

  python3 tournament/visual/samples/make_demo.py
  python3 tournament/visual/build_arena.py     # re-embeds it
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine import maps

# 1v1 SHOWCASE on the big "colosseum" map — the EVENT FORMAT (each team fields ONE
# robot). Two maximally-contrasting archetype builds so the default screen demos the
# customization layer: Crimson's shotgun/sprint/small rusher vs Azure's cannon/tank/
# large siege gun. Brains are the proven charge-and-finish dummies (the examples/
# brains are teaching material — too polite to end a showmatch); the LOADOUTS are
# what's on display. CLEAR weather so hazards read (bracket matches roll fog/wind).
from engine.dummies import chaser, bomber

RIOT = {"hp": 4, "speed": 4, "damage": 3, "range": 0, "special": 1,
        "size": "small", "gun": "shotgun", "engine": "sprint"}
BASTION = {"hp": 3, "speed": 4, "damage": 1, "range": 3, "special": 1,
           "size": "large", "gun": "cannon", "engine": "tank"}

# entry tuple: (name, decide, loadout, appearance, team)
ENTRIES = [
    ("Crimson:Riot", chaser.decide, RIOT,
     {"color": "#ff5d3a", "shape": "speeder", "accent": "#ffd93d"}, "Crimson"),
    ("Azure:Bastion", bomber.decide, BASTION,
     {"color": "#4d6bff", "shape": "tank", "accent": "#9fdcff"}, "Azure"),
]
SEED = 11      # scanned 1-30 (house robot off for the event): KO at 367 ticks, 12 rams + 2 flips
MAP = "colosseum"


def main():
    m = maps.get(MAP)
    res = Game(ENTRIES, seed=SEED, width=m["w"], height=m["h"], walls=m["walls"],
               hazards=m["hazards"], pickups=m["pickups"], weather="clear",
               house=m.get("house", False)).run()
    out = os.path.join(os.path.dirname(__file__), "demo.jsonl")
    with open(out, "w") as f:
        for fr in res["frames"]:
            f.write(json.dumps(fr) + "\n")
    print(f"wrote {out}  ({len(res['frames'])} frames, winner team {res['winner_team']})")


if __name__ == "__main__":
    main()
