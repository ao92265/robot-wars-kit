"""Dummy: never moves, never fires. Confirms your weapon works. Easy win.
Also a minimal worked example of the contract."""

LOADOUT = {"hp": 6, "speed": 0, "damage": 0, "range": 3, "special": 0}


def decide(view):
    return {}  # do nothing
