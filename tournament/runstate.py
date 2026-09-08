"""Run-state plumbing for tournaments: the one-per-machine lock, the
incremental per-match run log that makes a crash resumable, and run-id dirs.

Lock: CPU contention between two concurrent tournaments changes wall-clock
timeout results (retro 2026-07-15), so a second runner/bracket on the same
machine must hard-fail, not silently corrupt determinism.

Run log: every completed match is appended to <out_dir>/run_log.jsonl the
moment it finishes. --resume reloads it and skips completed matches; seeds
are identity-derived (seeds.py) so the continuation is exactly the run the
crash interrupted.
"""

import json
import os
import tempfile
import time
from contextlib import contextmanager

LOCK_PATH = os.path.join(tempfile.gettempdir(), "robot-wars-tournament.lock")
RUN_LOG = "run_log.jsonl"


class TournamentLockHeld(RuntimeError):
    pass


@contextmanager
def machine_lock(path=None):
    path = path or LOCK_PATH
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(path) as f:
                holder = f.read().strip() or "?"
        except OSError:
            holder = "?"
        raise TournamentLockHeld(
            f"another tournament appears to be running on this machine "
            f"(lock {path}, pid {holder}). Never run two at once — CPU contention "
            f"changes results. If that process is gone, delete the lock file and retry.")
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    try:
        yield
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def new_run_id(seed):
    return time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime()) + f"-seed{seed}"


def load_run_log(out_dir):
    """Completed matches from a previous (crashed) run, keyed by label."""
    path = os.path.join(out_dir, RUN_LOG)
    done = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done[rec["label"]] = rec
    return done


def append_run_log(out_dir, rec):
    with open(os.path.join(out_dir, RUN_LOG), "a", newline="\n") as f:
        f.write(json.dumps(rec) + "\n")
