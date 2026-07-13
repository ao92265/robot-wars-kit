"""Announcer line script for the Robot Wars 3D arena.

Two kinds of clips:
  GENERIC   — name-independent, baked once (fight / final blow / winner / ...).
  PER-TEAM  — "{TEAM} destroyed!" / "{TEAM} wins!", baked pre-show once the team
              list is locked (dynamic names, zero live risk).

The renderer plays a clip on the matching game event; if a clip is missing it
falls back to the browser's built-in speech. Keys here MUST match the keys the
renderer looks up (see arena.app.js: playClip / slug).

Voice direction (Mortal-Kombat-style hype announcer) is in the `*_STYLE` text —
ElevenLabs v3 honours bracketed delivery tags like [shouting], [excited].
"""
import re

# ElevenLabs config (override via env: ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL_ID).
# Voice IDs come from `python3 -m tournament.voice.bake --list-voices`.
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"   # placeholder; pick a deep "epic" voice
DEFAULT_MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = "mp3_44100_128"

# Generic, name-independent lines. key -> announcer text (with v3 delivery tags).
GENERIC = {
    "announce":   "[announcer, booming] Welcome... to ROBOT WARS!",
    "fight":      "[shouting] Fight!",
    "final_blow": "[intense, slow] Finish him!",
    "eliminated": "[excited] Eliminated!",
    "double_ko":  "[shouting] Double knockout!",
    "winner":     "[triumphant] We have a winner!",
    "flawless":   "[shouting, triumphant] Flawless victory!",
}

# Per-team templates. {TEAM} is replaced with the (upper-cased) team name.
PER_TEAM = {
    "elim_{slug}": "[excited, shouting] {TEAM}... destroyed!",
    "win_{slug}":  "[triumphant, booming] {TEAM} wins!",
}


def slug(name):
    """Stable key from a team name. MUST match arena.app.js slug()."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def per_team_lines(team_names):
    """Expand PER_TEAM templates for a locked team list -> {key: text}."""
    out = {}
    for name in team_names:
        s, up = slug(name), name.upper()
        for ktpl, vtpl in PER_TEAM.items():
            out[ktpl.format(slug=s)] = vtpl.format(TEAM=up)
    return out
