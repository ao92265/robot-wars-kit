"""Schema-header tests: v2 recordings open with a metadata line, results carry
schema_version + run_meta, and readers tolerate v1 (unstamped) + truncated files.
Run from repo root:  python3 tests/test_recmeta.py"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tournament.recmeta import RECORDING_SCHEMA, engine_sha, recording_header
from tournament.runner import run_match
from engine.dummies import sitting_duck, chaser
import arena

FAILS = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def main():
    check("engine sha is stable", engine_sha() == engine_sha() and len(engine_sha()) == 12)
    h = recording_header(7, "classic", "X")
    check("header carries schema+seed+map+python", h["schema"] == RECORDING_SCHEMA
          and h["seed"] == 7 and h["map"] == "classic" and "python_version" in h)
    check("header has no robots key (frame discriminator)", "robots" not in h)

    tmp = tempfile.mkdtemp(prefix="rw_recmeta_test_")
    try:
        duck = os.path.join(ROOT, "engine", "dummies", "sitting_duck.py")
        chas = os.path.join(ROOT, "engine", "dummies", "chaser.py")
        bots = [{"name": "duck", "path": duck, "loadout": sitting_duck.LOADOUT},
                {"name": "chaser", "path": chas, "loadout": chaser.LOADOUT}]
        r = run_match(bots, 3, "META", tmp)

        with open(r["jsonl"]) as f:
            first = json.loads(f.readline())
        check("recording starts with the metadata header",
              first.get("schema") == RECORDING_SCHEMA and first["seed"] == r["seed"])

        frames = arena._read_frames(r["jsonl"])
        check("replay reader skips the header", frames and "robots" in frames[0])

        # v1 (unstamped) recordings still load
        v1 = os.path.join(tmp, "v1.jsonl")
        with open(r["jsonl"]) as src, open(v1, "w") as dst:
            src.readline()  # drop header
            shutil.copyfileobj(src, dst)
        f1 = arena._read_frames(v1)
        check("v1 unstamped recording still loads", len(f1) == len(frames))

        # truncated file: half-written last line must not traceback
        trunc = os.path.join(tmp, "trunc.jsonl")
        with open(r["jsonl"]) as src:
            lines = src.readlines()
        with open(trunc, "w") as dst:
            dst.writelines(lines[:10])
            dst.write(lines[10][:25])  # torn mid-JSON
        ft = arena._read_frames(trunc)
        check("truncated recording loads the intact prefix", len(ft) == 9)

        # results carry the stamp
        check("match result seed matches header", r["seed"] == first["seed"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all recmeta tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
