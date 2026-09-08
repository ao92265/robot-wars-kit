# Robot Wars — Organiser Runbook

You are running the show. The author is **not** in the room, and that's fine —
everything below is rehearsed, resumable, and recorded. One machine runs the
whole event; venue internet is never required.

## T-minus checklist

**T-7 days**
- [ ] `python3 -m tournament.doctor` on the ACTUAL machine you'll use on stage. Green board or fix now.
- [ ] `python3 -m tournament.event rehearse dress-rehearsal` — full dry-run on the example bots
      (create → snapshot → tournament → closeout bundle). Delete `events/dress-rehearsal/` after.
- [ ] Distribute the kit (clone the repo, or the zip from the latest release if one is
      cut) + announce the build window.

**T-1 day**
- [ ] `python3 -m tournament.event create <event-id> --format ffa|bracket --seed <N>`
      (`ffa` = heats + final on colosseum; `bracket` = 1v1 team knockout).
- [ ] `python3 -m tournament.event register <event-id> "Team A" "Team B" ...` — hand each
      team its token PRIVATELY (chat DM, paper). Tokens are shown once; hashes only on disk.
- [ ] `python3 -m tournament.doctor` again. Charge the laptop. Disable sleep.

**Event day — the whole show is 5 commands**
```
python3 -m tournament.event serve   <event-id>          # 1. teams submit (browser or CLI)
#    watch http://<host>:<port>/status for teams received / rejects
python3 -m tournament.event snapshot <event-id>         # 2. CLOSE submissions (immutable)
python3 -m tournament.event run     <event-id>          # 3. run it (Enter paces each match;
                                                        #    --auto for the morning dry-run)
#    replay any recordings/run-*/<match>.jsonl in tournament/visual/arena.html (big screen)
python3 -m tournament.event closeout <event-id>         # 4. canonical bundle + sha256 manifest
python3 -m tournament.event closeout <event-id> --publish   # 5. GitHub Release event-<id>
```

## Rules the tooling enforces (don't fight them)
- **Submissions can't be lost or overwritten.** Every accepted POST is journaled
  append-only per team with receipts; wrong token → 409 and the old file is untouched.
  `--open` (single-room LAN) drops only the token check — the journal and audit log stay on.
- **The snapshot is the roster.** After `snapshot`, late submissions don't exist for this
  event. Never hand-edit `snapshot/` — closeout verifies its hashes and will block.
- **One tournament per machine.** A second run hard-fails on the lock. CPU contention
  changes results; this is a feature.
- **The recording is the result.** Every match's seed is identity-derived and stored in the
  results JSON. Disputes are settled by replaying the recording, never by re-running.

## Failure drills (rehearse each once)
| Symptom | Drill |
|---|---|
| Bot hangs/crashes mid-match | Nothing to do — isolation substitutes its last action and the match continues. Substitutions are logged in the results JSON. |
| Bot rejected at ingest | Read the reason aloud (`snapshot/manifest.json`); it names the import or failure. That team fixes nothing today — snapshot already closed. Their journal keeps every prior version. |
| Submit server machine dies during build window | Restart `serve` — journal + roster are on disk; teams resubmit only what's newer. |
| No LAN / hub unreachable | Teams run `python3 arena.py --submit "Team"` with no URL set → file lands in a local drop; collect the files by USB into `events/<id>/submissions/`. |
| Tournament machine dies mid-show | Restart, then `python3 -m tournament.event run <event-id> --resume` — completed matches reload from the run log; the rest run with identical seeds. |
| Second tournament refuses to start | The lock is at the path printed in the error. If no other run exists, delete the lock file and retry. |
| Viewer misbehaves on stage | Recordings are already on disk. Reload `arena.html`, drag the .jsonl back in. Worst case: `python3 arena.py --replay <file>` (ASCII, always works). |

## Escalation
First two non-author events: the author is the pre-agreed escalation contact for the event
window. From event 3 on, the organiser guild owns it. If you're reading this during an
incident: recordings + run log are your ground truth; nothing that's on disk can be lost by
retrying a command — closeout/`--resume`/`serve` are all idempotent or append-only.
