"""Artifact schema stamps (v2): recordings open with a metadata header line,
results/manifest JSON carry schema_version + run_meta. Readers must keep
accepting unstamped v0/v1 inputs (the frozen 2026-07-09 artifacts).

engine_sha hashes engine/ source contents (not git) so kit users without git
still get a meaningful fingerprint for reproducibility disputes.
"""

import hashlib
import os
import platform

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RECORDING_SCHEMA = 2
RESULTS_SCHEMA = 2

_ENGINE_SHA = None


def engine_sha():
    global _ENGINE_SHA
    if _ENGINE_SHA is None:
        h = hashlib.sha256()
        eng = os.path.join(ROOT, "engine")
        for dirpath, dirnames, filenames in sorted(os.walk(eng)):
            dirnames.sort()
            for name in sorted(filenames):
                if name.endswith(".py"):
                    rel = os.path.relpath(os.path.join(dirpath, name), eng)
                    h.update(rel.encode())
                    with open(os.path.join(dirpath, name), "rb") as f:
                        h.update(f.read())
        _ENGINE_SHA = h.hexdigest()[:12]
    return _ENGINE_SHA


def recording_header(seed, map_kw, label, event_id=None):
    """First line of every v2 recording .jsonl. Has no 'robots' key, which is
    how readers (replay CLI, viewer) tell it apart from a frame."""
    return {"schema": RECORDING_SCHEMA, "kind": "rw-recording",
            "engine_sha": engine_sha(), "python_version": platform.python_version(),
            "seed": seed, "map": map_kw, "label": label, "event_id": event_id}


def run_meta():
    return {"engine_sha": engine_sha(), "python_version": platform.python_version()}
