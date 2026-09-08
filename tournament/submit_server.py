"""Zero-dependency submission server for the live event.

Organiser runs this on the tournament machine; teams submit their bot over the
LAN (no shared drive needed). Two ways in:
  - CLI:     ROBOT_WARS_SUBMIT_URL=http://<host>:8000 ROBOT_WARS_TOKEN=<token> \
             python3 arena.py --submit "Team"
  - Browser: open http://<host>:8000 , paste my_bot.py, name your team + token, submit.

Submissions are SAVED ONLY (never executed here) -> team_<slug>.py in the drop
folder. Validation + smoke-testing happens later in `tournament.ingest`, which
runs each bot in isolation. So a malicious/looping submission can't hurt this server.

Integrity model:
  - Auth: per-team tokens issued by `python3 -m tournament.roster` (hashes in
    roster.json). No token -> 401. Wrong token for a slug -> 409, file untouched.
    `--open` relaxes ONLY the token check (single-room LAN events).
  - Append-only journal: every accepted submission is also stored as
    <drop>/<slug>/<utc-ts>_<sha8>.py with an index.jsonl ledger — an overwrite
    is always attributable and reversible, even in --open mode.
  - Atomic writes (temp file + os.replace), sha256 receipts, idempotent
    resubmits, 256KB body cap, audit.jsonl of every request.

  python3 -m tournament.submit_server [--port 8000] [--drop submissions]
                                      [--roster roster.json] [--open]
"""

import hashlib
import html
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

MAX_BODY = 256 * 1024  # 413 above this

FORM = """<!doctype html><meta charset=utf-8><title>Robot Wars — submit</title>
<style>body{{background:#0a1620;color:#eaf2f8;font-family:system-ui,sans-serif;max-width:760px;
margin:6vh auto;padding:0 18px}}h1{{color:#3fd0c9}}input,textarea{{width:100%;background:#0d1c28;
color:#eaf2f8;border:1px solid #1c2e3c;border-radius:8px;padding:12px;font-family:ui-monospace,monospace}}
textarea{{height:48vh}}button{{margin-top:14px;background:#3fd0c9;color:#04121a;border:0;border-radius:8px;
padding:12px 22px;font-size:16px;font-weight:700;cursor:pointer}}label{{display:block;margin:14px 0 6px}}
.msg{{padding:12px;border-radius:8px;margin-bottom:16px}}.ok{{background:#13351f;color:#6bCB77}}
.err{{background:#3a1620;color:#ff9b9b}}</style>
<h1>🤖 Submit your robot</h1>{msg}
<form method=post action=/submit>
<label>Team name</label><input name=team required placeholder="Team Rocket">
{token_field}
<label>Paste your whole <code>my_bot.py</code></label><textarea name=code required></textarea>
<button type=submit>Submit robot</button></form>"""

TOKEN_FIELD = ("<label>Team token (from the organiser)</label>"
               "<input name=token required placeholder=\"paste your token\">")


def _slug(team):
    s = "".join(c if c.isalnum() else "_" for c in team).lower()
    s = re.sub(r"_+", "_", s).strip("_") or "team"
    return s if s.startswith("team") else "team_" + s


def _clean_team(team):
    """Sanitize a submitted team name: NFKD-normalize, strip control chars, cap length."""
    import unicodedata
    team = unicodedata.normalize("NFKD", team or "")
    team = "".join(c if c.isprintable() else " " for c in team)
    return " ".join(team.split())[:64]


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_roster(path):
    with open(path) as f:
        data = json.load(f)
    return data.get("teams", {})


class SubmitServer(ThreadingHTTPServer):
    """HTTP server carrying the event config so handlers (and tests) don't use globals."""

    def __init__(self, addr, drop="submissions", roster_path=None, open_mode=False,
                 rate_interval=1.0):
        self.drop = drop
        self.open_mode = open_mode
        self.rate_interval = rate_interval
        self.roster = load_roster(roster_path) if roster_path else {}
        self._last_post = {}
        self._lock = threading.Lock()
        super().__init__(addr, Handler)

    # ---- journal + audit (always on, regardless of auth mode) ----

    def audit(self, action, slug, sha, remote_ip):
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "slug": slug, "sha256": sha, "remote_ip": remote_ip,
                           "action": action})
        with self._lock:
            os.makedirs(self.drop, exist_ok=True)
            with open(os.path.join(self.drop, "audit.jsonl"), "a", newline="\n") as f:
                f.write(line + "\n")

    def rate_limited(self, slug):
        with self._lock:
            now = time.monotonic()
            last = self._last_post.get(slug)
            if last is not None and (now - last) < self.rate_interval:
                return True
            self._last_post[slug] = now
            return False

    def journal(self, slug, team, code):
        """Append-only store. Returns receipt dict; idempotent on identical bytes."""
        sha = hashlib.sha256(code.encode()).hexdigest()
        jdir = os.path.join(self.drop, slug)
        index = os.path.join(jdir, "index.jsonl")
        with self._lock:
            os.makedirs(jdir, exist_ok=True)
            last = None
            if os.path.exists(index):
                with open(index) as f:
                    for line in f:
                        if line.strip():
                            last = json.loads(line)
            if last and last["sha256"] == sha:
                return {"slug": slug, "sha256": sha,
                        "submitted_at": last["submitted_at"], "version": last["version"]}
            version = (last["version"] + 1) if last else 1
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            fname = f"{ts}_{sha[:8]}.py"
            # This file is executed later by tournament.ingest, so the name must
            # not be able to add a second line of Python. `_clean_team` happens to
            # drop newlines today; don't make that load-bearing from a distance.
            content = "# Submitted by: " + team.replace("\r", " ").replace("\n", " ") + "\n" + code
            _atomic_write(os.path.join(jdir, fname), content)
            _atomic_write(os.path.join(self.drop, f"{slug}.py"), content)
            entry = {"version": version, "sha256": sha, "submitted_at": ts,
                     "team": team, "file": fname}
            with open(index, "a", newline="\n") as f:
                f.write(json.dumps(entry) + "\n")
            return {"slug": slug, "sha256": sha, "submitted_at": ts, "version": version}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _form(self, msg=""):
        token_field = "" if self.server.open_mode else TOKEN_FIELD
        return FORM.format(msg=msg, token_field=token_field)

    def do_GET(self):
        if self.path.rstrip("/") == "/status":
            return self._send(200, json.dumps(self._status()), "application/json")
        self._send(200, self._form())

    def _status(self):
        """Organiser glance: who's in, when the last one landed, what bounced."""
        drop = self.server.drop
        teams = {}
        for d in sorted(os.listdir(drop) if os.path.isdir(drop) else []):
            idx = os.path.join(drop, d, "index.jsonl")
            if os.path.isfile(idx):
                with open(idx) as f:
                    lines = [json.loads(l) for l in f if l.strip()]
                if lines:
                    last = lines[-1]
                    teams[d] = {"versions": last["version"], "team": last["team"],
                                "last_submitted": last["submitted_at"]}
        rejects, last_activity = 0, None
        audit = os.path.join(drop, "audit.jsonl")
        if os.path.isfile(audit):
            with open(audit) as f:
                for line in f:
                    if line.strip():
                        a = json.loads(line)
                        last_activity = a
                        if a["action"].startswith("rejected"):
                            rejects += 1
        return {"mode": "open" if self.server.open_mode else "token",
                "teams_received": len(teams), "teams": teams,
                "rejected_posts": rejects, "last_activity": last_activity}

    def _reject(self, code, reason, slug, sha, cli):
        self.server.audit(f"rejected_{code}", slug, sha, self.client_address[0])
        if cli:
            return self._send(code, reason, "text/plain")
        # `reason` quotes the submitted team name back at the submitter, so it is
        # participant-controlled text, never markup.
        return self._send(code, self._form(
            msg=f"<div class='msg err'>{html.escape(reason, quote=True)}</div>"))

    def do_POST(self):
        if self.path.rstrip("/") != "/submit":
            return self._send(404, "not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, "missing body", "text/plain")
        if length > MAX_BODY:
            self.server.audit("rejected_413", "?", "", self.client_address[0])
            return self._send(413, f"submission too large (max {MAX_BODY // 1024}KB)", "text/plain")
        body = self.rfile.read(length).decode("utf-8", "replace")
        team = self.headers.get("X-Team-Name")
        token = self.headers.get("X-Team-Token")
        code = body
        cli = team is not None
        if not cli:  # browser form post
            form = parse_qs(body)
            team = (form.get("team", [""])[0]).strip()
            code = form.get("code", [""])[0]
            token = token or (form.get("token", [""])[0]).strip() or None
        team = _clean_team(team or "")
        if not team or not code.strip():
            return self._reject(400, "missing team name or code", "?", "", cli)
        slug = _slug(team)
        sha = hashlib.sha256(code.encode()).hexdigest()

        if not self.server.open_mode:
            entry = self.server.roster.get(slug)
            if entry is None:
                return self._reject(
                    401, f"team '{team}' is not registered — ask the organiser to add you "
                         f"(python3 -m tournament.roster)", slug, sha, cli)
            if not token:
                return self._reject(
                    401, "missing team token — the organiser gave you one at registration; "
                         "CLI: export ROBOT_WARS_TOKEN=<token>", slug, sha, cli)
            if hashlib.sha256(token.encode()).hexdigest() != entry["token_sha256"]:
                return self._reject(
                    409, f"token does not match team '{team}' — existing submission left "
                         f"untouched. Check your token with the organiser.", slug, sha, cli)

        if self.server.rate_limited(slug):
            return self._reject(429, "too many submissions — wait a moment and retry",
                                slug, sha, cli)

        receipt = self.server.journal(slug, team, code)
        self.server.audit("accepted", slug, receipt["sha256"], self.client_address[0])
        print(f"  received '{team}' v{receipt['version']} sha {receipt['sha256'][:8]} "
              f"-> {os.path.join(self.server.drop, slug + '.py')}")
        if cli:
            return self._send(200, json.dumps(receipt), "application/json")
        self._send(200, self._form(
            msg=f"<div class='msg ok'>Saved as {slug}.py — version {receipt['version']}, "
                f"sha256 {receipt['sha256'][:12]}…. Every version is kept; "
                f"resubmitting adds a new one.</div>"))

    def log_message(self, *a):
        pass  # request noise stays quiet; audit.jsonl records every POST


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    port = 8000
    drop = os.environ.get("ROBOT_WARS_DROP", "submissions")
    roster_path = None
    open_mode = False
    args = sys.argv[1:]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    if "--drop" in args:
        drop = args[args.index("--drop") + 1]
    if "--roster" in args:
        roster_path = args[args.index("--roster") + 1]
    if "--open" in args:
        open_mode = True
    if not open_mode:
        roster_path = roster_path or os.path.join(drop, "roster.json")
        if not os.path.exists(roster_path):
            print(f"No roster at {roster_path}.")
            print("Register teams first:  python3 -m tournament.roster "
                  f"{roster_path} \"Team A\" \"Team B\" ...")
            print("Or run a single-room LAN event without tokens:  --open")
            return 1
    os.makedirs(drop, exist_ok=True)
    srv = SubmitServer(("0.0.0.0", port), drop=drop, roster_path=roster_path,
                       open_mode=open_mode)
    ip = _lan_ip()
    mode = "OPEN (no tokens — journal + audit still on)" if open_mode else f"token auth ({roster_path})"
    print(f"Submission server up [{mode}]. Drop folder: {os.path.abspath(drop)}")
    print(f"  Teams (browser):  http://{ip}:{port}")
    print(f"  Teams (CLI):      ROBOT_WARS_SUBMIT_URL=http://{ip}:{port} python3 arena.py --submit \"Team\"")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
