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


def roster_from_submissions(path):
    """(team_names, bot_labels) from an ingest manifest. Grouping and the
    "Team:Bot" label are taken from tournament.bracket so the baked keys can't
    drift from what the engine actually emits into the frames."""
    man = os.path.join(path, "manifest.json")
    if not os.path.exists(man):
        return [], []
    with open(man) as f:
        m = json.load(f)
    accepted = m.get("accepted", m.get("entries", m if isinstance(m, list) else []))
    from ..bracket import teams_from_accepted
    teams = teams_from_accepted([e for e in accepted if isinstance(e, dict)])
    labels = []
    for t in teams:
        for b in sorted(t["bots"], key=lambda b: str(b.get("bot", b["name"]))):
            labels.append(f"{t['name']}:{b.get('bot', b['name'])}")
    return [t["name"] for t in teams], labels


def sample_labels():
    """"Team:Bot" labels in the bundled demo reel. arena.html ships with that
    match, so bake its bots too or the preview has no per-bot voice at all."""
    path = os.path.join(ROOT, "tournament", "visual", "samples", "demo.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        for line in f:                       # first frame carries the full roster
            robots = json.loads(line).get("robots")
            if robots:
                return [r["name"] for r in robots if r.get("name")]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--teams", default="", help="comma-separated team names")
    ap.add_argument("--teams-from", default="", help="submissions dir to read names from")
    ap.add_argument("--fx", action="store_true", help="apply ffmpeg arena post")
    ap.add_argument("--no-demo", action="store_true", help="skip the bundled demo-reel bots")
    ap.add_argument("--voice-id", default=os.environ.get("ELEVENLABS_VOICE_ID", L.DEFAULT_VOICE_ID))
    ap.add_argument("--model-id", default=os.environ.get("ELEVENLABS_MODEL_ID", L.DEFAULT_MODEL_ID))
    args = ap.parse_args()

    if args.list_voices:
        list_voices(); return

    # assemble the line set: generic + per-team + per-bot
    teams = [t.strip() for t in args.teams.split(",") if t.strip()]
    labels = []
    if args.teams_from:
        t2, labels = roster_from_submissions(args.teams_from)
        teams += t2
    if not args.no_demo:
        for lb in sample_labels():
            if lb not in labels:
                labels.append(lb)
                t = lb.split(":", 1)[0]
                if t not in teams:
                    teams.append(t)
    script = dict(L.GENERIC)
    script.update(L.per_team_lines(teams))
    script.update(L.per_bot_lines(labels))
    script.update(L.entrance_lines(labels))

    os.makedirs(CLIPS_DIR, exist_ok=True)

    # Incremental: an existing clip whose text AND voice/model/fx are unchanged
    # is already correct on disk. Re-rendering it costs credits and changes
    # nothing, so reuse the mp3 and only pay for genuinely new lines.
    prev = {}
    man_path = os.path.join(CLIPS_DIR, "manifest.json")
    if os.path.exists(man_path):
        with open(man_path) as f:
            pm = json.load(f)
        if (pm.get("voice_id") == args.voice_id and pm.get("model_id") == args.model_id
                and bool(pm.get("fx")) == bool(args.fx)):
            prev = pm.get("lines", {})

    clips, by_text, reused = {}, {}, 0
    for i, (key, text) in enumerate(script.items(), 1):
        cached = os.path.join(CLIPS_DIR, key + ".mp3")
        if prev.get(key) == text and os.path.exists(cached):
            with open(cached, "rb") as f:
                audio = f.read()
            clips[key] = "data:audio/mpeg;base64," + base64.b64encode(audio).decode()
            by_text.setdefault(text, (key, clips[key]))
            reused += 1
            continue
        if text in by_text:                     # same words, same audio — don't pay twice
            print(f"[{i}/{len(script)}] {key}: reuse of {by_text[text][0]!r}")
            clips[key] = by_text[text][1]
            continue
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
        by_text[text] = (key, clips[key])

    with open(OUT_JS, "w") as f:
        f.write("/* Auto-generated by tournament/voice/bake.py — do not edit. */\n")
        f.write("window.__VOICE_CLIPS__ = " + json.dumps(clips) + ";\n")
    # manifest WITHOUT audio/keys (safe to commit): which lines exist + text
    with open(os.path.join(CLIPS_DIR, "manifest.json"), "w") as f:
        json.dump({"voice_id": args.voice_id, "model_id": args.model_id,
                   "fx": args.fx, "lines": script}, f, indent=2)
    kb = sum(len(v) for v in clips.values()) / 1024
    print(f"\nbaked {len(clips)} clips ({reused} reused from disk) -> {OUT_JS} ({kb:.0f} KB inlined)")
    print("now run: python3 tournament/visual/build_arena.py")


if __name__ == "__main__":
    main()
