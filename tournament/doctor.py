"""Preflight doctor: verify the organiser machine can actually run the show,
BEFORE the room fills up. Checks interpreter, spawn, ports, disk, the
tournament lock, the memory guard for this OS, and engine determinism.

  python3 -m tournament.doctor [--fast]     (--fast skips the determinism run)

Exit 0 = green board. Any FAIL = fix before event day (the runbook maps each
check to its drill).
"""

import os
import shutil
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def check_python():
    v = sys.version_info
    report("Python >= 3.9", v >= (3, 9), f"{v.major}.{v.minor}.{v.micro} at {sys.executable}")


def check_launchers():
    found = [c for c in ("python3", "python", "py") if shutil.which(c)]
    report("player launcher on PATH (python3/python/py)", bool(found), ", ".join(found) or "none")


def check_spawn():
    try:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_spawn_probe, args=(q,))
        p.start()
        ok = q.get(timeout=30) == "alive" and (p.join(5) or p.exitcode == 0)
        report("multiprocessing spawn (bot isolation)", ok)
    except Exception as e:
        report("multiprocessing spawn (bot isolation)", False, repr(e))


def _spawn_probe(q):
    q.put("alive")


def check_port():
    try:
        s = socket.socket()
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]
        s.close()
        report("can bind a LAN port (submit server)", True, f"probe bound :{port}")
    except Exception as e:
        report("can bind a LAN port (submit server)", False, repr(e))


def check_disk():
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    report("disk free >= 1 GB (recordings)", free_gb >= 1.0, f"{free_gb:.1f} GB free")


def check_lock():
    from tournament.runstate import LOCK_PATH
    held = os.path.exists(LOCK_PATH)
    report("no stale tournament lock", not held,
           f"{LOCK_PATH}" + (" — delete it if no tournament is running" if held else ""))


def check_memory_guard():
    if sys.platform == "win32":
        from tournament.isolation import _win_job_limit
        job = _win_job_limit(os.getpid(), 4096)
        report("memory guard (Windows Job Object)", job is not None,
               "runaway bots will be capped" if job else
               "UNAVAILABLE — a memory-hog bot can freeze this machine")
    else:
        try:
            import resource  # noqa: F401
            report("memory guard (POSIX rlimit)", True, "workers cap themselves")
        except ImportError:
            report("memory guard (POSIX rlimit)", False, "resource module missing")


def check_determinism():
    print("  ...   determinism self-check (two identical matches, ~10s)")
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "determinism_gate.py")],
                       capture_output=True, text=True, timeout=600)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    report("engine determinism (same seed = same match)", r.returncode == 0, tail)


def main():
    fast = "--fast" in sys.argv
    print("Robot Wars preflight doctor")
    check_python()
    check_launchers()
    check_spawn()
    check_port()
    check_disk()
    check_lock()
    check_memory_guard()
    if not fast:
        check_determinism()
    bad = RESULTS.count(False)
    print(f"\n{'GREEN BOARD — ready to run the show.' if not bad else str(bad) + ' CHECK(S) FAILED — see ORGANISER-RUNBOOK.md drills.'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
