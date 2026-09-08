"""One-command event lifecycle. An event lives in events/<event-id>/ and moves
through: create -> register -> serve -> snapshot -> run -> closeout.

  python3 -m tournament.event create  <id> [--format ffa|bracket] [--map M] [--seed N]
  python3 -m tournament.event register <id> "Team A" "Team B" ...
  python3 -m tournament.event serve   <id> [--open] [--port N]
  python3 -m tournament.event snapshot <id>
  python3 -m tournament.event run     <id> [--auto] [--resume]
  python3 -m tournament.event closeout <id> [--publish]
  python3 -m tournament.event rehearse <id>
  python3 -m tournament.event status  <id>

Snapshot is the close-of-submissions ceremony: an immutable content-hashed
copy that every downstream step reads — never the hot drop folder. Closeout
assembles the canonical bundle (config, roster, journal, snapshot, recordings,
results, sha256 manifest), validates referential integrity, and can publish it
as GitHub Release event-<id>. Closeout is idempotent: same inputs -> byte-
identical bundle. The 2026-07-09 artifacts predate this pipeline and are
migrated best-effort, never regenerated.
"""

import glob
import hashlib
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.ingest import ingest
from tournament.recmeta import RESULTS_SCHEMA, engine_sha, run_meta
from tournament.runstate import TournamentLockHeld, new_run_id
from tournament import roster as roster_mod

EVENTS_ROOT = os.path.join(ROOT, "events")
FORMATS = ("ffa", "bracket")


def _ws(root, event_id):
    return os.path.join(root, event_id)


def _cfg_path(ws):
    return os.path.join(ws, "event.json")


def _load_cfg(ws):
    with open(_cfg_path(ws)) as f:
        return json.load(f)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- create / register / serve ----

def create(root, event_id, fmt="ffa", map_kw="colosseum", seed=1):
    if fmt not in FORMATS:
        raise SystemExit(f"unknown format '{fmt}' — choose {'/'.join(FORMATS)}")
    ws = _ws(root, event_id)
    if os.path.exists(_cfg_path(ws)):
        raise SystemExit(f"event '{event_id}' already exists at {ws}")
    os.makedirs(os.path.join(ws, "submissions"), exist_ok=True)
    cfg = {"schema_version": RESULTS_SCHEMA, "event_id": event_id, "format": fmt,
           "map": map_kw, "seed": seed, "engine_sha": engine_sha(),
           "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(_cfg_path(ws), "w", newline="\n") as f:
        json.dump(cfg, f, indent=2)
    print(f"created event '{event_id}' ({fmt}, map {map_kw}, seed {seed}) -> {ws}")
    return cfg


def register(root, event_id, names):
    ws = _ws(root, event_id)
    _load_cfg(ws)  # must exist
    roster_path = os.path.join(ws, "submissions", "roster.json")
    issued = roster_mod.create(roster_path, names)
    print(f"roster -> {roster_path}   ({len(issued)} team(s))")
    print("Tokens (shown ONCE — hand each team theirs privately):")
    for e in issued:
        print(f"  {e['team']:<30} {e['slug']:<30} {e['token']}")
    return issued


def serve(root, event_id, open_mode=False, port=8000):
    from tournament.submit_server import SubmitServer, _lan_ip
    ws = _ws(root, event_id)
    _load_cfg(ws)
    drop = os.path.join(ws, "submissions")
    roster_path = os.path.join(drop, "roster.json")
    if not open_mode and not os.path.exists(roster_path):
        raise SystemExit(f"no roster at {roster_path} — run `register` first, or serve --open")
    srv = SubmitServer(("0.0.0.0", port), drop=drop,
                       roster_path=None if open_mode else roster_path,
                       open_mode=open_mode)
    ip = _lan_ip()
    mode = "OPEN (no tokens — journal + audit still on)" if open_mode else "token auth"
    print(f"[{event_id}] submission server up [{mode}]. Drop: {drop}")
    print(f"  Teams (browser):  http://{ip}:{port}")
    print(f"  Teams (CLI):      ROBOT_WARS_SUBMIT_URL=http://{ip}:{port} python3 arena.py --submit \"Team\"")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


# ---- snapshot (close of submissions) ----

def snapshot(root, event_id):
    ws = _ws(root, event_id)
    _load_cfg(ws)
    snap = os.path.join(ws, "snapshot")
    if os.path.exists(snap):
        raise SystemExit(f"submissions already closed: {snap} exists. The snapshot is "
                         f"immutable — delete the directory only if you know why.")
    drop = os.path.join(ws, "submissions")
    files = sorted(glob.glob(os.path.join(drop, "team_*.py")))
    if not files:
        raise SystemExit(f"no team_*.py submissions in {drop} — nothing to close.")
    os.makedirs(snap)
    hashes = {}
    for path in files:
        name = os.path.basename(path)
        shutil.copy2(path, os.path.join(snap, name))
        hashes[name] = _sha256_file(path)
    meta = {"schema_version": RESULTS_SCHEMA, "n": len(hashes), "files": hashes,
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.path.join(snap, "snapshot.json"), "w", newline="\n") as f:
        json.dump(meta, f, indent=2)
    print(f"submissions closed: {len(hashes)} bot(s) snapshotted -> {snap}")
    return meta


# ---- run ----

def _latest_run_dir(ws):
    runs = sorted(glob.glob(os.path.join(ws, "recordings", "run-*")))
    for d in reversed(runs):
        if os.path.exists(os.path.join(d, "run_log.jsonl")):
            return d
    return None


def run(root, event_id, auto=False, resume=False):
    ws = _ws(root, event_id)
    cfg = _load_cfg(ws)
    snap = os.path.join(ws, "snapshot")
    if not os.path.exists(snap):
        raise SystemExit("submissions are not closed yet — run `snapshot` first "
                         "(downstream reads the snapshot, never the hot folder).")
    print(f"Ingesting snapshot {snap} ...")
    manifest = ingest(snap)
    print(f"  accepted {manifest['n_accepted']}, rejected {manifest['n_rejected']}")
    for r in manifest["rejected"]:
        print(f"    rejected {r['name']}: {r['reason']}")
    if manifest["n_accepted"] < 2:
        raise SystemExit("Need at least 2 accepted bots.")
    if resume:
        out_dir = _latest_run_dir(ws)
        if not out_dir:
            raise SystemExit("--resume: no previous run log found.")
        print(f"Resuming {out_dir}")
    else:
        out_dir = os.path.join(ws, "recordings", new_run_id(cfg["seed"]))
    try:
        if cfg["format"] == "bracket":
            from tournament.bracket import run_bracket, teams_from_accepted
            teams = teams_from_accepted(manifest["accepted"])
            summary = run_bracket(teams, cfg["seed"], out_dir, auto=auto, resume=resume)
        else:
            from tournament.runner import run_tournament
            summary = run_tournament(manifest["accepted"], cfg["seed"], out_dir,
                                     auto=auto, map_kw=cfg["map"], resume=resume)
    except TournamentLockHeld as e:
        raise SystemExit(f"REFUSED: {e}")
    return summary


# ---- closeout ----

def _bundle_add(bundle, ws, rel):
    src = os.path.join(ws, rel)
    dst = os.path.join(bundle, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def closeout(root, event_id, publish=False):
    ws = _ws(root, event_id)
    cfg = _load_cfg(ws)
    problems = []

    # integrity: every match in every results file must have its recording
    run_dirs = sorted(glob.glob(os.path.join(ws, "recordings", "run-*")))
    results_seen = 0
    for rd in run_dirs:
        for res_name in ("tournament_results.json", "bracket_results.json"):
            rp = os.path.join(rd, res_name)
            if not os.path.exists(rp):
                continue
            results_seen += 1
            with open(rp) as f:
                summary = json.load(f)
            for m in summary.get("matches", []):
                rec = os.path.join(rd, f"{m['label']}.jsonl")
                if not os.path.exists(rec):
                    problems.append(f"missing recording for match {m['label']}: {rec}")
                if "seed" not in m:
                    problems.append(f"match {m['label']} has no recorded seed")
    if not results_seen:
        problems.append("no results file found in any run directory — run the tournament first")

    # integrity: snapshot manifest files all present + hashes intact
    snap = os.path.join(ws, "snapshot")
    snap_meta_path = os.path.join(snap, "snapshot.json")
    if os.path.exists(snap_meta_path):
        with open(snap_meta_path) as f:
            snap_meta = json.load(f)
        for name, sha in snap_meta["files"].items():
            p = os.path.join(snap, name)
            if not os.path.exists(p):
                problems.append(f"snapshot file missing: {name}")
            elif _sha256_file(p) != sha:
                problems.append(f"snapshot file altered after close: {name}")
    else:
        problems.append("no snapshot — submissions were never closed")

    if problems:
        print("CLOSEOUT BLOCKED — integrity problems:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)

    # assemble the bundle (rebuilt from scratch each time: idempotent)
    bundle = os.path.join(ws, "bundle")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    os.makedirs(bundle)
    _bundle_add(bundle, ws, "event.json")
    for rel in ("submissions/roster.json", "submissions/audit.jsonl"):
        if os.path.exists(os.path.join(ws, rel)):
            _bundle_add(bundle, ws, rel)
    def _bundle_files(pattern):
        # files only — bot imports can drop __pycache__/ dirs into these folders
        for fp in sorted(glob.glob(pattern)):
            if os.path.isfile(fp):
                _bundle_add(bundle, ws, os.path.relpath(fp, ws))

    for jdir in sorted(glob.glob(os.path.join(ws, "submissions", "team_*"))):
        if os.path.isdir(jdir):  # per-team append-only journal
            _bundle_files(os.path.join(jdir, "*"))
    _bundle_files(os.path.join(snap, "*"))
    for rd in run_dirs:
        _bundle_files(os.path.join(rd, "*"))

    # sha256 manifest over every bundled file (no timestamps: reruns byte-identical)
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(bundle):
        dirnames.sort()
        for name in sorted(filenames):
            fp = os.path.join(dirpath, name)
            manifest[os.path.relpath(fp, bundle)] = _sha256_file(fp)
    with open(os.path.join(bundle, "sha256-manifest.json"), "w", newline="\n") as f:
        json.dump({"schema_version": RESULTS_SCHEMA, "event_id": event_id,
                   "run_meta": run_meta(), "files": manifest}, f, indent=2, sort_keys=True)
    print(f"closeout OK: {len(manifest)} file(s) -> {bundle}")

    if publish:
        _publish(ws, event_id, bundle)
    return bundle


def _publish(ws, event_id, bundle):
    import subprocess
    zip_base = os.path.join(ws, f"event-{event_id}-bundle")
    zip_path = shutil.make_archive(zip_base, "zip", bundle)
    tag = f"event-{event_id}"
    print(f"publishing {zip_path} as GitHub Release {tag} ...")
    r = subprocess.run(["gh", "release", "create", tag, zip_path,
                        "--title", f"Event {event_id}",
                        "--notes", f"Canonical archive for event {event_id}. "
                                   f"sha256 manifest inside the bundle."],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr.strip())
        raise SystemExit(f"publish failed — bundle is intact at {bundle}; "
                         f"retry with: gh release create {tag} {zip_path}")
    print(r.stdout.strip() or f"published {tag}")


# ---- rehearse / status ----

def rehearse(root, event_id):
    """Full dry-run on the examples/ bots: create -> snapshot -> run -> closeout."""
    ws = _ws(root, event_id)
    if not os.path.exists(_cfg_path(ws)):
        create(root, event_id, fmt="ffa", map_kw="colosseum", seed=7)
    drop = os.path.join(ws, "submissions")
    os.makedirs(drop, exist_ok=True)
    for src in sorted(glob.glob(os.path.join(ROOT, "examples", "*.py"))):
        name = os.path.basename(src)
        if name == "__init__.py":
            continue
        shutil.copy2(src, os.path.join(drop, f"team_{name}"))
    print(f"rehearsal: staged {len(glob.glob(os.path.join(drop, 'team_*.py')))} example bots")
    snapshot(root, event_id)
    run(root, event_id, auto=True)
    bundle = closeout(root, event_id)
    print(f"REHEARSAL COMPLETE — dry-run bundle at {bundle}")
    return bundle


def status(root, event_id):
    ws = _ws(root, event_id)
    cfg = _load_cfg(ws)
    drop = os.path.join(ws, "submissions")
    subs = sorted(glob.glob(os.path.join(drop, "team_*.py")))
    snap = os.path.exists(os.path.join(ws, "snapshot"))
    runs = sorted(glob.glob(os.path.join(ws, "recordings", "run-*")))
    bundle = os.path.exists(os.path.join(ws, "bundle", "sha256-manifest.json"))
    print(f"event {cfg['event_id']}: format {cfg['format']}, map {cfg['map']}, seed {cfg['seed']}")
    print(f"  submissions: {len(subs)}   closed: {'yes' if snap else 'no'}   "
          f"runs: {len(runs)}   closeout: {'yes' if bundle else 'no'}")
    audit = os.path.join(drop, "audit.jsonl")
    if os.path.exists(audit):
        with open(audit) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        if lines:
            last = lines[-1]
            print(f"  last submission activity: {last['ts']} {last['slug']} ({last['action']})")
    return cfg


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__.strip())
        return 2
    cmd, event_id = args[0], args[1]
    rest = args[2:]

    def flag(name, default=None):
        if name in rest:
            i = rest.index(name)
            v = rest[i + 1]
            del rest[i:i + 2]
            return v
        return default

    root = flag("--root", EVENTS_ROOT)
    try:
        if cmd == "create":
            create(root, event_id, fmt=flag("--format", "ffa"),
                   map_kw=flag("--map", "colosseum"), seed=int(flag("--seed", "1")))
        elif cmd == "register":
            register(root, event_id, rest)
        elif cmd == "serve":
            serve(root, event_id, open_mode="--open" in rest,
                  port=int(flag("--port", "8000")))
        elif cmd == "snapshot":
            snapshot(root, event_id)
        elif cmd == "run":
            run(root, event_id, auto="--auto" in rest, resume="--resume" in rest)
        elif cmd == "closeout":
            closeout(root, event_id, publish="--publish" in rest)
        elif cmd == "rehearse":
            rehearse(root, event_id)
        elif cmd == "status":
            status(root, event_id)
        else:
            print(__doc__.strip())
            return 2
    except SystemExit:
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
