"""Isolation additions: per-move timeout override (param + env) for the CI
determinism gate, timeout substitution counter, Windows job-object guard no-op
on POSIX. Default match-time semantics (0.2s, last-action reuse) untouched —
pinned by the existing test_isolation.py.
Run from repo root:  python3 tests/test_isolation_override.py"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.isolation import _Worker, _win_job_limit, TIMEOUT_S

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


SLOW_BOT = """import time
LOADOUT = {}

def decide(view):
    time.sleep(0.5)
    return {"turn": 1.0}
"""


class V:
    """Minimal picklable stand-in for an engine view."""
    pass


def main():
    fd, slow = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(SLOW_BOT)
    try:
        check("default per-move budget unchanged", TIMEOUT_S == 0.2)

        # default budget: 0.5s decide misses the 0.2s window -> last action reused
        w = _Worker(slow, seed=1)
        try:
            a = w.decide(V())
            check("slow bot misses default window (idle action)", a["turn"] == 0.0)
            check("timeout substitution counted", w.timeouts == 1)
        finally:
            w.close()

        # explicit override lifts the budget -> real action arrives
        w = _Worker(slow, seed=1, timeout_s=5.0)
        try:
            a = w.decide(V())
            check("param override returns the real action", a["turn"] == 1.0)
            check("no substitution under lifted budget", w.timeouts == 0)
        finally:
            w.close()

        # env override (what the CI determinism gate sets)
        os.environ["ROBOT_WARS_MOVE_TIMEOUT_S"] = "5.0"
        try:
            w = _Worker(slow, seed=1)
            try:
                a = w.decide(V())
                check("env override returns the real action", a["turn"] == 1.0)
            finally:
                w.close()
        finally:
            del os.environ["ROBOT_WARS_MOVE_TIMEOUT_S"]

        # windows job-object guard: graceful no-op off-Windows
        if sys.platform != "win32":
            check("job-object guard no-ops on POSIX", _win_job_limit(os.getpid(), 512) is None)
        else:
            check("job-object guard returns a handle on Windows",
                  _win_job_limit(os.getpid(), 4096) is not None)
    finally:
        os.unlink(slow)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all isolation-override tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
