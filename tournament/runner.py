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
from tournament.seeds import SCHEME, match_seed, bot_seed, shuffle_seed
from tournament.runstate import (machine_lock, TournamentLockHeld, new_run_id,
                                 load_run_log, append_run_log)
from tournament.recmeta import RESULTS_SCHEMA, recording_header, run_meta

GROUP_SIZE = 8
ADVANCE = 2


def run_match(bots, event_seed, label, out_dir, map_kw=None):
    """bots: list of {name, path, loadout}. Returns result with ranking (names) + jsonl path.
    Seeds are identity-derived (event_seed + label + names, seeds.py) and the
    bot list is name-sorted first, so staging/rename order can never change the
    match — same roster + same event seed = byte-identical recording.
    map_kw: a preset name from engine.maps — brings that map's walls, hazards,
    pickups, rolled weather and (on showcase maps) the house robot. None = the
    plain classic arena (unchanged legacy behaviour)."""
    bots = sorted(bots, key=lambda b: b["name"])
    seed = match_seed(event_seed, label, [b["name"] for b in bots])
    entries = [(b["name"], None, b["loadout"]) for b in bots]
    specs = [(i, b["path"], bot_seed(event_seed, label, b["name"]))
             for i, b in enumerate(bots)]
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
    with open(path, "w", newline="\n") as f:
        f.write(json.dumps(recording_header(seed, map_kw, label)) + "\n")
        for fr in result["frames"]:
            f.write(json.dumps(fr) + "\n")
    return {"label": label, "winner": result["winner_name"],
            "ranking": ranking_names, "standings": result["standings"],
            "reason": result["reason"], "seed": seed, "seed_scheme": SCHEME,
            "substitutions": {bots[rid]["name"]: s for rid, s in pool.stats().items()},
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


def run_round(bots, event_seed, label, out_dir, map_kw=None, done=None, on_match=None):
    # Slug-sort before the seeded shuffle: heat composition depends on who
    # entered, never on manifest/filename order.
    bots = sorted(bots, key=lambda b: b["name"])
    rng = random.Random(shuffle_seed(event_seed, label))
    rng.shuffle(bots)
    groups = _chunk(bots, GROUP_SIZE)
    survivors, matches = [], []
    for i, grp in enumerate(groups):
        lbl = f"{label}_heat{i + 1}"
        if done and lbl in done:
            res = done[lbl]
            print(f"  {lbl}: resumed from run log (winner {res['winner']})")
        else:
            res = run_match(grp, event_seed, lbl, out_dir, map_kw=map_kw)
            if on_match:
                on_match(res)
        matches.append(res)
        by_name = {b["name"]: b for b in grp}
        survivors += [by_name[n] for n in res["ranking"][:ADVANCE] if n in by_name]
    return survivors, matches


def run_tournament(accepted, seed, out_dir, auto=False, map_kw=None, resume=False):
    os.makedirs(out_dir, exist_ok=True)
    with machine_lock():
        return _run_tournament_locked(accepted, seed, out_dir, auto, map_kw, resume)


def _run_tournament_locked(accepted, seed, out_dir, auto, map_kw, resume):
    done = load_run_log(out_dir) if resume else {}
    if done:
        print(f"Resuming: {len(done)} completed match(es) found in the run log.")

    def on_match(rec):
        append_run_log(out_dir, rec)

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
        remaining, matches = run_round(remaining, seed, f"R{rnd}", out_dir, map_kw=map_kw,
                                       done=done, on_match=on_match)
        for m in matches:
            print(f"  {m['label']}: winner {m['winner']}  (advance: {m['ranking'][:ADVANCE]})  -> {m['jsonl']}")
        all_matches += matches
        rnd += 1

    print(f"\n-- FINAL: {[b['name'] for b in remaining]} --")
    pause("Press Enter to run the FINAL... ")
    if done and "FINAL" in done:
        final = done["FINAL"]
        print(f"  FINAL: resumed from run log (winner {final['winner']})")
    else:
        final = run_match(remaining, seed, "FINAL", out_dir, map_kw=map_kw)
        on_match(final)
    all_matches.append(final)
    champion, tiebreak = decide_champion(final)
    final["champion"] = champion
    final["tiebreak"] = tiebreak
    detail = final["reason"] + (f" -> tiebreak: {tiebreak}" if tiebreak else "")
    print(f"\n*** CHAMPION: {champion} ***  ({detail}, {final['ticks']} ticks)")
    print(f"    final placings: {final['ranking']}")
    print(f"    replay: {final['jsonl']}")

    summary = {"schema_version": RESULTS_SCHEMA, "run_meta": run_meta(),
               "champion": champion, "tiebreak": tiebreak, "n_entries": len(accepted),
               "event_seed": seed, "seed_scheme": SCHEME,
               "matches": all_matches}
    with open(os.path.join(out_dir, "tournament_results.json"), "w", newline="\n") as f:
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
    resume_dir = _flag_value("--resume")
    folder = args[0] if args else os.path.join(ROOT, "submissions")
    if resume_dir:
        out_dir = resume_dir
        if not os.path.exists(os.path.join(out_dir, "run_log.jsonl")):
            print(f"--resume: no run log at {out_dir} — nothing to resume.")
            return 2
        print(f"Resuming run in {out_dir} (same roster + seed required for identical results).")
    else:
        # unique run-id dir: recovery/reruns never overwrite prior artifacts
        out_dir = os.path.join(folder, "recordings", new_run_id(seed))

    print(f"Ingesting {folder} ...")
    manifest = ingest(folder)
    print(f"  accepted {manifest['n_accepted']}, rejected {manifest['n_rejected']}")
    for r in manifest["rejected"]:
        print(f"    rejected {r['name']}: {r['reason']}")
    if manifest["n_accepted"] < 2:
        print("Need at least 2 accepted bots to run a tournament.")
        return 1
    print(f"map: {map_kw}")
    try:
        run_tournament(manifest["accepted"], seed, out_dir, auto=auto, map_kw=map_kw,
                       resume=bool(resume_dir))
    except TournamentLockHeld as e:
        print(f"REFUSED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
