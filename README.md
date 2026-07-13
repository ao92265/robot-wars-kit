# Robot Wars — AI build-off

Design a robot, code its brain with Claude Code, then watch the robots fight 1v1 on the
big screen. Built for the Harris all-hands.

> ## ⛔ HOLD FIRE — don't build yet
> You're getting this repo early so you can check your setup works, **nothing more**.
> Run the 60-second start below and confirm you see the practice fight — then stop.
> **Bot-building starts ON THE DAY**, when the task briefing lands and every team gets
> the same clock. Head starts get you gently mocked on the big screen.

## Why we're doing this
This is a **Claude Code workshop wearing a robot costume**. The loop you'll practice is
the real skill: describe an idea in plain English → let the agent build it → **test it**
→ read what actually happened → refine. The game is deliberately full of trade-offs and
chaos (shots miss, guns jam, robots get flipped) so the winning move is never "max the
stats" — it's *think outside the box, then verify*. In our testing the biggest, tankiest
build loses 7 times out of 10 to a mid-size bot with a smarter plan.

## 60-second start
Unzip this folder anywhere, then in a terminal:
```
cd robot-wars
python3 arena.py        # Windows: python arena.py
```
That runs the starter robot against the practice dummies and renders the fight in your terminal.
**No install** — pure Python standard library, no `pip`. Any recent Python 3 works.
**Windows:** the command is `python`, not `python3` (no Python at all? Install "Python 3.12"
free from the Microsoft Store — no admin rights needed — then open a *new* terminal).
Use Windows Terminal or the VS Code terminal (the old cmd.exe mangles the display).

## You only edit one file: `my_bot.py`
- `LOADOUT` — spend 12 points (hp / speed / damage / range / special / **agility**) + three
  free archetype picks: `size` (small/medium/large), `gun` (**laser / cannon / shotgun**),
  `engine` (**standard / sprint / tank / hover**)
- `APPEARANCE` — your colours (+ accent stripe) and spinner shape on the big 3D screen (cosmetic)
- `decide(view)` — your strategy, called once per tick

Arsenal: your **gun** (archetype-dependent), **rockets** (travel + splash, 3, dodgeable, can FLIP),
**mines** (proximity, 5, can FLIP), **dash**. Powerup crates on the big maps (⚡ overdrive ·
🛡 shield · 💨 haste). Cover walls block guns and rockets. Shots can miss, guns can jam,
blasts can flip you wheels-up. The real game: find the dirtiest *legal* strategy.

Quick reference: **`GUIDELINES.md`** · every knob + trade-off table: **`CUSTOMIZE.md`** ·
copyable builds: **`examples/`** · have Claude Code build it for you: **`PROMPT.md`**.

## Commands
| Command | Does |
|---|---|
| `python3 arena.py` | starter vs all dummies, animated |
| `python3 arena.py --vs sniper` | fight one dummy (`duck`, `chaser`, `sniper`, `bomber`, `trapper`) |
| `python3 arena.py --fast` | skip animation, just the result |
| `python3 arena.py --vs sniper --best-of 20` | win-rate over N matches |
| `python3 arena.py --check` | is my loadout legal? |
| `python3 arena.py --submit "Team"` | hand in `my_bot.py` |
| `python3 arena.py --replay match.jsonl` | replay a recorded match |
| `python3 arena.py --map colosseum` | test on a bigger arena (`classic`, `arena`, `colosseum`, `gauntlet`, `pillars`) |

The bigger maps bite back: lava, water, ice, pits, powerup crates — and on `colosseum`
and `arena`, a floor **flipper** that hurls robots wheels-up and a spinning **turntable**.
Your bot sees every hazard in `view.arena.hazards`; dodging (or abusing) them is legal.

On the day, the organiser gives you a submit URL — `export ROBOT_WARS_SUBMIT_URL=http://<host>:8000`
then `--submit` uploads over the network (or just paste your bot at that URL in a browser).

## For organisers (running the tournament)
```
# 1. collect submissions (pick ONE that fits the venue network):
#    a) submit server (no shared drive needed) — teams submit over the LAN or a browser:
python3 -m tournament.submit_server            # prints the URL to give teams
#    b) OR a shared drive: tell everyone `export ROBOT_WARS_DROP=/path/to/share`
# 2. validate + run the whole bracket, recording every match:
python3 -m tournament.runner submissions --auto --seed 1
# 3. replay any match on the big screen:
open tournament/visual/arena.html      # then load a recordings/*.jsonl (or drag it in)
```
- **Balance/tuning:** every game knob lives in `engine/config.py` (budget, stat curves, speeds, falloff,
  arena size). Tune it in the playtest — no other code changes needed.
- `--auto` runs unattended (use it for the morning-of dry-run); drop it to pace matches with Enter on stage.
- Scales by entry count: ≤8 → one royale; more → seeded heats of 8 (top 2 advance) → final.
- **Stage plan:** matches run in <1s headless and record to `submissions/recordings/*.jsonl`. Pre-run the whole
  tournament in the morning, then on stage replay the decisive heats + the final from those recordings. The recordings
  ARE the fallback — if anything misbehaves live, you still have every battle.
- Isolation: each bot runs in its own subprocess; a `while True`, crash, or OOM just idles that bot — never stalls the show.

## Layout
```
my_bot.py            ← the only file you edit
arena.py             ← run / check / submit / replay
GUIDELINES.md        ← 2-minute cheat sheet
CUSTOMIZE.md         ← every knob: stats, guns, engines, mishaps, powerups
examples/            ← four contrasting builds to copy + tweak
PROMPT.md            ← paste into Claude Code
engine/              ← locked: game, view, sandbox, render, geom (walls), dummies
tournament/          ← run by the organisers on the day
tests/               ← engine tests (python3 tests/test_engine.py)
```

Animation needs nothing extra (ASCII). `pygame` is optional and only used by organisers.
