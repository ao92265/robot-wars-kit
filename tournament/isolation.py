"""Tournament-mode bot isolation. Each bot runs in its own subprocess so that a
`while True`, a crash, or an OOM can never freeze or kill the match.

Reliability > security: this is an internal event with trusted-ish submissions.
The guards are speed-bumps (per-tick wall-clock timeout + memory guard + crash
isolation), not a hostile-code sandbox.

Memory guard: POSIX workers cap themselves with RLIMIT_AS; on Windows (where
rlimit is unavailable) the parent attaches each worker to a Job Object with a
per-process memory limit, so a runaway bot gets MemoryError instead of freezing
the organiser machine.

The per-move budget (TIMEOUT_S) is overridable per pool/worker or via the
ROBOT_WARS_MOVE_TIMEOUT_S env var. That override exists for the CI determinism
gate — lifting the wall-clock budget so a loaded runner can't inject spurious
timeout substitutions. Event-day matches use the default.
"""

import importlib.util
import multiprocessing as mp
import os
import sys

TIMEOUT_S = 0.2      # per-move wall-clock budget
MAX_MISSES = 5       # consecutive misses before we give up on a worker
IDLE = {"thrust": None, "turn": 0.0, "fire": None, "drop_trap": False, "special": False}

_CTX = mp.get_context("spawn")


def _win_job_limit(pid, mem_mb):
    """Windows: OS-enforced per-process memory cap via a Job Object — the
    parent-side counterpart of the POSIX rlimit the worker sets on itself.
    Returns the job handle (keep it alive) or None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMITS),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
        JobObjectExtendedLimitInformation = 9
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x100, 0x1

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = mem_mb * 1024 * 1024
        if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h:
            k32.CloseHandle(job)
            return None
        ok = k32.AssignProcessToJobObject(job, h)
        k32.CloseHandle(h)
        if not ok:
            k32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _norm_fire(v):
    if v is True:
        return "laser"
    if v in ("laser", "rocket"):
        return v
    return None


def _safe_extract(action):
    """Coerce a bot's return into a minimal picklable action dict, or None.
    Mirrors engine.sandbox.normalise_action so rocket/trap intents survive the
    subprocess hop. (The engine normalises again on receipt as a safety net.)"""
    if not isinstance(action, dict):
        return None
    thrust = action.get("thrust")
    if thrust not in ("forward", "back"):
        thrust = None
    try:
        turn = float(action.get("turn", 0.0))
    except (TypeError, ValueError):
        turn = 0.0
    if turn != turn or turn in (float("inf"), float("-inf")):
        turn = 0.0
    return {"thrust": thrust, "turn": turn,
            "fire": _norm_fire(action.get("fire")),
            "drop_trap": bool(action.get("drop_trap", False)),
            "special": bool(action.get("special", False))}


def _worker_main(conn, path, seed, mem_mb):
    import sys as _sys
    _sys.dont_write_bytecode = True  # no __pycache__ litter in submission folders
    # best-effort hardening (POSIX only): cap memory so one bot can't OOM the host.
    try:
        import resource
        soft = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
    except Exception:
        pass
    import random
    rng = random.Random(seed)
    try:
        spec = importlib.util.spec_from_file_location("bot", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        decide = mod.decide
    except Exception:
        decide = None
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg == "STOP":
            break
        view = msg
        view.rng = rng
        if decide is None:
            conn.send(None)
            continue
        try:
            action = decide(view)
        except Exception:
            action = None
        try:
            conn.send(_safe_extract(action))
        except Exception:
            conn.send(None)


class _Worker:
    def __init__(self, path, seed, mem_mb=512, timeout_s=None):
        self.path, self.seed, self.mem_mb = path, seed, mem_mb
        if timeout_s is None:
            timeout_s = float(os.environ.get("ROBOT_WARS_MOVE_TIMEOUT_S", TIMEOUT_S))
        self.timeout_s = timeout_s
        self.parent, self.child = _CTX.Pipe(duplex=True)
        self.proc = _CTX.Process(target=_worker_main,
                                 args=(self.child, path, seed, mem_mb), daemon=True)
        self.proc.start()
        self._job = _win_job_limit(self.proc.pid, mem_mb)  # Windows memory guard
        self.misses = 0
        self.timeouts = 0  # total timeout substitutions, for results logging
        self.dead = False
        self.last = dict(IDLE)

    def decide(self, view):
        if self.dead or not self.proc.is_alive():
            self.dead = True
            return dict(IDLE)
        view.rng = None  # not picklable-for-continuity; worker holds its own
        try:
            self.parent.send(view)
        except Exception:
            self.dead = True
            return dict(IDLE)
        if self.parent.poll(self.timeout_s):
            try:
                action = self.parent.recv()
            except EOFError:
                self.dead = True
                return dict(IDLE)
            self.misses = 0
            self.last = action if isinstance(action, dict) else dict(IDLE)
            return self.last
        # timeout (e.g. infinite loop): reuse last action, give up after MAX_MISSES
        self.misses += 1
        self.timeouts += 1
        if self.misses >= MAX_MISSES:
            self._kill()
        return self.last

    def _kill(self):
        self.dead = True
        try:
            self.proc.terminate()
        except Exception:
            pass

    def close(self):
        try:
            if self.proc.is_alive():
                self.parent.send("STOP")
                self.proc.join(timeout=0.5)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()


class IsolationPool:
    """Owns one worker per robot id. Pass `.decider` to Game(decider=...)."""

    def __init__(self, specs, mem_mb=512, timeout_s=None):
        # specs: list of (robot_id, path, seed)
        self.workers = {rid: _Worker(path, seed, mem_mb, timeout_s)
                        for rid, path, seed in specs}

    def decider(self, robot, view):
        w = self.workers.get(robot.id)
        return w.decide(view) if w else dict(IDLE)

    def stats(self):
        """Per-robot substitution stats for results logging."""
        return {rid: {"timeouts": w.timeouts, "dead": w.dead}
                for rid, w in self.workers.items()}

    def close(self):
        for w in self.workers.values():
            w.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
