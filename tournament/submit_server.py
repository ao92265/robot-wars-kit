"""Zero-dependency submission server for the live event.

Organiser runs this on the tournament machine; teams submit their bot over the
LAN (no shared drive needed). Two ways in:
  - CLI:     ROBOT_WARS_SUBMIT_URL=http://<host>:8000 python3 arena.py --submit "Team"
  - Browser: open http://<host>:8000 , paste my_bot.py, name your team, submit.

Submissions are SAVED ONLY (never executed here) -> team_<slug>.py in the drop
folder. Validation + smoke-testing happens later in `tournament.ingest`, which
runs each bot in isolation. So a malicious/looping submission can't hurt this server.

  python3 -m tournament.submit_server [--port 8000] [--drop submissions]
"""

import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

DROP = os.environ.get("ROBOT_WARS_DROP", "submissions")

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
<label>Paste your whole <code>my_bot.py</code></label><textarea name=code required></textarea>
<button type=submit>Submit robot</button></form>"""


def _slug(team):
    s = "".join(c if c.isalnum() else "_" for c in team).strip("_").lower() or "team"
    return s if s.startswith("team") else "team_" + s


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send(200, FORM.format(msg=""))

    def do_POST(self):
        if self.path.rstrip("/") != "/submit":
            return self._send(404, "not found", "text/plain")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        team = self.headers.get("X-Team-Name")
        code = body
        cli = team is not None
        if not cli:  # browser form post
            form = parse_qs(body)
            team = (form.get("team", [""])[0]).strip()
            code = form.get("code", [""])[0]
        if not team or not code.strip():
            return self._send(400, "missing team name or code", "text/plain")
        os.makedirs(DROP, exist_ok=True)
        dest = os.path.join(DROP, f"{_slug(team)}.py")
        with open(dest, "w") as f:
            f.write(f"# Submitted by: {team}\n" + code)
        print(f"  received '{team}' -> {dest}")
        if cli:
            return self._send(200, f"saved {os.path.basename(dest)}", "text/plain")
        self._send(200, FORM.format(
            msg=f"<div class='msg ok'>Saved as {os.path.basename(dest)}. "
                f"Re-submit any time to overwrite.</div>"))

    def log_message(self, *a):
        pass  # quiet


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    port = 8000
    args = sys.argv[1:]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    if "--drop" in args:
        global DROP
        DROP = args[args.index("--drop") + 1]
    os.makedirs(DROP, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    ip = _lan_ip()
    print(f"Submission server up. Drop folder: {os.path.abspath(DROP)}")
    print(f"  Teams (browser):  http://{ip}:{port}")
    print(f"  Teams (CLI):      ROBOT_WARS_SUBMIT_URL=http://{ip}:{port} python3 arena.py --submit \"Team\"")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
