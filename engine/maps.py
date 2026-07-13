"""Arena map presets — size + static wall layout.

Walls are (x, y, w, h) rectangles, top-left origin, same format as
config.WALLS. Everything here is deterministic (no RNG) so replays stay
byte-exact. Pick a map with `arena.py --map NAME`, or pass
`Game(..., width=m["w"], height=m["h"], walls=m["walls"])`.

Bigger maps = more room to kite, more cover, more robots on screen — the
showcase arenas for the big screen.
"""


def _bar(cx, cy, w, h):
    """Rect centred on (cx, cy)."""
    return (cx - w / 2, cy - h / 2, w, h)


def _classic(w, h):
    return [
        _bar(w * 0.5, h * 0.5, 200, 36),    # centre bar
        _bar(w * 0.26, h * 0.28, 36, 140),  # left pillar
        _bar(w * 0.74, h * 0.72, 36, 140),  # right pillar
    ]


def _arena(w, h):
    walls = [
        _bar(w * 0.5, h * 0.5, 40, 40),                 # centre block
        _bar(w * 0.5 - 180, h * 0.5, 40, 220),          # inner left wall
        _bar(w * 0.5 + 180, h * 0.5, 40, 220),          # inner right wall
    ]
    for sx in (0.2, 0.8):                               # 4 corner bunkers (L-shapes)
        for sy in (0.22, 0.78):
            walls.append(_bar(w * sx, h * sy, 150, 36))
            walls.append(_bar(w * sx + (40 if sx < 0.5 else -40), h * sy + (40 if sy < 0.5 else -40), 36, 110))
    return walls


def _colosseum(w, h):
    # A ring arena built AROUND a lethal centre: no wall in the middle (that's a
    # lava pool now), a ring of pillars for cover, and mid-edge bars. Terrain — not
    # walls — is the centrepiece, so bots must fight around the hazards.
    walls = []
    for sx in (0.32, 0.68):                 # ring of 4 pillars hugging the lava pool
        for sy in (0.32, 0.68):
            walls.append(_bar(w * sx, h * sy, 64, 64))
    walls += [                              # mid-edge cover (bridgeheads onto the water flanks)
        _bar(w * 0.5, h * 0.16, 240, 36),
        _bar(w * 0.5, h * 0.84, 240, 36),
        _bar(w * 0.14, h * 0.5, 36, 200),
        _bar(w * 0.86, h * 0.5, 36, 200),
    ]
    return walls


def _gauntlet(w, h):
    walls = []
    for i, fx in enumerate((0.28, 0.5, 0.72)):          # staggered lane dividers
        off = 0.16 if i % 2 == 0 else -0.16
        walls.append(_bar(w * fx, h * (0.5 + off), 40, h * 0.5))
    walls += [_bar(w * 0.5, h * 0.5, 160, 36)]
    return walls


def _pillars(w, h):
    walls = []
    for gx in (0.25, 0.5, 0.75):                        # 3x3 grid minus centre
        for gy in (0.25, 0.5, 0.75):
            if gx == 0.5 and gy == 0.5:
                continue
            walls.append(_bar(w * gx, h * gy, 64, 64))
    return walls


def _hz(t, cx, cy, w, h):
    """A typed hazard rect centred on (cx, cy)."""
    return {"type": t, "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}


def _no_hazards(w, h):
    return []


def _no_pickups(w, h):
    return []


def _colosseum_hazards(w, h):
    # A themed, dynamic floor: a lethal LAVA pool owns the centre, twin WATER
    # channels bog the left/right flanks, ICE guards the top/bottom approaches,
    # and PITs punish the diagonals. Every route to the enemy crosses terrain.
    return [
        # CENTRE LEFT OPEN — the brawl pit where chargers from both ends collide.
        _hz("lava",  w * 0.30, h * 0.30, 150, 150),   # flanking molten pools (diagonal), not a centre wall
        _hz("lava",  w * 0.70, h * 0.70, 150, 150),
        _hz("water", w * 0.14, h * 0.50, 120, 340),   # far-flank wading channels
        _hz("water", w * 0.86, h * 0.50, 120, 340),
        _hz("ice",   w * 0.50, h * 0.16, 300, 110),   # slippery top approach
        _hz("ice",   w * 0.50, h * 0.84, 300, 110),   # slippery bottom approach
        _hz("pit",   w * 0.30, h * 0.70, 90, 90),      # diagonal pits opposite the lava
        _hz("pit",   w * 0.70, h * 0.30, 90, 90),
        # the open centre lane bites back: a launch paddle at one end and a
        # spinning turntable at the other
        _hz("flipper", w * 0.5, h * 0.65, 90, 90),
        _hz("turntable", w * 0.5, h * 0.35, 150, 150),
    ]


def _colosseum_pickups(w, h):
    return [
        {"x": w * 0.20, "y": h * 0.22, "kind": "rockets"},   # reward wading into the water channels
        {"x": w * 0.80, "y": h * 0.78, "kind": "traps"},
        {"x": w * 0.32, "y": h * 0.32, "kind": "repair"},    # tucked by the corner pillars
        {"x": w * 0.68, "y": h * 0.68, "kind": "repair"},
        # powerups: dead centre = the brawl pit's prize; flanks reward map control
        {"x": w * 0.50, "y": h * 0.50, "kind": "overdrive"},
        {"x": w * 0.50, "y": h * 0.15, "kind": "shield"},
        {"x": w * 0.50, "y": h * 0.85, "kind": "haste"},
    ]


def _arena_hazards(w, h):
    return [
        _hz("lava", w * 0.5, h * 0.22, 200, 70),
        _hz("lava", w * 0.5, h * 0.78, 200, 70),
        _hz("pit",  w * 0.2, h * 0.5,  80, 80),
        _hz("pit",  w * 0.8, h * 0.5,  80, 80),
        _hz("ice",  w * 0.5, h * 0.5,  220, 220),
        _hz("flipper", w * 0.32, h * 0.5, 90, 90),      # launch paddle west of the rink
        _hz("turntable", w * 0.68, h * 0.5, 150, 150),  # spinning platter east of it
    ]


def _arena_pickups(w, h):
    return [
        {"x": w * 0.5,  "y": h * 0.5,  "kind": "rockets"},
        {"x": w * 0.2,  "y": h * 0.22, "kind": "repair"},
        {"x": w * 0.8,  "y": h * 0.78, "kind": "traps"},
        {"x": w * 0.8,  "y": h * 0.22, "kind": "overdrive"},
        {"x": w * 0.2,  "y": h * 0.78, "kind": "haste"},
    ]


# name -> (w, h, walls_fn, hazards_fn, pickups_fn). Small maps stay clean.
_DEFS = {
    "classic":   (1280, 768, _classic, _no_hazards, _no_pickups),
    "arena":     (1600, 1000, _arena, _arena_hazards, _arena_pickups),
    "colosseum": (1920, 1152, _colosseum, _colosseum_hazards, _colosseum_pickups),
    "gauntlet":  (1792, 820, _gauntlet, _no_hazards, _no_pickups),
    "pillars":   (1536, 1024, _pillars, _no_hazards, _no_pickups),
}


# Maps that get the GATEKEEPER house robot (see engine/house.py). Empty for the
# event: the patroller only ever hassles whoever spawns nearest — unfair in 1v1.
_HOUSE_MAPS = set()


def get(name):
    """Return {'name','w','h','walls','hazards','pickups','house'} for a preset."""
    w, h, fn, hfn, pfn = _DEFS[name]
    return {"name": name, "w": w, "h": h, "walls": fn(w, h),
            "hazards": hfn(w, h), "pickups": pfn(w, h),
            "house": name in _HOUSE_MAPS}


def names():
    return list(_DEFS)
