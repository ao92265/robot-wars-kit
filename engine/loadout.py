"""Loadout schema + budget validation. Reads tunables from engine/config.py.

A loadout has four parts:
  - five point-budget stats: hp, speed, damage, range, special (cap per stat)
  - an optional chassis `size`: "small" | "medium" | "large" (free; has tradeoffs)
  - an optional `gun`: "laser" | "cannon" | "shotgun" (free; has tradeoffs)
  - an optional `engine`: "standard" | "sprint" | "tank" | "hover" (free; has tradeoffs)
"""

from . import config

BUDGET = config.BUDGET
STAT_CAP = config.STAT_CAP
STATS = ("hp", "speed", "damage", "range", "special", "agility")
SIZES = tuple(config.SIZES.keys())
GUNS = tuple(config.GUNS.keys())
ENGINES = tuple(config.ENGINES.keys())


def validate_loadout(loadout):
    """Return (ok: bool, message: str). message is a clear, fixable reason on failure."""
    if not isinstance(loadout, dict):
        return False, "LOADOUT must be a dict, e.g. {'hp':4,'speed':3,'damage':2,'range':2,'special':1,'size':'medium'}"
    allowed = set(STATS) | {"size", "gun", "engine"}
    unknown = [k for k in loadout if k not in allowed]
    if unknown:
        return False, f"unknown key(s): {unknown}. Allowed: {list(STATS)} + 'size'/'gun'/'engine'"
    size = loadout.get("size", config.DEFAULT_SIZE)
    if size not in config.SIZES:
        return False, f"size must be one of {list(SIZES)} (got {size!r})"
    gun = loadout.get("gun", config.DEFAULT_GUN)
    if gun not in config.GUNS:
        return False, f"gun must be one of {list(GUNS)} (got {gun!r})"
    engine = loadout.get("engine", config.DEFAULT_ENGINE)
    if engine not in config.ENGINES:
        return False, f"engine must be one of {list(ENGINES)} (got {engine!r})"
    for k in STATS:
        v = loadout.get(k, 0)
        if not isinstance(v, int) or v < 0:
            return False, f"stat '{k}' must be a non-negative integer (got {v!r})"
        if v > STAT_CAP:
            return False, f"stat '{k}'={v} exceeds the per-stat cap of {STAT_CAP}"
    total = sum(loadout.get(k, 0) for k in STATS)
    if total > BUDGET:
        return False, f"loadout spends {total} points, budget is {BUDGET}. Trim {total - BUDGET}."
    return True, f"READY — {total}/{BUDGET} points spent, size '{size}', gun '{gun}', engine '{engine}'"


def resolve_stats(loadout):
    """Map point allocations + chassis size + gun/engine archetypes to concrete
    combat stats. Defaults ("laser"/"standard") multiply by 1.0 everywhere, so a
    loadout that never mentions them resolves to the exact locked numbers."""
    g = lambda k: int(loadout.get(k, 0))
    c = config.CURVES
    size = loadout.get("size", config.DEFAULT_SIZE)
    if size not in config.SIZES:
        size = config.DEFAULT_SIZE
    sz = config.SIZES[size]
    gun = loadout.get("gun", config.DEFAULT_GUN)
    if gun not in config.GUNS:
        gun = config.DEFAULT_GUN
    gn = config.GUNS[gun]
    engine = loadout.get("engine", config.DEFAULT_ENGINE)
    if engine not in config.ENGINES:
        engine = config.DEFAULT_ENGINE
    en = config.ENGINES[engine]
    return {
        "max_hp": int((c["hp"]["base"] + g("hp") * c["hp"]["per"]) * sz["hp_mult"] * en["hp"]),
        "move_speed": (c["speed"]["base"] + g("speed") * c["speed"]["per"]) * sz["speed_mult"] * en["speed"],
        "weapon_damage": int(round((c["damage"]["base"] + g("damage") * c["damage"]["per"]) * gn["dmg"])),
        "weapon_range": float(c["range"]["base"] + g("range") * c["range"]["per"]) * gn["range"],
        "weapon_arc_deg": config.WEAPON_ARC_DEG * gn["arc"],
        "cooldown_ticks": int(round(config.COOLDOWN_TICKS * gn["cd"])),
        # agility points + engine/gun feel: 0 pts, standard, laser = the locked 34.0
        "turn_rate_deg": (c["agility"]["base"] + g("agility") * c["agility"]["per"]) * en["turn"] * gn["turn"],
        "special_level": g("special"),
        "radius": sz["radius"],
        "size": size,
        "gun": gun,
        "gun_multi": gn["multi"],
        "engine": engine,
        "slow_resist": en["slow_resist"],
        "hover": en["hover"],
    }
