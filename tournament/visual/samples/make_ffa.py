#!/usr/bin/env python3
"""Regenerate samples/ffa.jsonl — a crowded free-for-all for viewer QA.

Nothing embeds this. It exists because the camera, the name tags and the
roster rail all behave completely differently with two dozen machines on the
floor than with the two in demo.jsonl, and the event's own headline format is
the free-for-all. Load it over a running arena.html:

  python3 tournament/visual/samples/make_ffa.py
  cp tournament/visual/samples/ffa.jsonl tournament/visual/    # served dir
  # then in the page: LOAD MATCH, or RW.loadText(await (await fetch('/ffa.jsonl')).text())

24 rather than the event's 41 — enough to reproduce every crowding problem
while staying quick to generate and quick to eyeball.
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine import maps
from engine.dummies import chaser, bomber

N = 24
SEED = 7
MAP = "colosseum"
# time_cap high enough that the field thins out on camera rather than the match
# ending while it is still a scrum
TIME_CAP = 600

PALETTE = ["#ff5d3a", "#4d6bff", "#3ad17c", "#ffd93d", "#c86bff", "#37d5e0",
           "#ff7ab8", "#9aa7b8"]
SHAPES = ["speeder", "tank", "orb", "spike"]
LOADOUTS = [
    {"hp": 4, "speed": 4, "damage": 3, "range": 0, "special": 1,
     "size": "small", "gun": "shotgun", "engine": "sprint"},
    {"hp": 3, "speed": 4, "damage": 1, "range": 3, "special": 1,
     "size": "large", "gun": "cannon", "engine": "tank"},
]

# entry tuple: (name, decide, loadout, appearance, team) — one team per robot,
# which is the free-for-all shape
ENTRIES = [
    (f"BU{i:02d}:Bot{i:02d}",
     (chaser.decide, bomber.decide)[i % 2],
     LOADOUTS[i % 2],
     {"color": PALETTE[i % len(PALETTE)], "shape": SHAPES[i % len(SHAPES)],
      "accent": "#ffffff"},
     f"BU{i:02d}")
    for i in range(N)
]


def main():
    m = maps.get(MAP)
    res = Game(ENTRIES, seed=SEED, width=m["w"], height=m["h"], walls=m["walls"],
               hazards=m["hazards"], pickups=m["pickups"], weather="clear",
               house=m.get("house", False), time_cap=TIME_CAP).run()
    out = os.path.join(os.path.dirname(__file__), "ffa.jsonl")
    with open(out, "w") as f:
        for fr in res["frames"]:
            f.write(json.dumps(fr) + "\n")
    print(f"wrote {out}  ({len(res['frames'])} frames, winner team {res['winner_team']})")


if __name__ == "__main__":
    main()
