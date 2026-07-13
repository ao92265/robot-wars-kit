#!/usr/bin/env python3
"""Bake the announcer voice pack for the 3D arena (P3).

Pre-renders each line to an mp3 via the ElevenLabs REST API, optionally runs an
ffmpeg "arena" post (pitch + reverb), and emits a single
`tournament/visual/src/voice_clips.js` that inlines every clip as a base64
data: URI. build_arena.py bakes that into arena.html, so the announcer plays
fully OFFLINE on stage. This is a BUILD-TIME tool (needs network + a key);
the runtime arena stays zero-dependency.

  export ELEVENLABS_API_KEY=...                 # never written to disk/commit
  python3 -m tournament.voice.bake --list-voices            # find a voice_id
  python3 -m tournament.voice.bake                          # generic lines only
  python3 -m tournament.voice.bake --teams "Red Bots,Blue Crew,Team Vortex"
  python3 -m tournament.voice.bake --teams-from submissions # read ingested names
  python3 -m tournament.voice.bake --fx                     # ffmpeg arena post

Security: the API key is read from the environment only. It is NEVER written to
a file, the emitted JS, the manifest, or a commit. Rotate the key after the event.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error

from . import lines as L

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CLIPS_DIR = os.path.join(HERE, "clips")
OUT_JS = os.path.join(ROOT, "tournament", "visual", "src", "voice_clips.js")
API = "https://api.elevenlabs.io/v1"


def _key():
    k = os.environ.get("ELEVENLABS_API_KEY")
    if not k:
        sys.exit("ELEVENLABS_API_KEY not set. Run:  ! export ELEVENLABS_API_KEY=...  "
                 "(never commit it; rotate after the event)")
    return k


def list_voices():
    req = urllib.request.Request(f"{API}/voices", headers={"xi-api-key": _key()})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    for v in data.get("voices", []):
        labels = v.get("labels", {})
        print(f"{v['voice_id']}  {v.get('name',''):20s}  "
              f"{labels.get('descriptive','')}/{labels.get('use_case','')}")


def tts(text, voice_id, model_id):
    """POST one line; return mp3 bytes. Raises on HTTP error."""
    body = json.dumps({
        "text": text, "model_id": model_id,
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.8, "style": 0.6},
    }).encode()
    url = f"{API}/text-to-speech/{voice_id}?output_format={L.OUTPUT_FORMAT}"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": _key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def arena_fx(mp3_bytes):
    """Light pitch-down + reverb so it sounds like a PA in a big hall."""
    if not shutil.which("ffmpeg"):
        return mp3_bytes
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.mp3"), os.path.join(d, "out.mp3")
        with open(src, "wb") as f:
            f.write(mp3_bytes)
        # drop pitch ~6%, add a short reverb tail via aecho
        flt = "asetrate=44100*0.94,aresample=44100,aecho=0.8:0.85:60|120:0.5|0.25"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", src, "-af", flt, dst],
                           check=True, capture_output=True)
            with open(dst, "rb") as f:
                return f.read()
        except subprocess.CalledProcessError:
            return mp3_bytes


def team_names_from_submissions(path):
    """Best-effort team list from an ingest manifest or submission dir."""
    man = os.path.join(path, "manifest.json")
    if os.path.exists(man):
        with open(man) as f:
            m = json.load(f)
        ents = m.get("entries", m if isinstance(m, list) else [])
        names = [e.get("name") or e.get("team") for e in ents if isinstance(e, dict)]
        return [n for n in names if n]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--teams", default="", help="comma-separated team names")
    ap.add_argument("--teams-from", default="", help="submissions dir to read names from")
    ap.add_argument("--fx", action="store_true", help="apply ffmpeg arena post")
    ap.add_argument("--voice-id", default=os.environ.get("ELEVENLABS_VOICE_ID", L.DEFAULT_VOICE_ID))
    ap.add_argument("--model-id", default=os.environ.get("ELEVENLABS_MODEL_ID", L.DEFAULT_MODEL_ID))
    args = ap.parse_args()

    if args.list_voices:
        list_voices(); return

    # assemble the line set: generic + per-team
    teams = [t.strip() for t in args.teams.split(",") if t.strip()]
    if args.teams_from:
        teams += team_names_from_submissions(args.teams_from)
    script = dict(L.GENERIC)
    script.update(L.per_team_lines(teams))

    os.makedirs(CLIPS_DIR, exist_ok=True)
    clips = {}
    for i, (key, text) in enumerate(script.items(), 1):
        print(f"[{i}/{len(script)}] {key}: {text!r}")
        try:
            audio = tts(text, args.voice_id, args.model_id)
        except urllib.error.HTTPError as e:
            sys.exit(f"ElevenLabs error on '{key}': {e.code} {e.reason} — "
                     f"{e.read().decode(errors='replace')[:200]}")
        if args.fx:
            audio = arena_fx(audio)
        with open(os.path.join(CLIPS_DIR, key + ".mp3"), "wb") as f:
            f.write(audio)
        clips[key] = "data:audio/mpeg;base64," + base64.b64encode(audio).decode()

    with open(OUT_JS, "w") as f:
        f.write("/* Auto-generated by tournament/voice/bake.py — do not edit. */\n")
        f.write("window.__VOICE_CLIPS__ = " + json.dumps(clips) + ";\n")
    # manifest WITHOUT audio/keys (safe to commit): which lines exist + text
    with open(os.path.join(CLIPS_DIR, "manifest.json"), "w") as f:
        json.dump({"voice_id": args.voice_id, "model_id": args.model_id,
                   "fx": args.fx, "lines": script}, f, indent=2)
    kb = sum(len(v) for v in clips.values()) / 1024
    print(f"\nbaked {len(clips)} clips -> {OUT_JS} ({kb:.0f} KB inlined)")
    print("now run: python3 tournament/visual/build_arena.py")


if __name__ == "__main__":
    main()
