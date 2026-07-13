"""Tournament runner. Ingest -> validate -> run matches in isolation -> record
each to JSONL -> heats/bracket for scale -> champion. Replay the JSONL files on
the big screen with tournament/visual/arena.html.

  python3 -m tournament.runner [submissions_dir] [--auto] [--seed N]

--auto runs unattended (for the headless dry-run / scale rehearsal). Without it,
the operator presses Enter between matches to pace the show.
"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine import maps as engine_maps
from tournament.isolation import IsolationPool
from tournament.ingest import ingest

GROUP_SIZE = 8
ADVANCE = 2


def run_match(bots, seed, label, out_dir, map_kw=None):
    """bots: list of {name, path, loadout}. Returns result with ranking (names) + jsonl path.
    map_kw: a preset name from engine.maps — brings that map's walls, hazards,
    pickups, rolled weather and (on showcase maps) the house robot. None = the
    plain classic arena (unchanged legacy behaviour)."""
    entries = [(b["name"], None, b["loadout"]) for b in bots]
    specs = [(i, b["path"], seed * 100 + i) for i, b in enumerate(bots)]
    pool = IsolationPool(specs)
    gkw = {}
    if map_kw:
        m = engine_maps.get(map_kw)
        gkw = dict(width=m["w"], height=m["h"], walls=m["walls"], hazards=m["hazards"],
                   pickups=m["pickups"], weather="roll", house=m.get("house", False))
    try:
        game = Game(entries, seed=seed, decider=pool.decider, **gkw)
        result = game.run(collect_frames=True)
    finally:
        pool.close()
    ranking_names = [bots[rid]["name"] for rid in result["ranking"]]
    path = os.path.join(out_dir, f"{label}.jsonl")
    with open(path, "w") as f:
        for fr in result["frames"]:
            f.write(json.dumps(fr) + "\n")
    return {"label": label, "winner": result["winner_name"],
            "ranking": ranking_names, "standings": result["standings"],
            "reason": result["reason"],
            "ticks": result["ticks"], "jsonl": path}


def decide_champion(final):
    """A final must crown someone. If the match itself produced a winner, use it.
    On a draw (mutual KO), break the tie from standings — already ordered by
    survived-longest, then most HP, then most damage dealt.
    Returns (champion_name, tiebreak_note_or_None)."""
    if final["winner"]:
        return final["winner"], None
    standings = final.get("standings") or []
    if not standings:
        return None, None
    top = standings[0]
    if top["death_tick"] is not None:
        basis = f"survived to tick {top['death_tick']}, {top['damage_dealt']} damage dealt"
    else:
        basis = f"{top['hp']} HP left, {top['damage_dealt']} damage dealt"
    return top["name"], basis


def _chunk(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def run_round(bots, seed, label, out_dir, map_kw=None):
    rng = random.Random(seed)
    bots = bots[:]
    rng.shuffle(bots)
    groups = _chunk(bots, GROUP_SIZE)
    survivors, matches = [], []
    for i, grp in enumerate(groups):
        res = run_match(grp, seed + i + 1, f"{label}_heat{i + 1}", out_dir, map_kw=map_kw)
        matches.append(res)
        by_name = {b["name"]: b for b in grp}
        survivors += [by_name[n] for n in res["ranking"][:ADVANCE] if n in by_name]
    return survivors, matches


def run_tournament(accepted, seed, out_dir, auto=False, map_kw=None):
    os.makedirs(out_dir, exist_ok=True)
    all_matches = []
    remaining = accepted[:]
    rnd = 1

    def pause(msg):
        if not auto:
            try:
                input(msg)
            except EOFError:
                pass

    print(f"\n=== TOURNAMENT: {len(accepted)} entries ===")
    while len(remaining) > GROUP_SIZE:
        print(f"\n-- Round {rnd}: {len(remaining)} bots in {len(_chunk(remaining, GROUP_SIZE))} heats --")
        pause("Press Enter to run this round of heats... ")
        remaining, matches = run_round(remaining, seed + rnd * 1000, f"R{rnd}", out_dir, map_kw=map_kw)
        for m in matches:
            print(f"  {m['label']}: winner {m['winner']}  (advance: {m['ranking'][:ADVANCE]})  -> {m['jsonl']}")
        all_matches += matches
        rnd += 1

    print(f"\n-- FINAL: {[b['name'] for b in remaining]} --")
    pause("Press Enter to run the FINAL... ")
    final = run_match(remaining, seed + 99999, "FINAL", out_dir, map_kw=map_kw)
    all_matches.append(final)
    champion, tiebreak = decide_champion(final)
    final["champion"] = champion
    final["tiebreak"] = tiebreak
    detail = final["reason"] + (f" -> tiebreak: {tiebreak}" if tiebreak else "")
    print(f"\n*** CHAMPION: {champion} ***  ({detail}, {final['ticks']} ticks)")
    print(f"    final placings: {final['ranking']}")
    print(f"    replay: {final['jsonl']}")

    summary = {"champion": champion, "tiebreak": tiebreak, "n_entries": len(accepted),
               "matches": all_matches}
    with open(os.path.join(out_dir, "tournament_results.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nresults -> {os.path.join(out_dir, 'tournament_results.json')}")
    print("Replay any match: open tournament/visual/arena.html and load its .jsonl")
    return summary


def main():
    args = sys.argv[1:]
    auto = "--auto" in args
    args = [a for a in args if a != "--auto"]
    def _flag_value(flag):
        """Pop `--flag value` from args; None if absent. A flag with no value
        is a usage error, not a traceback."""
        if flag not in args:
            return None
        i = args.index(flag)
        if i + 1 >= len(args):
            print(f"{flag} needs a value")
            raise SystemExit(2)
        v = args[i + 1]; del args[i:i + 2]
        return v

    v = _flag_value("--seed")
    seed = int(v) if v is not None else 1
    # the show runs on the showcase map (hazards + pickups + house robot + rolled
    # weather) unless the operator picks another; --map classic = the plain pit
    map_kw = _flag_value("--map") or "colosseum"
    if map_kw not in engine_maps.names():
        print(f"unknown map '{map_kw}'. Choose from: {', '.join(engine_maps.names())}")
        return 2
    folder = args[0] if args else os.path.join(ROOT, "submissions")
    out_dir = os.path.join(folder, "recordings")

    print(f"Ingesting {folder} ...")
    manifest = ingest(folder)
    print(f"  accepted {manifest['n_accepted']}, rejected {manifest['n_rejected']}")
    for r in manifest["rejected"]:
        print(f"    rejected {r['name']}: {r['reason']}")
    if manifest["n_accepted"] < 2:
        print("Need at least 2 accepted bots to run a tournament.")
        return 1
    print(f"map: {map_kw}")
    run_tournament(manifest["accepted"], seed, out_dir, auto=auto, map_kw=map_kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
