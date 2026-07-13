"""Safe in-process bot invocation for DEV mode (try/except + action validation).
A bot error becomes a do-nothing tick — it never crashes the match. The hard
per-move timeout + process isolation lives in tournament/isolation.py for the show.

An action is a dict; a bot may set any subset of:
  {"thrust": "forward"|"back",   # move
   "turn": <deg>,                # rotate (clamped to your turn rate)
   "fire": "laser"|"rocket",     # shoot (True also accepted = laser)
   "drop_trap": True,            # drop a mine at your current position
   "special": True}              # dash
Missing/invalid keys default to no-op. This is the only contract a bot has to
honour; anything weird it returns is coerced here, so a bot can't break a match
by returning junk.
"""

IDLE = {"thrust": None, "turn": 0.0, "fire": None, "drop_trap": False, "special": False}


def _norm_fire(v):
    """Coerce the 'fire' field. True -> laser (back-compat with v1 bots)."""
    if v is True:
        return "laser"
    if v in ("laser", "rocket"):
        return v
    return None


def normalise_action(action):
    """Coerce a bot's return value into a legal action dict; invalid parts → no-op."""
    if not isinstance(action, dict):
        return dict(IDLE)
    thrust = action.get("thrust")
    if thrust not in ("forward", "back"):
        thrust = None
    try:
        turn = float(action.get("turn", 0.0))
    except (TypeError, ValueError):
        turn = 0.0
    if turn != turn or turn in (float("inf"), float("-inf")):  # NaN / inf guard
        turn = 0.0
    return {
        "thrust": thrust,
        "turn": turn,
        "fire": _norm_fire(action.get("fire")),
        "drop_trap": bool(action.get("drop_trap", False)),
        "special": bool(action.get("special", False)),
    }


def safe_decide(decide_fn, view):
    """Call a bot's decide(view); any exception → idle. Returns a normalised action."""
    try:
        return normalise_action(decide_fn(view))
    except Exception:
        return dict(IDLE)
