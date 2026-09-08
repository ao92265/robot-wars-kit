"""Identity-derived seed schedule — scheme v2.

Seeds derive from WHO is in a match (event seed + match label + participant
names), never from filename or staging order, so re-staging or renaming
submission files cannot shuffle RNG (the event-1 defect: seed*100+index).
sha256-based because builtin hash() is salted per process.

Scheme v1 (2026-07-09) was index-based; its artifacts are frozen and never
reinterpreted under v2. Bump SCHEME if the derivation ever changes.
"""

import hashlib

SCHEME = "v2"


def _h48(*parts):
    s = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def match_seed(event_seed, label, names):
    """Engine seed for one match: identity of the match, not its staging."""
    return _h48(SCHEME, "match", event_seed, label, *sorted(names))


def bot_seed(event_seed, label, name):
    """Per-bot RNG seed inside a match."""
    return _h48(SCHEME, "bot", event_seed, label, name)


def shuffle_seed(event_seed, label):
    """Seed for roster shuffles (heats, bracket slots) over slug-sorted lists."""
    return _h48(SCHEME, "shuffle", event_seed, label)
