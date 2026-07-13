"""Tournament-mode bot isolation. Each bot runs in its own subprocess so that a
`while True`, a crash, or an OOM can never freeze or kill the match.

Reliability > security: this is an internal event with trusted-ish submissions.
The guards are speed-bumps (per-tick wall-clock timeout + memory rlimit + crash
isolation), not a hostile-code sandbox.
"""

import importlib.util
import multiprocessing as mp

TIMEOUT_S = 0.2      # per-move wall-clock budget
MAX_MISSES = 5       # consecutive misses before we give up on a worker
IDLE = {"thrust": None, "turn": 0.0, "fire": None, "drop_trap": False, "special": False}

_CTX = mp.get_context("spawn")


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
    def __init__(self, path, seed, mem_mb=512):
        self.path, self.seed, self.mem_mb = path, seed, mem_mb
        self.parent, self.child = _CTX.Pipe(duplex=True)
        self.proc = _CTX.Process(target=_worker_main,
                                 args=(self.child, path, seed, mem_mb), daemon=True)
        self.proc.start()
        self.misses = 0
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
        if self.parent.poll(TIMEOUT_S):
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

    def __init__(self, specs, mem_mb=512):
        # specs: list of (robot_id, path, seed)
        self.workers = {rid: _Worker(path, seed, mem_mb) for rid, path, seed in specs}

    def decider(self, robot, view):
        w = self.workers.get(robot.id)
        return w.decide(view) if w else dict(IDLE)

    def close(self):
        for w in self.workers.values():
            w.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
