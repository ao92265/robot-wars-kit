# Announcer voice pack (P3)

Pre-rendered, Mortal-Kombat-style announcer clips that the 3D arena triggers on
live game events. Baked at build time, inlined into `arena.html`, played fully
**offline** on stage. The arena falls back to the browser's built-in speech when
no pack is baked, so it always works.

## One-time, pre-show

```bash
# 1. Key in the shell only — NEVER commit it; rotate it after the event.
!  export ELEVENLABS_API_KEY=...

# 2. (optional) find a deep "epic announcer" voice and set it
python3 -m tournament.voice.bake --list-voices
!  export ELEVENLABS_VOICE_ID=<voice_id>

# 3. Bake. Generic lines always; add the locked team list for per-name calls.
python3 -m tournament.voice.bake --teams "Red Bots,Blue Crew,Team Vortex" --fx
#   or pull names from an ingest dir:
python3 -m tournament.voice.bake --teams-from submissions --fx

# 4. Re-bundle the single-file arena (inlines the clips as data: URIs)
python3 tournament/visual/build_arena.py
```

Then open `tournament/visual/arena.html`, click **🔊 Voice**, and the announcer
plays on: match start (`fight`), each elimination (`elim_<team>` → `eliminated`),
the deciding blow (`final_blow`), and the winner (`flawless` / `win_<team>` /
`winner`).

## Files
- `lines.py` — the line script (generic + per-team templates) + voice/model config.
- `bake.py` — calls the ElevenLabs REST API (stdlib `urllib`, no pip), optional
  ffmpeg "arena" post (pitch + reverb), writes `clips/*.mp3` + emits
  `../visual/src/voice_clips.js` for the bundler.
- `clips/` — generated mp3s (gitignored).

## Security
- The API key is read from `ELEVENLABS_API_KEY` only. It is never written to a
  file, the emitted JS, the manifest, or a commit.
- The key shared in chat is exposed — **rotate it after the event.**
- Don't rip real Mortal Kombat audio (WB/NetherRealm copyright); recreate the
  vibe with a licensed AI voice.
