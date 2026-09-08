"""Register teams for an event and issue per-team submission tokens.

Tokens are printed ONCE, here — only sha256 hashes land in roster.json.
Hand each team its token privately; they submit with
  export ROBOT_WARS_TOKEN=<token>   (CLI)  or paste it in the browser form.

  python3 -m tournament.roster <roster.json|drop-folder> "Team A" "Team B" ...

Re-registering an existing team rotates its token (old one stops working).
"""

import hashlib
import json
import os
import secrets
import sys

from tournament.submit_server import _slug


def create(roster_path, names):
    """Add/rotate teams in roster.json. Returns [{team, slug, token}] — the only
    place raw tokens ever exist; the file stores hashes."""
    data = {"schema": 1, "teams": {}}
    if os.path.exists(roster_path):
        with open(roster_path) as f:
            data = json.load(f)
        data.setdefault("teams", {})
    issued = []
    for team in names:
        team = team.strip()
        if not team:
            continue
        slug = _slug(team)
        token = secrets.token_urlsafe(16)
        data["teams"][slug] = {"team": team,
                               "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
        issued.append({"team": team, "slug": slug, "token": token})
    d = os.path.dirname(roster_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(roster_path, "w", newline="\n") as f:
        json.dump(data, f, indent=2)
    return issued


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__.strip())
        return 1
    target, names = args[0], args[1:]
    roster_path = target if target.endswith(".json") else os.path.join(target, "roster.json")
    issued = create(roster_path, names)
    print(f"roster -> {os.path.abspath(roster_path)}   ({len(issued)} team(s) registered)")
    print("\nTokens (shown ONCE — hand each team theirs privately):")
    for e in issued:
        print(f"  {e['team']:<30} {e['slug']:<30} {e['token']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
