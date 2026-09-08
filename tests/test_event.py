"""Event lifecycle tests: create -> snapshot ceremony -> run -> closeout, with
closeout idempotence + integrity validation.
Run from repo root:  python3 tests/test_event.py"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament import event as ev

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def bundle_state(bundle):
    state = {}
    for dirpath, dirnames, filenames in os.walk(bundle):
        dirnames.sort()
        for n in sorted(filenames):
            p = os.path.join(dirpath, n)
            with open(p, "rb") as f:
                state[os.path.relpath(p, bundle)] = f.read()
    return state


def main():
    root = tempfile.mkdtemp(prefix="rw_event_test_")
    try:
        # create
        cfg = ev.create(root, "dryrun", fmt="ffa", map_kw="classic", seed=11)
        check("create writes event.json", cfg["event_id"] == "dryrun"
              and os.path.exists(os.path.join(root, "dryrun", "event.json")))
        try:
            ev.create(root, "dryrun")
            check("duplicate create refused", False)
        except SystemExit:
            check("duplicate create refused", True)

        # stage 3 example bots straight into the drop (offline organiser path)
        drop = os.path.join(root, "dryrun", "submissions")
        staged = 0
        for name in ("artillery_sprint.py", "brawler_shotgun.py", "skirmisher_hover.py"):
            shutil.copy2(os.path.join(ROOT, "examples", name),
                         os.path.join(drop, f"team_{name}"))
            staged += 1
        check("staged example bots", staged == 3)

        # run before snapshot must refuse (hot folder is never the input)
        try:
            ev.run(root, "dryrun", auto=True)
            check("run refuses before snapshot", False)
        except SystemExit:
            check("run refuses before snapshot", True)

        # snapshot ceremony
        meta = ev.snapshot(root, "dryrun")
        check("snapshot hashes every bot", meta["n"] == 3 and len(meta["files"]) == 3)
        try:
            ev.snapshot(root, "dryrun")
            check("second snapshot refused (immutable close)", False)
        except SystemExit:
            check("second snapshot refused (immutable close)", True)

        # run from the snapshot
        summary = ev.run(root, "dryrun", auto=True)
        check("run produced a champion", bool(summary and summary.get("champion")))
        check("results carry event seed + scheme",
              summary["event_seed"] == 11 and summary["seed_scheme"] == "v2")

        # closeout: idempotent byte-identical bundle
        b1 = ev.closeout(root, "dryrun")
        s1 = bundle_state(b1)
        check("bundle has sha256 manifest", "sha256-manifest.json" in s1)
        man = json.loads(s1["sha256-manifest.json"])
        check("manifest covers every bundled file",
              set(man["files"].keys()) == set(k for k in s1 if k != "sha256-manifest.json"))
        b2 = ev.closeout(root, "dryrun")
        s2 = bundle_state(b2)
        check("closeout is idempotent (byte-identical bundle)", s1 == s2)

        # integrity: delete a referenced recording -> closeout blocks, names it
        run_dir = sorted(os.listdir(os.path.join(root, "dryrun", "recordings")))[0]
        rec_dir = os.path.join(root, "dryrun", "recordings", run_dir)
        victim = os.path.join(rec_dir, "FINAL.jsonl")
        os.rename(victim, victim + ".hidden")
        try:
            ev.closeout(root, "dryrun")
            check("closeout blocks on missing recording", False)
        except SystemExit as e:
            check("closeout blocks on missing recording", e.code == 1)
        os.rename(victim + ".hidden", victim)
        check("restored recording closes out again", ev.closeout(root, "dryrun") == b1)

        # status runs
        ev.status(root, "dryrun")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all event lifecycle tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
