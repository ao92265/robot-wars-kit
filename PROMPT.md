# Paste this into Claude Code to build your robot

> You're helping me build a robot for a live 1v1 battle. Edit **only `my_bot.py`** — never touch `engine/`.
>
> First read `CUSTOMIZE.md` (the full contract), then `GUIDELINES.md` and the current `my_bot.py`.
> The `examples/` folder has four working builds — steal ideas from them freely.
> The contract: `LOADOUT` spends ≤12 points (max 6 per stat) across `hp, speed, damage, range,
> special, agility`, plus three free picks — `size` ("small"|"medium"|"large"),
> `gun` ("laser"|"cannon"|"shotgun"), `engine` ("standard"|"sprint"|"tank"|"hover").
> `APPEARANCE` sets my colours. `decide(view)` returns one action dict per tick.
> The arsenal: my gun, rockets (3, splash, dodgeable), mines (5, proximity), dash.
> Physics that matter: shots can MISS (worse at range / vs fast targets / while snap-turning),
> guns can JAM (~2s, cannon worst), blasts can FLIP a robot helpless (`view.enemies[0].flipped`
> = attack window), and powerup crates (overdrive/shield/haste) sit on the big maps.
> Use only the `view` fields and action keys listed in CUSTOMIZE.md — never invent new ones.
>
> My strategy idea: **[describe it in one sentence — e.g. "a small agile shotgun bot that dashes
> in when the enemy's cannon is reloading or jammed", "a tank that mines the middle and camps the
> repair crate", or "the cheesiest legal exploit you can find"]**.
>
> Then:
> 1. Propose a budget-legal `LOADOUT` (stats + size/gun/engine) that fits my strategy.
>    Explain the trade-off in one line — including what my build LOSES to.
> 2. Implement `decide(view)` for it. Keep it short and readable. Handle walls (steer around
>    them), incoming rockets (dodge), and flipped enemies (punish).
> 3. Run `python3 arena.py --vs sniper --fast --best-of 20` and tell me the win-rate;
>    then the same vs `bomber` and `chaser`.
> 4. If any matchup is under 50%, suggest and apply ONE concrete improvement, then re-run.
>    Repeat until I say stop.
>
> Never invent `view` fields or action types. If unsure, re-read CUSTOMIZE.md.
