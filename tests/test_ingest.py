"""Ingest sandboxing tests: submitted code never executes in the parent process,
module-level hangs/crashes are rejected not fatal, AST allowlist screens imports.
Run from repo root:  python3 tests/test_ingest.py   (pure stdlib, no pytest needed)."""

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.ingest import ingest, _ast_check

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


GOOD = """import math
LOADOUT = {}

def decide(view):
    return {"turn": math.pi / 4}
"""

HANG = """LOADOUT = {}
while True:
    pass

def decide(view):
    return {}
"""

CRASH = """LOADOUT = {}
raise RuntimeError("boom at import")

def decide(view):
    return {}
"""

BAD_IMPORT = """import os
LOADOUT = {}
open("ast_should_have_stopped_me.txt", "w").write(str(os.getpid()))

def decide(view):
    return {}
"""

NO_DECIDE = """LOADOUT = {}
"""


def write_bot(folder, name, src):
    with open(os.path.join(folder, name), "w") as f:
        f.write(src)


def rejected_reason(manifest, name):
    for r in manifest["rejected"]:
        if r["name"] == name:
            return r["reason"]
    return None


def main():
    tmp = tempfile.mkdtemp(prefix="rw_ingest_test_")
    marker = os.path.join(os.getcwd(), "ast_should_have_stopped_me.txt")
    if os.path.exists(marker):
        os.unlink(marker)
    try:
        # ---- AST allowlist unit checks ----
        good_path = os.path.join(tmp, "team_good.py")
        write_bot(tmp, "team_good.py", GOOD)
        ok, _ = _ast_check(good_path)
        check("math import allowed", ok)

        bad_path = os.path.join(tmp, "team_bad_import.py")
        write_bot(tmp, "team_bad_import.py", BAD_IMPORT)
        ok, reason = _ast_check(bad_path)
        check("os import rejected by AST", not ok)
        check("rejection names the disallowed module", "os" in reason)
        check("rejection lists what IS allowed", "math" in reason)

        # ---- full ingest over a hostile folder ----
        folder = os.path.join(tmp, "drop")
        os.makedirs(folder)
        write_bot(folder, "team_good.py", GOOD)
        write_bot(folder, "team_hang.py", HANG)
        write_bot(folder, "team_crash.py", CRASH)
        write_bot(folder, "team_bad_import.py", BAD_IMPORT)
        write_bot(folder, "team_no_decide.py", NO_DECIDE)

        t0 = time.time()
        manifest = ingest(folder)
        elapsed = time.time() - t0

        accepted = {a["name"] for a in manifest["accepted"]}
        check("good bot accepted", "team_good" in accepted)
        check("good bot loadout extracted", any(
            a["name"] == "team_good" and a["loadout"] == {} for a in manifest["accepted"]))

        check("module-level infinite loop rejected, ingest survives",
              "team_hang" not in accepted and rejected_reason(manifest, "team_hang") is not None)
        check("hang rejection mentions the hang",
              "hung" in (rejected_reason(manifest, "team_hang") or "").lower())
        check("ingest of a hanging bot completes in bounded time", elapsed < 120)

        check("module-level crash rejected with import reason",
              "import failed" in (rejected_reason(manifest, "team_crash") or ""))

        check("disallowed import rejected before execution",
              rejected_reason(manifest, "team_bad_import") is not None)
        check("AST screen ran pre-execution (no side-effect file)",
              not os.path.exists(marker) and
              not os.path.exists(os.path.join(folder, "ast_should_have_stopped_me.txt")))

        check("missing decide() rejected",
              "missing LOADOUT or decide" in (rejected_reason(manifest, "team_no_decide") or ""))

        check("manifest counts consistent",
              manifest["n_accepted"] == 1 and manifest["n_rejected"] == 4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(marker):
            os.unlink(marker)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all ingest tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
