"""Safe read-modify-write for the shared dataset manifests (publish/<dataset>/manifest.json).

Several processes can finish a language at the same time (a catalog sweep and a repair run, or two batch
workers), and every one of them used to read the manifest, add its own entry and write the whole file back —
so one update could silently overwrite another's. `update_json` holds an exclusive lock on a sidecar
`<name>.lock` file for the whole read-modify-write and replaces the file atomically (temp file + rename), so
a reader never sees a half-written manifest either.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Callable


def update_json(path: Path, mutate: Callable[[dict], None], default: dict | None = None) -> dict:
    """Apply `mutate(doc)` to the JSON document at `path` under an exclusive lock; returns the new document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(default or {})
            mutate(doc)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            return doc
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
