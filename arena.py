#!/usr/bin/env python3
"""Robot Wars — participant entrypoint. Pure stdlib; no pip needed.

  python3 arena.py                      # your bot vs all dummies, animated
  python3 arena.py --vs sniper          # vs one dummy (duck|chaser|sniper|bomber|trapper)
  python3 arena.py --fast               # no animation, just the result
  python3 arena.py --vs sniper --best-of 20   # win-rate over N matches
  python3 arena.py --check              # is my LOADOUT legal?
  python3 arena.py --submit "Team Name" # hand in my_bot.py
  python3 arena.py --replay match.jsonl # replay a recorded match (ASCII)
  python3 arena.py --vs-file rival.py   # table playoff: fight a teammate's bot file
  python3 arena.py --exhibition chaser,sniper   # dummy-vs-dummy showcase (no player bot)
"""
import argparse
import importlib.util
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine.loadout import validate_loadout, BUDGET
from engine import render
from engine import maps

DUMMIES = {"duck": "sitting_duck", "sitting_duck": "sitting_duck",
           "chaser": "chaser", "sniper": "sniper",
           "bomber": "bomber", "trapper": "trapper"}


def load_module(path, modname="loaded"):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_my_bot():
    return load_module(os.path.join(ROOT, "my_bot.py"), "my_bot")


def load_dummy(key):
    # import as a real package member — the dummies use relative imports
    # (from . import nav_turn), which break under load-by-path
    return importlib.import_module(f"engine.dummies.{DUMMIES[key]}")


def entries_for(my, opponents):
    e = [("You", my.decide, my.LOADOUT, getattr(my, "APPEARANCE", None))]
    for key in opponents:
        d = load_dummy(key)
        e.append((key.capitalize(), d.decide, d.LOADOUT, getattr(d, "APPEARANCE", None)))
    return e


def exhibition_entries(keys):
    """Dummy-vs-dummy showcase lineup (no player bot). Duplicate picks get
    numbered names so 'chaser,chaser' is a valid mirror match."""
    counts = {}
    entries = []
    for key in keys:
        d = load_dummy(key)
        counts[key] = counts.get(key, 0) + 1
        name = key.capitalize() + (f" {counts[key]}" if keys.count(key) > 1 else "")
        entries.append((name, d.decide, d.LOADOUT, getattr(d, "APPEARANCE", None)))
    return entries


def animate(result):
    f0 = result["frames"][0]
    arena_w = f0["status"].get("w", 1280)
    arena_h = f0["status"].get("h", 768)
    names = [r["name"] for r in f0["robots"]]
    for fr in result["frames"]:
        sys.stdout.write("\033[H\033[J")  # clear screen
        sys.stdout.write(render.render_frame(fr, arena_w, arena_h, names) + "\n")
        sys.stdout.flush()
        time.sleep(0.04)
    print(f"\nWINNER: {result['winner_name']}  ({result['reason']}, {result['ticks']} ticks)")


def cmd_check(my):
    ok, msg = validate_loadout(my.LOADOUT)
    print(f"LOADOUT = {my.LOADOUT}")
    print(("READY TO SUBMIT — " if ok else "NOT LEGAL — ") + msg)
    return 0 if ok else 1


def cmd_submit(my, team):
    ok, msg = validate_loadout(my.LOADOUT)
    if not ok:
        print(f"Cannot submit — {msg}")
        return 1
    with open(os.path.join(ROOT, "my_bot.py")) as f:
        src = f.read()
    # Distributed submission: POST to the organiser's submit server if configured.
    url = os.environ.get("ROBOT_WARS_SUBMIT_URL")
    if url:
        import urllib.request
        req = urllib.request.Request(
            url.rstrip("/") + "/submit", data=src.encode(),
            headers={"X-Team-Name": team, "Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                print(f"Submitted '{team}' to {url} — {r.read().decode().strip()}   ({msg})")
            return 0
        except Exception as e:
            print(f"Could not reach submit server at {url}: {e}\nFalling back to local drop.")
    # Local fallback (shared drive via ROBOT_WARS_DROP, or this machine).
    drop = os.environ.get("ROBOT_WARS_DROP", os.path.join(ROOT, "submissions"))
    os.makedirs(drop, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in team).strip("_").lower() or "team"
    if not slug.startswith("team"):
        slug = "team_" + slug
    dest = os.path.join(drop, f"{slug}.py")
    with open(dest, "w") as f:
        f.write(f"# Submitted by: {team}\n" + src)
    print(f"Submitted '{team}' -> {dest}   ({msg})")
    return 0


def cmd_replay(path):
    with open(path) as f:
        frames = [json.loads(line) for line in f if line.strip()]
    aw = frames[0]["status"].get("w", 1280)
    ah = frames[0]["status"].get("h", 768)
    names = [r["name"] for r in frames[0]["robots"]]
    for fr in frames:
        sys.stdout.write("\033[H\033[J")
        sys.stdout.write(render.render_frame(fr, aw, ah, names) + "\n")
        sys.stdout.flush()
        time.sleep(0.04)
    return 0


def cmd_best_of(entries, label, n, map_kw, base_seed):
    wins = 0
    for s in range(base_seed, base_seed + n):
        res = Game(entries, seed=s, **map_kw).run(collect_frames=False)
        if res["winner_name"] == "You":
            wins += 1
    rate = 100 * wins / n
    print(f"You won {wins}/{n} matches ({rate:.0f}%) vs {label}"
          f"  [seeds {base_seed}-{base_seed + n - 1}]")
    if rate < 50:
        print("  < 50% — tweak your LOADOUT or strategy and try again.")
    else:
        print("  >= 50% — you're competitive. Push for more.")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vs", help="one dummy: duck|chaser|sniper|bomber|trapper")
    p.add_argument("--vs-file", metavar="FILE",
                   help="fight another player's bot file 1v1 (table playoff)")
    p.add_argument("--fast", action="store_true", help="no animation, just the result")
    p.add_argument("--best-of", type=int, default=0, metavar="N")
    p.add_argument("--check", action="store_true")
    p.add_argument("--submit", metavar="TEAM")
    p.add_argument("--replay", metavar="FILE")
    p.add_argument("--record", metavar="FILE", help="write match frames to a .jsonl")
    p.add_argument("--exhibition", metavar="BOTS",
                   help="dummy-vs-dummy showcase (no player bot): comma list, "
                        "e.g. chaser,sniper — honours --map/--seed/--record/--fast")
    p.add_argument("--seed", type=int, default=None,
                   help="fix the RNG (same seed = the exact same fight); default: random each run")
    p.add_argument("--map", default="classic", help="arena: " + "|".join(maps.names()))
    p.add_argument("--weather", default="clear",
                   help="clear (default) | fog | wind | roll (random from seed)")
    args = p.parse_args()

    if args.replay:
        return cmd_replay(args.replay)

    if args.map not in maps.names():
        print(f"unknown map '{args.map}'. Choose from: {', '.join(maps.names())}")
        return 2
    _m = maps.get(args.map)
    map_kw = {"width": _m["w"], "height": _m["h"], "walls": _m["walls"],
              "hazards": _m["hazards"], "pickups": _m["pickups"], "weather": args.weather,
              "house": _m.get("house", False)}

    if args.exhibition:
        keys = [k.strip().lower() for k in args.exhibition.split(",") if k.strip()]
        bad = [k for k in keys if k not in DUMMIES]
        if bad or len(keys) < 2:
            print(f"--exhibition needs 2+ of: {', '.join(sorted(set(DUMMIES)))}"
                  + (f"  (unknown: {', '.join(bad)})" if bad else ""))
            return 2
        seed = args.seed if args.seed is not None else random.randrange(1, 1_000_000)
        result = Game(exhibition_entries(keys), seed=seed, **map_kw).run()
        if args.record:
            with open(args.record, "w") as f:
                for fr in result["frames"]:
                    f.write(json.dumps(fr) + "\n")
            print(f"recorded {len(result['frames'])} frames -> {args.record}")
        if args.fast:
            print(f"WINNER: {result['winner_name']}  ({result['reason']}, {result['ticks']} ticks)")
        else:
            animate(result)
        replay_cmd = f"python3 arena.py --exhibition {args.exhibition}"
        if args.map != "classic":
            replay_cmd += f" --map {args.map}"
        if args.weather != "clear":
            replay_cmd += f" --weather {args.weather}"
        print(f"seed {seed} — replay this exact fight: {replay_cmd} --seed {seed}")
        return 0

    my = load_my_bot()
    if args.check:
        return cmd_check(my)
    if args.submit:
        return cmd_submit(my, args.submit)

    if args.vs_file:
        # Table playoff: your my_bot.py vs a teammate's bot file, 1v1.
        if not os.path.exists(args.vs_file):
            print(f"no such file: {args.vs_file}")
            return 2
        if not args.vs_file.endswith(".py"):
            print(f"'{args.vs_file}' isn't a .py file — point me at a my_bot.py-style bot")
            return 2
        try:
            rival = load_module(args.vs_file, "rival_bot")
        except Exception as e:
            print(f"rival bot failed to load: {e}")
            return 2
        if not hasattr(rival, "decide") or not hasattr(rival, "LOADOUT"):
            print("rival file needs LOADOUT and decide(view) — is it a my_bot.py?")
            return 2
        ok, msg = validate_loadout(rival.LOADOUT)
        if not ok:
            print(f"rival LOADOUT NOT LEGAL — {msg}")
            return 2
        rname = os.path.splitext(os.path.basename(args.vs_file))[0]
        entries = [("You", my.decide, my.LOADOUT, getattr(my, "APPEARANCE", None)),
                   (rname, rival.decide, rival.LOADOUT, getattr(rival, "APPEARANCE", None))]
        label = rname
    else:
        opponents = [args.vs] if args.vs else ["duck", "chaser", "sniper", "bomber", "trapper"]
        for o in opponents:
            if o not in DUMMIES:
                print(f"unknown opponent '{o}'. Choose from: duck, chaser, sniper, bomber, trapper")
                return 2
        entries = entries_for(my, opponents)
        label = ", ".join(opponents)

    # Fresh fight every run unless --seed pins it. Same seed = the exact same
    # fight, so any bout can still be replayed for a rematch or a dispute.
    seed = args.seed if args.seed is not None else random.randrange(1, 1_000_000)

    if args.best_of:
        return cmd_best_of(entries, label, args.best_of, map_kw, seed)

    result = Game(entries, seed=seed, **map_kw).run()
    if args.record:
        with open(args.record, "w") as f:
            for fr in result["frames"]:
                f.write(json.dumps(fr) + "\n")
        print(f"recorded {len(result['frames'])} frames -> {args.record}")
    if args.fast:
        print(f"WINNER: {result['winner_name']}  ({result['reason']}, {result['ticks']} ticks)")
    else:
        animate(result)
    replay_cmd = "python3 arena.py"
    if args.vs_file:
        replay_cmd += f" --vs-file {args.vs_file}"
    elif args.vs:
        replay_cmd += f" --vs {args.vs}"
    if args.map != "classic":
        replay_cmd += f" --map {args.map}"
    if args.weather != "clear":
        replay_cmd += f" --weather {args.weather}"
    print(f"seed {seed} — replay this exact fight: {replay_cmd} --seed {seed}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nFight abandoned — the robots hold no grudge. Run python3 arena.py to go again.")
        sys.exit(130)
