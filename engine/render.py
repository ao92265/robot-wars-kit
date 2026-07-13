"""ASCII arena renderer — the zero-dependency fallback so `python3 arena.py` runs
with no pip install. Locked engine file."""

COLS = 64
ROWS = 22


def _gx(x, arena_w):
    return min(COLS - 1, max(0, int(x / arena_w * COLS)))


def _gy(y, arena_h):
    return min(ROWS - 1, max(0, int(y / arena_h * ROWS)))


def render_frame(frame, arena_w, arena_h, names):
    grid = [[" "] * COLS for _ in range(ROWS)]
    # walls first (so other glyphs draw over them)
    for (wx, wy, ww, wh) in frame["status"].get("walls", []):
        for gy in range(_gy(wy, arena_h), _gy(wy + wh, arena_h) + 1):
            for gx in range(_gx(wx, arena_w), _gx(wx + ww, arena_w) + 1):
                grid[gy][gx] = "#"
    # mines '^', rockets 'o'
    for mn in frame.get("mines", []):
        grid[_gy(mn["y"], arena_h)][_gx(mn["x"], arena_w)] = "^"
    for rk in frame.get("rockets", []):
        grid[_gy(rk["y"], arena_h)][_gx(rk["x"], arena_w)] = "o"
    for rb in frame["robots"]:
        if not rb["alive"]:
            continue
        grid[_gy(rb["y"], arena_h)][_gx(rb["x"], arena_w)] = rb["name"][0].upper()
    # laser beams hit-point '*'
    pos = {rb["id"]: (rb["x"], rb["y"]) for rb in frame["robots"]}
    for f in frame["fired"]:
        if f["hit"] and f["t"] in pos:
            tx, ty = pos[f["t"]]
            gx, gy = _gx(tx, arena_w), _gy(ty, arena_h)
            if grid[gy][gx] == " ":
                grid[gy][gx] = "*"
    # explosions 'X'
    for ex in frame.get("explosions", []):
        grid[_gy(ex["y"], arena_h)][_gx(ex["x"], arena_w)] = "X"
    top = "+" + "-" * COLS + "+"
    lines = [top] + ["|" + "".join(row) + "|" for row in grid] + [top]
    # HP bars
    for rb in frame["robots"]:
        bar_len = 20
        filled = int(bar_len * rb["hp"] / max(1, rb["max_hp"]))
        bar = "#" * filled + "." * (bar_len - filled)
        status = "" if rb["alive"] else "  (OUT)"
        lines.append(f"  {rb['name'][:14]:<14} [{bar}] {rb['hp']:>3}/{rb['max_hp']}{status}")
    lines.append(f"  tick {frame['tick']}   alive {frame['status']['alive']}   "
                 f"time_left {frame['status']['time_left']}")
    return "\n".join(lines)
