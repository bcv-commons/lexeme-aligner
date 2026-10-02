"""Re-run compact-alignments for a given list of edition tags (JSON list), e.g. the editions whose `_token_spans` disagreed
with `tokenize` before the 2026-10-01 fix (their `bonus` sidecar indexes were shifted). Own state file, resumable, workers.

    .venv/bin/python pipeline/scripts/adhoc/regen_compact_for_tags.py TAGS.json [--workers 2]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))
from lexeme_aligner.gapfill_batch import USJ_DIR_OVERRIDES  # noqa: E402

STATE = REPO / "pipeline/work/logs/regen_compact_for_tags_state.json"
METHODS = "spanext,eflomal,gloss,gapfill"
PY = str(REPO / ".venv/bin/python3")
MIN_FREE_GB = 15.0


def main() -> int:
    tags = [t.lower() for t in json.loads(Path(sys.argv[1]).read_text())]
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 2
    cm = json.loads((REPO / "publish/compact-alignments/manifest.json").read_text())["languages"]
    pairs = [(iso, e["tag"]) for iso, lang in cm.items() for e in lang.get("editions", {}).values()
             if e.get("tag", "").lower() in tags]
    st = json.loads(STATE.read_text()) if STATE.exists() else {"done": [], "failed": []}
    todo = [p for p in pairs if f"{p[0]}/{p[1]}" not in st["done"]]
    print(f"[regen-compact] {len(tags)} tags -> {len(pairs)} (language, edition) pairs, {len(todo)} to do", file=sys.stderr)
    lock = threading.Lock()

    def one(n: int, iso: str, tag: str) -> None:
        if shutil.disk_usage(REPO).free / 1024 ** 3 < MIN_FREE_GB:
            print(f"[regen-compact] STOP: low disk before {iso}/{tag}", file=sys.stderr)
            return
        usj = REPO / "pipeline/work/ingest-cache" / USJ_DIR_OVERRIDES.get(tag, f"usj-{tag}")
        t0 = time.time()
        rc = subprocess.run([PY, "-m", "lexeme_aligner.compact_align", "--iso", tag, "--publish-iso", iso, "--usj-dir", str(usj),
                             "--publish", "publish/compact-alignments", "--methods", METHODS], cwd=str(REPO)).returncode
        with lock:
            (st["done"] if rc == 0 else st["failed"]).append(f"{iso}/{tag}")
            STATE.write_text(json.dumps(st, indent=1))
        print(f"[regen-compact] {'OK  ' if rc == 0 else 'FAIL'} {n}/{len(todo)} {iso}/{tag} ({time.time() - t0:.0f}s)", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (iso, tag) in enumerate(todo, 1):
            ex.submit(one, n, iso, tag)
    print(f"[regen-compact] done; failed={st['failed']}", file=sys.stderr)
    return 1 if st["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
