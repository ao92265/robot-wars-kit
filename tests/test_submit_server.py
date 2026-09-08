"""Submit-server hardening tests: auth, append-only journal, receipts, limits.
Run from repo root:  python3 tests/test_submit_server.py   (pure stdlib, no pytest needed)."""

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.submit_server import SubmitServer, _slug
from tournament import roster as roster_mod

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def start_server(drop, roster_path=None, open_mode=False, rate_interval=0.0):
    srv = SubmitServer(("127.0.0.1", 0), drop=drop, roster_path=roster_path,
                       open_mode=open_mode, rate_interval=rate_interval)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def post(url, code, team=None, token=None, form=False):
    """POST a submission; returns (status, body_str)."""
    if form:
        from urllib.parse import urlencode
        fields = {"team": team or "", "code": code}
        if token:
            fields["token"] = token
        data = urlencode(fields).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = code.encode()
        headers = {"X-Team-Name": team, "Content-Type": "text/plain"}
        if token:
            headers["X-Team-Token"] = token
    req = urllib.request.Request(url + "/submit", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def read_json(path):
    with open(path) as f:
        return json.load(f)


def journal_files(drop, slug):
    d = os.path.join(drop, slug)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".py"))


def audit_lines(drop):
    p = os.path.join(drop, "audit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    tmp = tempfile.mkdtemp(prefix="rw_submit_test_")
    try:
        run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all submit-server tests passed")
    return 0


def run_all(tmp):
    bot = "LOADOUT = {}\n\ndef decide(view):\n    return {}\n"
    bot2 = bot + "# v2\n"

    # ---- slug rules ----
    check("slug collapses underscore runs (no '__' ingest merge)",
          _slug("Rock & Roll") == "team_rock_roll")
    check("slug never emits double underscore for messy names",
          "__" not in _slug("a & b & c!!") and "__" not in _slug("we__try__hard"))
    check("slug keeps team prefix", _slug("Team Rocket") == "team_rocket")
    check("slug survives traversal attempt", _slug("../../evil") == "team_evil")

    # ---- roster creation ----
    drop = os.path.join(tmp, "toks")
    os.makedirs(drop)
    roster_path = os.path.join(drop, "roster.json")
    issued = roster_mod.create(roster_path, ["Team Rocket", "Duck Norris"])
    check("roster issues one token per team", len(issued) == 2)
    rj = read_json(roster_path)
    slugs = {e["slug"] for e in issued}
    check("roster keys are slugs", slugs == set(rj["teams"].keys()))
    check("roster stores hashes, never raw tokens",
          all(e["token"] not in json.dumps(rj) for e in issued))
    tok = {e["slug"]: e["token"] for e in issued}

    # ---- token mode ----
    srv, url = start_server(drop, roster_path=roster_path)
    try:
        st, body = post(url, bot, team="Team Rocket")
        check("no token -> 401", st == 401)
        check("401 leaves no file", not os.path.exists(os.path.join(drop, "team_rocket.py")))

        st, body = post(url, bot, team="Team Rocket", token=tok["team_rocket"])
        check("valid token -> 200", st == 200)
        rec = json.loads(body)
        sha = hashlib.sha256(bot.encode()).hexdigest()
        check("receipt sha256 matches submitted code", rec["sha256"] == sha)
        check("receipt is version 1", rec["version"] == 1)
        latest = os.path.join(drop, "team_rocket.py")
        check("latest pointer written", os.path.exists(latest))
        with open(latest) as f:
            check("latest content = header + code", f.read() == "# Submitted by: Team Rocket\n" + bot)
        check("journal has one version", len(journal_files(drop, "team_rocket")) == 1)

        st, body = post(url, bot, team="Team Rocket", token=tok["team_duck_norris"])
        check("wrong token for existing slug -> 409", st == 409)
        with open(latest) as f:
            check("409 leaves original byte-unchanged",
                  f.read() == "# Submitted by: Team Rocket\n" + bot)

        st, _ = post(url, bot, team="Unregistered", token="nope")
        check("unregistered team -> 401", st == 401)

        # ---- a hostile team name is reflected into the browser error page ----
        hostile = "<script>alert(1)</script>"
        st, body = post(url, bot, team=hostile, token="nope", form=True)
        check("hostile team name -> 401", st == 401)
        check("hostile team name is escaped in the error page",
              "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body)
        st, body = post(url, bot, team='" onload="alert(1)', token="nope", form=True)
        check("quotes in a team name cannot break out of an attribute",
              '" onload="' not in body)

        # idempotent resubmit
        st, body = post(url, bot, team="Team Rocket", token=tok["team_rocket"])
        rec2 = json.loads(body)
        check("identical bytes -> same receipt, same version",
              st == 200 and rec2["sha256"] == rec["sha256"] and rec2["version"] == 1)
        check("identical bytes -> no new journal version",
              len(journal_files(drop, "team_rocket")) == 1)

        # new version
        st, body = post(url, bot2, team="Team Rocket", token=tok["team_rocket"])
        rec3 = json.loads(body)
        check("changed bytes -> version 2", st == 200 and rec3["version"] == 2)
        check("journal keeps both versions", len(journal_files(drop, "team_rocket")) == 2)

        # size cap
        st, _ = post(url, "x" * (300 * 1024), team="Team Rocket", token=tok["team_rocket"])
        check("300KB body -> 413", st == 413)

        # adversarial names via browser form (needs some token)
        st, _ = post(url, bot, team="Team\nRocket", token=tok["team_rocket"], form=True)
        check("newline in name sanitized, still routes by slug", st == 200)
        # the name is written into a .py file that tournament.ingest later executes,
        # so a second line in the name would be a second line of Python
        with open(latest) as f:
            head, rest = f.read().split("\n", 1)
        check("a newline in the name cannot add a line to the bot file",
              head == "# Submitted by: Team Rocket" and rest == bot)
        srv.journal("team_probe", "Evil\nimport os", bot)
        with open(os.path.join(drop, "team_probe.py")) as f:
            probe_head, probe_rest = f.read().split("\n", 1)
        check("journal strips newlines itself, not via the caller",
              probe_head == "# Submitted by: Evil import os" and probe_rest == bot)
        st, _ = post(url, bot, team="", token=tok["team_rocket"], form=True)
        check("empty team -> 400", st == 400)

        # /status page
        with urllib.request.urlopen(url + "/status", timeout=10) as r:
            status = json.loads(r.read().decode())
        check("/status counts received teams", status["teams_received"] >= 1
              and "team_rocket" in status["teams"])
        check("/status shows mode + version count", status["mode"] == "token"
              and status["teams"]["team_rocket"]["versions"] == 3)
        check("/status counts rejected posts", status["rejected_posts"] >= 3)

        # audit log
        acts = [a["action"] for a in audit_lines(drop)]
        check("audit log captured accepts and rejects",
              "accepted" in acts and any(a.startswith("rejected") for a in acts))
        check("audit lines carry slug+sha", all("slug" in a for a in audit_lines(drop)))
    finally:
        srv.shutdown()

    # ---- open mode: token check relaxed, journal/audit NEVER relaxed ----
    drop2 = os.path.join(tmp, "open")
    os.makedirs(drop2)
    srv, url = start_server(drop2, open_mode=True)
    try:
        st, body = post(url, bot, team="Alpha")
        check("open mode accepts tokenless POST", st == 200)
        st, body = post(url, bot2, team="Alpha")  # overwrite, different author scenario
        check("open-mode overwrite still journals a new version",
              st == 200 and len(journal_files(drop2, "team_alpha")) == 2)
        idx = os.path.join(drop2, "team_alpha", "index.jsonl")
        check("open-mode ledger intact (append-only index)", os.path.exists(idx))
        check("open-mode audit log active", len(audit_lines(drop2)) >= 2)
        first = journal_files(drop2, "team_alpha")[0]
        with open(os.path.join(drop2, "team_alpha", first)) as f:
            check("open-mode prior version still on disk", "# v2" not in f.read())
    finally:
        srv.shutdown()

    # ---- rate limit ----
    drop3 = os.path.join(tmp, "rate")
    os.makedirs(drop3)
    srv, url = start_server(drop3, open_mode=True, rate_interval=60.0)
    try:
        st, _ = post(url, bot, team="Speedy")
        st2, _ = post(url, bot2, team="Speedy")
        check("second POST inside rate window -> 429", st == 200 and st2 == 429)
    finally:
        srv.shutdown()

    # ---- arena.py client: hard-fail when URL set and unreachable ----
    env = dict(os.environ, ROBOT_WARS_SUBMIT_URL="http://127.0.0.1:9", ROBOT_WARS_DROP=os.path.join(tmp, "cli_drop"))
    p = subprocess.run([sys.executable, "arena.py", "--submit", "HardFail"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    check("client hard-fails when submit URL unreachable (exit != 0)", p.returncode != 0)
    check("client does NOT fall back to local drop when URL set",
          not os.path.exists(os.path.join(tmp, "cli_drop", "team_hardfail.py")))
    check("client tells the player to retry", "retry" in (p.stdout + p.stderr).lower())


if __name__ == "__main__":
    sys.exit(main())
