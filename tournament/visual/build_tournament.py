#!/usr/bin/env python3
"""Run a full 1v1 team bracket and bundle it into ONE self-contained
tournament.html — a World-Cup-style bracket + live leaderboard that plays each
match's 3D replay on click and fills in the winners as you go. No CDN, no module
imports, no external assets, so it runs from a laptop at an offline venue.

  python3 tournament/visual/build_tournament.py                 # built-in demo roster
  python3 tournament/visual/build_tournament.py --submissions submissions
  python3 tournament/visual/build_tournament.py --seed 7 --map colosseum

Inlines, in order, into the template placeholders:
  <!--THREE-->            vendored three.min.js (global THREE)
  <!--TOURNAMENT_DATA-->  window.__TOURNAMENT__ = {bracket topology + every match's frames}
  <!--ARENA_APP-->        the 3D renderer (src/arena.app.js), exposing window.RW
  <!--TOURNAMENT_APP-->   the bracket/leaderboard shell (src/tournament.app.js)
Writes tournament/visual/tournament.html.
"""
import argparse
import base64
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from engine import maps  # noqa: E402
from tournament.bracket import run_bracket  # noqa: E402
from tournament.ingest import ingest  # noqa: E402
from tournament.bracket import teams_from_accepted  # noqa: E402

# Stable per-team identity colours for the bracket/leaderboard (distinct from the
# per-SIDE red/blue the arena paints each match — like home/away kit).
TEAM_PALETTE = ["#ff4d4d", "#4d96ff", "#ffd93d", "#2ec4b6", "#ff9f43", "#b983ff",
                "#ff5d8f", "#6bcb77", "#f9f871", "#00c2ff", "#ff8a5c", "#a685e2"]


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def demo_teams():
    """A built-in 8-team roster so the page builds out of the box (no submissions
    needed) with a clean World-Cup structure (quarter-finals / semis / final).
    Pure 1v1: one robot per team — the event format, where each team is a group
    of people building a single bot. Real events pass --submissions instead."""
    from engine.dummies import chaser, sniper, bomber, trapper

    def team(name, mod):
        return {"name": name, "bots": [{"name": name, "bot": name,
                                        "decide": mod.decide, "loadout": {}}]}
    return [
        team("Vipers",    chaser),
        team("Sentinels", sniper),
        team("Reapers",   bomber),
        team("Wardens",   trapper),
        team("Falcons",   sniper),
        team("Titans",    chaser),
        team("Hydras",    trapper),
        team("Onyx",      bomber),
    ]


def round_name(round_idx, total_rounds):
    """World-Cup-style label: the LAST round is the Final, counting back."""
    from_final = total_rounds - round_idx        # 0 = final
    return {0: "Final", 1: "Semi-finals", 2: "Quarter-finals",
            3: "Round of 16", 4: "Round of 32"}.get(from_final, f"Round {round_idx}")


def build_payload(summary, seed, map_kw, recordings_dir):
    """Turn a bracket summary + its on-disk JSONLs into the embeddable payload."""
    # Stable colours by first-seen team order across the whole bracket.
    seen = []
    for r in summary["rounds"]:
        for g in r["games"]:
            for t in (g["team_a"], g["team_b"]):
                if t and t not in seen:
                    seen.append(t)
    teams = [{"name": t, "color": TEAM_PALETTE[i % len(TEAM_PALETTE)]}
             for i, t in enumerate(seen)]

    total_rounds = len(summary["rounds"])
    rounds = []
    for r in summary["rounds"]:
        rounds.append({"round": r["round"], "name": round_name(r["round"], total_rounds),
                       "games": r["games"]})

    # Load every played match's frames (keyed by label) for the arena replay.
    matches = {}
    for m in summary["matches"]:
        if not os.path.exists(m["jsonl"]):
            raise SystemExit(f"match recording missing for {m['label']}: {m['jsonl']} "
                             f"(bracket run may have crashed mid-way)")
        frames = [json.loads(line) for line in read(m["jsonl"]).splitlines() if line.strip()]
        matches[m["label"]] = {"team_a": m["team_a"], "team_b": m["team_b"],
                               "winner": m["winner"], "reason": m["reason"],
                               "ticks": m["ticks"], "frames": frames}
    return {"champion": summary["champion"], "seed": seed, "map": map_kw,
            "teams": teams, "rounds": rounds, "matches": matches}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submissions", default=None,
                    help="folder of team_*.py submissions; omit to use the built-in demo roster")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--map", default="colosseum", choices=maps.names())
    ap.add_argument("--out", default=os.path.join(HERE, "tournament.html"))
    args = ap.parse_args()

    if args.submissions:
        manifest = ingest(args.submissions)
        teams = teams_from_accepted(manifest["accepted"])
        print(f"ingested {args.submissions}: {len(teams)} teams "
              f"({manifest['n_accepted']} bots, {manifest['n_rejected']} rejected)")
    else:
        teams = demo_teams()
        print(f"using built-in demo roster: {len(teams)} teams")

    if len(teams) < 2:
        print("Need at least 2 teams to run a bracket.")
        return 1

    rec_dir = tempfile.mkdtemp(prefix="rw_tourney_")
    summary = run_bracket(teams, args.seed, rec_dir, auto=True)
    if not summary:
        return 1

    payload = build_payload(summary, args.seed, args.map, rec_dir)

    tpl = read(os.path.join(HERE, "src", "tournament.template.html"))
    three = read(os.path.join(HERE, "vendor", "three.min.js"))
    arena_app = read(os.path.join(HERE, "src", "arena.app.js"))
    tourney_app = read(os.path.join(HERE, "src", "tournament.app.js"))

    # real Harris logo (white SVG) as a data URI — drawn on jumbotrons + header offline
    logo_js = ""
    logo_p = os.path.join(HERE, "src", "assets", "harris-logo-white.svg")
    if os.path.exists(logo_p):
        with open(logo_p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        logo_js = 'window.__HARRIS_LOGO__ = "data:image/svg+xml;base64,' + b64 + '";\n'

    data_tag = "<script>window.__TOURNAMENT__ = " + json.dumps(payload) + ";</script>"
    html = (tpl
            .replace("<!--THREE-->", "<script>\n" + three + "\n</script>")
            .replace("<!--TOURNAMENT_DATA-->", data_tag)
            .replace("<!--ARENA_APP-->", "<script>\n" + logo_js + arena_app + "\n</script>")
            .replace("<!--TOURNAMENT_APP-->", "<script>\n" + tourney_app + "\n</script>"))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    n_matches = len(payload["matches"])
    total_frames = sum(len(m["frames"]) for m in payload["matches"].values())
    print(f"champion: {payload['champion']}")
    print(f"wrote {args.out}  ({len(html) // 1024} KB, {n_matches} matches, {total_frames} frames)")
    print("Serve it:  cd tournament/visual && python3 -m http.server 8731")
    print("Open:      http://localhost:8731/tournament.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
