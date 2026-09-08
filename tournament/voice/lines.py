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
DEFAULT_VOICE_ID = "SOYHLrjzK2X1ezoPC6cr"   # Harry — "Fierce Warrior" (characters/animation)
DEFAULT_MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = "mp3_44100_128"

# Generic, name-independent lines. key -> announcer text (with v3 delivery tags).
GENERIC = {
    "announce":   "[announcer, booming] Welcome... to ROBOT WARS!",
    "fight":      "[shouting] Fight!",
    "final_blow": "[intense, slow] Finish him!",
    "eliminated": "[excited] Eliminated!",
    "double_ko":  "[shouting] Double knockout!",
    "sudden_death": "[ominous, booming] Sudden death! The floor is closing in!",
    "overheat":   "[urgent] Overheating!",
    "big_hit":    "[shouting] What a hit!",
    "flipper":    "[excited, shouting] Airborne!",
    "winner":     "[triumphant] We have a winner!",
    "flawless":   "[shouting, triumphant] Flawless victory!",
    "jam":        "[wry] Gun jammed!",
    "flip":       "[excited] Flipped!",
    # one per engine pickup kind (engine/game.py _collect_pickups)
    "pickup_overdrive": "[excited, shouting] Overdrive!",
    "pickup_shield":    "[excited] Shield up!",
    "pickup_haste":     "[excited] Haste!",
    "pickup_rockets":   "[excited, shouting] Rockets loaded!",
    "pickup_traps":     "[wry] Traps armed!",
    "pickup_repair":    "[commentating] Patched up!",
    # hazards. These fire every tick a bot is in the zone, so the renderer
    # rate-limits them — the lines themselves stay name-free (no id on the event)
    "hazard_lava":  "[urgent, shouting] Into the lava!",
    "hazard_pit":   "[wry] Straight into the pit!",
    "hazard_water": "[wry] In the drink!",
    "house_robot": "[ominous, booming] House robot on patrol. Stay clear!",
    # damage-state callouts. Numbers are spelled out — the model reads a bare
    # "75%" inconsistently between takes.
    "hp75": "[commentating] Down to seventy five percent!",
    "hp50": "[urgent] Down to fifty percent!",
    "hp25": "[urgent, shouting] Down to twenty five percent! It's in trouble!",
    "trouble": "[concerned, commentating] It's not looking good here.",
    "rally": "[surprised, excited] Still going!",
}

# Per-TEAM template. Keyed on the bare team name — arena.app.js showWinner()
# looks up "win_" + slug(team) in team mode.
PER_TEAM = {
    "win_{slug}":  "[triumphant, booming] Team {NAME} wins!",
}

# Per-BOT templates. Keyed on the full "Team:Bot" label the engine emits
# (tournament/bracket.py _entries_and_specs), which is what arena.app.js slugs
# for eliminations and the solo winner. Spoken name is the part after the colon.
PER_BOT = {
    "elim_{slug}":     "[excited, shouting] {NAME}... destroyed!",
    "win_{slug}":      "[triumphant, booming] {NAME} wins!",
    "jam_{slug}":      "[wry] {NAME}'s gun is jammed!",
    "flip_{slug}":     "[excited] {NAME} is flipped!",
    "overheat_{slug}": "[urgent] {NAME} is overheating!",
    "flipper_{slug}":  "[excited, shouting] {NAME} takes flight!",
    "hp75_{slug}":     "[commentating] {NAME}, down to seventy five percent.",
    "hp50_{slug}":     "[urgent] {NAME} down to fifty percent!",
    "hp25_{slug}":     "[urgent, shouting] {NAME} down to twenty five percent! It's in trouble!",
    "trouble_{slug}":  "[concerned, commentating] It's not looking good for {NAME}.",
    "rally_{slug}":    "[surprised, excited] {NAME} is still going!",
}

# Comical walk-on lines. The announcer roasts each bot as it enters. Bespoke
# where we know the name, template fallback otherwise so an unseen roster still
# gets a (duller) entrance. Keyed enter_<slug of the full "Team:Bot" label>.
ENTRANCE_JOKES = {
    "Chassis":    "Built like a filing cabinet. Drives like one, too.",
    "Circuit":    "Nine volts of pure ambition.",
    "Crankshaft": "Turns fuel into regret.",
    "Diesel":     "Loud, filthy, and somehow still legal.",
    "Dynamo":     "Generates its own electricity. And its own problems.",
    "Flywheel":   "Spins up in four seconds. Stops in never.",
    "Magneto":    "Attracts trouble. And cutlery.",
    "Overdrive":  "One gear. Wrong gear.",
    "Piston":     "Up, down, up, down. That is the entire strategy.",
    "Ratchet":    "Only tightens. Never lets go.",
    "Servo":      "Precision engineering. Questionable decisions.",
    "Socket":     "Fits everything. Commits to nothing.",
    "Sprocket":   "Small, toothy, and weirdly confident.",
    "Torque":     "All twist, no mercy.",
    "Turbo":      "Zero to disaster in two seconds.",
    "Voltage":    "Do not touch. Seriously. Do not touch.",
    # demo-reel bots (tournament/visual/samples/demo.jsonl)
    "Riot":       "Started out as a peaceful protest.",
    "Bastion":    "An immovable object. Also a fairly unmoving one.",
}
ENTRANCE_FALLBACK = "Weighs a ton, thinks about half that."


def slug(name):
    """Stable key from a team name. MUST match arena.app.js slug()."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def display_name(label):
    """Spoken name for a "Team:Bot" label. MUST match arena.app.js botLabel()."""
    return label.split(":", 1)[1] if ":" in label else label


def per_team_lines(team_names):
    """Expand PER_TEAM templates for a locked team list -> {key: text}."""
    out = {}
    for name in team_names:
        for ktpl, vtpl in PER_TEAM.items():
            out[ktpl.format(slug=slug(name))] = vtpl.format(NAME=name.upper())
    return out


def per_bot_lines(bot_labels):
    """Expand PER_BOT templates for the locked "Team:Bot" labels -> {key: text}."""
    out = {}
    for label in bot_labels:
        for ktpl, vtpl in PER_BOT.items():
            out[ktpl.format(slug=slug(label))] = vtpl.format(NAME=display_name(label).upper())
    return out


def entrance_lines(bot_labels):
    """Comical walk-on line per bot -> {key: text}."""
    out = {}
    for label in bot_labels:
        name = display_name(label)
        joke = ENTRANCE_JOKES.get(name, ENTRANCE_FALLBACK)
        out["enter_" + slug(label)] = f"[announcer, wry] {name.upper()}! {joke}"
    return out
