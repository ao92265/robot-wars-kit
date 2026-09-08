"""1v1 team bracket — single-elimination knockout where each "entry" is a TEAM
of one or more robots. Two teams enter the arena, last team standing advances,
until one champion team remains. Records each match to JSONL for the big-screen
replay (tournament/visual/arena.html).

  python3 -m tournament.bracket [submissions_dir] [--auto] [--seed N]

Teams come from the filename convention parsed in ingest.py:
  team_<Team>__<Bot>.py   -> several bots grouped under <Team>
  team_<Name>.py          -> a one-bot team
"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.game import Game
from engine import maps
from tournament.isolation import IsolationPool
from tournament.ingest import ingest
from tournament.seeds import SCHEME, match_seed, bot_seed, shuffle_seed
from tournament.runstate import (machine_lock, TournamentLockHeld, new_run_id,
                                 load_run_log, append_run_log)
from tournament.recmeta import RESULTS_SCHEMA, recording_header, run_meta

# Default showcase map for bracket matches: hazards + pickups + rolled weather.
BRACKET_MAP = "colosseum"

# A small palette so the two sides read as distinct colours on screen. Per-bot
# shade variation keeps teammates grouped but individually visible.
TEAM_COLORS = ["#ff4d4d", "#4d96ff", "#ffd93d", "#2ec4b6", "#ff9f43", "#b983ff"]
SHAPES = ["tank", "spike", "speeder", "orb"]


def teams_from_accepted(accepted):
    """Group a flat ingest 'accepted' list into ordered teams (first-seen order).
    Returns [{"name": team, "bots": [bot_dict, ...]}, ...]."""
    order, by_team = [], {}
    for b in accepted:
        t = b.get("team", b["name"])
        if t not in by_team:
            by_team[t] = []
            order.append(t)
        by_team[t].append(b)
    return [{"name": t, "bots": by_team[t]} for t in order]


def _entries_and_specs(team_a, team_b, event_seed, label):
    """Flatten two teams into engine entries (with team labels + colours) and the
    isolation specs (robot_id -> path). Bots are label-sorted within each team
    and seeded by identity (seeds.py) so staging order can't change the match.
    In-process bots (a 'decide' callable, no 'path') run without a worker —
    handy for tests."""
    entries, specs = [], []
    rid = 0
    for side, team in enumerate((team_a, team_b)):
        color = TEAM_COLORS[side % len(TEAM_COLORS)]
        bots = sorted(team["bots"], key=lambda b: str(b.get("bot", b["name"])))
        for j, b in enumerate(bots):
            bot_label = f"{team['name']}:{b.get('bot', b['name'])}"
            appearance = {"color": color, "shape": SHAPES[j % len(SHAPES)]}
            entries.append((bot_label, b.get("decide"), b["loadout"], appearance, team["name"]))
            if b.get("path"):
                specs.append((rid, b["path"], bot_seed(event_seed, label, bot_label)))
            rid += 1
    return entries, specs


def run_team_match(team_a, team_b, event_seed, label, out_dir, map_kw=BRACKET_MAP):
    """Run team_a vs team_b in one arena. Returns the match record + JSONL path."""
    entries, specs = _entries_and_specs(team_a, team_b, event_seed, label)
    seed = match_seed(event_seed, label, [team_a["name"], team_b["name"]])
    m = maps.get(map_kw)
    pool = IsolationPool(specs) if specs else None
    try:
        game = Game(entries, seed=seed, decider=(pool.decider if pool else None),
                    width=m["w"], height=m["h"], walls=m["walls"],
                    hazards=m["hazards"], pickups=m["pickups"], weather="roll",
                    house=m.get("house", False))
        result = game.run(collect_frames=True)
    finally:
        if pool:
            pool.close()
    # winner_team is the team label; a mutual KO (None) falls back to the team
    # with more total surviving HP, then most damage dealt — a bracket must advance
    # exactly one side.
    winner = result["winner_team"] or _tiebreak_team(result["standings"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{label}.jsonl")
    with open(path, "w", newline="\n") as f:
        f.write(json.dumps(recording_header(seed, map_kw, label)) + "\n")
        for fr in result["frames"]:
            f.write(json.dumps(fr) + "\n")
    return {"label": label, "team_a": team_a["name"], "team_b": team_b["name"],
            "winner": winner, "reason": result["reason"], "ticks": result["ticks"],
            "seed": seed, "seed_scheme": SCHEME,
            "substitutions": ({entries[rid][0]: s for rid, s in pool.stats().items()}
                              if pool else {}),
            "standings": result["standings"], "jsonl": path}


def _tiebreak_team(standings):
    """Crown a side when the match itself produced no winner (mutual KO). Sum each
    team's surviving HP then damage dealt; pick the strongest. Deterministic."""
    agg = {}
    for s in standings:
        a = agg.setdefault(s["team"], [0, 0])
        a[0] += s["hp"]
        a[1] += s["damage_dealt"]
    if not agg:
        return None
    return max(agg, key=lambda t: (agg[t][0], agg[t][1]))


def _seed_bracket(teams, event_seed):
    """Name-sort, deterministic identity-seeded shuffle, then pad to the next
    power of two with byes (None). Input order never affects the draw."""
    rng = random.Random(shuffle_seed(event_seed, "bracket"))
    order = sorted(teams, key=lambda t: t["name"])
    rng.shuffle(order)
    size = 1
    while size < len(order):
        size *= 2
    order += [None] * (size - len(order))
    return order


def run_bracket(teams, seed, out_dir, auto=False, resume=False):
    with machine_lock():
        return _run_bracket_locked(teams, seed, out_dir, auto, resume)


def _run_bracket_locked(teams, seed, out_dir, auto, resume):
    if len(teams) < 2:
        print("Need at least 2 teams to run a bracket.")
        return None
    os.makedirs(out_dir, exist_ok=True)
    done = load_run_log(out_dir) if resume else {}
    if done:
        print(f"Resuming: {len(done)} completed match(es) found in the run log.")
    slots = _seed_bracket(teams, seed)
    all_matches = []
    rounds = []          # round-by-round topology (incl. byes) for the bracket tree
    rnd = 1

    def pause(msg):
        if not auto:
            try:
                input(msg)
            except EOFError:
                pass

    print(f"\n=== 1v1 TEAM BRACKET: {len(teams)} teams ===")
    while len(slots) > 1:
        # Byes can leave a round with an odd number of survivors (e.g. 5 teams ->
        # 3 in round 2). Pad to even so the i/i+1 pairing never runs off the end
        # and the odd team out gets a clean bye.
        if len(slots) % 2:
            slots.append(None)
        names = [s["name"] if s else "(bye)" for s in slots]
        print(f"\n-- Round {rnd}: {names} --")
        nxt = []
        round_games = []      # this round's slots as the tree renders them
        for i in range(0, len(slots), 2):
            a, b = slots[i], slots[i + 1]
            if a is None and b is None:
                continue
            if a is None or b is None:        # bye: the present team advances free
                adv = a or b
                print(f"  {adv['name']} advances on a bye")
                nxt.append(adv)
                round_games.append({"label": f"R{rnd}_M{i // 2 + 1}", "team_a": adv["name"],
                                    "team_b": None, "winner": adv["name"], "bye": True})
                continue
            lbl = f"R{rnd}_M{i // 2 + 1}"
            if done and lbl in done:
                m = done[lbl]
                print(f"  {lbl}: resumed from run log (winner {m['winner']})")
            else:
                pause(f"Press Enter to run {a['name']} vs {b['name']}... ")
                m = run_team_match(a, b, seed, lbl, out_dir)
                append_run_log(out_dir, m)
            all_matches.append(m)
            detail = m["reason"] + (" (tiebreak)" if m["reason"] == "mutual KO" else "")
            print(f"  {a['name']} vs {b['name']} -> {m['winner']}  ({detail}, {m['ticks']} ticks)  -> {m['jsonl']}")
            winner_team = a if a["name"] == m["winner"] else b
            nxt.append(winner_team)
            round_games.append({"label": m["label"], "team_a": m["team_a"], "team_b": m["team_b"],
                                "winner": m["winner"], "reason": m["reason"], "ticks": m["ticks"],
                                "bye": False})
        rounds.append({"round": rnd, "games": round_games})
        slots = nxt
        rnd += 1

    champion = slots[0]["name"] if slots and slots[0] else None
    print(f"\n*** CHAMPION TEAM: {champion} ***")
    summary = {"schema_version": RESULTS_SCHEMA, "run_meta": run_meta(),
               "champion": champion, "n_teams": len(teams), "format": "1v1 team bracket",
               "event_seed": seed, "seed_scheme": SCHEME,
               "rounds": rounds, "matches": all_matches}
    with open(os.path.join(out_dir, "bracket_results.json"), "w", newline="\n") as f:
        json.dump(summary, f, indent=2)
    print(f"results -> {os.path.join(out_dir, 'bracket_results.json')}")
    print("Replay any match: open tournament/visual/arena.html and load its .jsonl")
    return summary


def main():
    args = sys.argv[1:]
    auto = "--auto" in args
    args = [a for a in args if a != "--auto"]
    seed = 1
    if "--seed" in args:
        i = args.index("--seed")
        seed = int(args[i + 1]); del args[i:i + 2]
    resume_dir = None
    if "--resume" in args:
        i = args.index("--resume")
        if i + 1 >= len(args):
            print("--resume needs the run directory to resume")
            return 2
        resume_dir = args[i + 1]; del args[i:i + 2]
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
    teams = teams_from_accepted(manifest["accepted"])
    if len(teams) < 2:
        print("Need at least 2 teams to run a bracket.")
        return 1
    try:
        run_bracket(teams, seed, out_dir, auto=auto, resume=bool(resume_dir))
    except TournamentLockHeld as e:
        print(f"REFUSED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
