"""Crash-resume + concurrency-lock tests: a killed tournament continues from
its run log with identical results; a second concurrent run hard-fails.
Run from repo root:  python3 tests/test_resume.py"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament import runstate
from tournament.runstate import machine_lock, TournamentLockHeld, load_run_log
from tournament.bracket import run_bracket
from tournament import runner
from engine.dummies import sitting_duck, chaser, sniper, bomber

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def dummy(name, mod):
    path = os.path.join(ROOT, "engine", "dummies", f"{mod.__name__.split('.')[-1]}.py")
    return {"name": name, "path": path, "loadout": mod.LOADOUT}


def match_key(summary):
    return [(m["label"], m["seed"], m["winner"]) for m in summary["matches"]]


def truncate_run_log(out_dir, keep):
    """Simulate a crash after `keep` completed matches."""
    path = os.path.join(out_dir, runstate.RUN_LOG)
    with open(path) as f:
        lines = [l for l in f if l.strip()]
    kept, dropped = lines[:keep], lines[keep:]
    with open(path, "w") as f:
        f.writelines(kept)
    for line in dropped:  # crash also means later recordings never existed
        rec = json.loads(line)
        if os.path.exists(rec["jsonl"]):
            os.unlink(rec["jsonl"])
    return [json.loads(l)["label"] for l in kept]


def main():
    tmp = tempfile.mkdtemp(prefix="rw_resume_test_")
    lock_backup = runstate.LOCK_PATH
    runstate.LOCK_PATH = os.path.join(tmp, "test.lock")
    try:
        # ---- machine lock ----
        with machine_lock():
            try:
                with machine_lock():
                    check("second lock acquisition fails", False)
            except TournamentLockHeld as e:
                check("second lock acquisition fails", True)
                check("lock error explains itself", "delete the lock file" in str(e))
            try:
                run_bracket([], 1, tmp, auto=True)
                check("run_bracket refuses while lock held", False)
            except TournamentLockHeld:
                check("run_bracket refuses while lock held", True)
        check("lock released on exit", not os.path.exists(runstate.LOCK_PATH))

        # ---- bracket: reference run vs crash+resume ----
        teams = [{"name": "Red", "bots": [dummy("Red", chaser)]},
                 {"name": "Blue", "bots": [dummy("Blue", sniper)]},
                 {"name": "Green", "bots": [dummy("Green", bomber)]}]
        ref_dir = os.path.join(tmp, "ref")
        s_ref = run_bracket(teams, 7, ref_dir, auto=True)

        crash_dir = os.path.join(tmp, "crash")
        run_bracket(teams, 7, crash_dir, auto=True)  # complete, then rewind
        kept = truncate_run_log(crash_dir, 1)
        # sentinel: if resume re-ran the completed match it would overwrite this
        sentinel_path = json.loads(open(os.path.join(crash_dir, runstate.RUN_LOG)).readline())["jsonl"]
        with open(sentinel_path, "w") as f:
            f.write("SENTINEL — resume must not re-run completed matches\n")

        s_res = run_bracket(teams, 7, crash_dir, auto=True, resume=True)
        check("bracket resume: same champion", s_res["champion"] == s_ref["champion"])
        check("bracket resume: identical match labels+seeds+winners",
              match_key(s_res) == match_key(s_ref))
        with open(sentinel_path) as f:
            check("bracket resume: completed match NOT re-run", "SENTINEL" in f.read())
        check("bracket resume: continued past the crash point",
              len(load_run_log(crash_dir)) > len(kept))

        # ---- runner (heats + final): crash+resume ----
        old_gs, old_adv = runner.GROUP_SIZE, runner.ADVANCE
        runner.GROUP_SIZE, runner.ADVANCE = 2, 1
        try:
            bots = [dummy("duck", sitting_duck), dummy("chaser", chaser),
                    dummy("sniper", sniper), dummy("bomber", bomber)]
            rref = os.path.join(tmp, "rref")
            t_ref = runner.run_tournament(bots, 9, rref, auto=True, map_kw=None)

            rcrash = os.path.join(tmp, "rcrash")
            runner.run_tournament(bots, 9, rcrash, auto=True, map_kw=None)
            truncate_run_log(rcrash, 1)  # crash after heat 1
            t_res = runner.run_tournament(bots, 9, rcrash, auto=True, map_kw=None,
                                          resume=True)
            check("runner resume: same champion", t_res["champion"] == t_ref["champion"])
            check("runner resume: identical match labels+seeds+winners",
                  match_key(t_res) == match_key(t_ref))
        finally:
            runner.GROUP_SIZE, runner.ADVANCE = old_gs, old_adv
    finally:
        runstate.LOCK_PATH = lock_backup
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all resume/lock tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
