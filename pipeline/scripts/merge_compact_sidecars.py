"""Merge compact-alignments' three per-book provenance sidecars into one `<BOOK>_<hash>.meta.json`.

`compact_align.publish_compact` wrote `.method.json`, `.conf.json` and `.contested.json` as three files
per book between 2026-09-03 and 2026-09-04; it now writes a single `.meta.json` carrying the same three
arrays under those keys. This migrates what the earlier form already produced — a pure file
transformation over `publish/compact-alignments/`, no re-alignment, nothing recomputed.

Why it matters: HF caps commits at 128/hour/repo. Three channels put a full-catalog publish at ~1,210
commits (~9.5h of pure rate-limit); one file puts it at ~725 (~5.7h).

Idempotent and safe to re-run: a book already carrying `.meta.json` with no legacy files left is
skipped, and a book carrying both has its legacy files removed only after the merged file verifies
byte-for-byte against them.

    python3 pipeline/scripts/merge_compact_sidecars.py --dry-run     # report, change nothing
    python3 pipeline/scripts/merge_compact_sidecars.py               # migrate

Run it when nothing else is writing the tree — i.e. NOT while the update-all sweep is mid-flight.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

CHANNELS = ("method", "conf", "contested")
ROOT = Path("publish/compact-alignments")


def _legacy(prefix: Path) -> dict[str, Path]:
    """{channel: path} for whichever of the three legacy files exist for this <BOOK>_<hash> prefix."""
    return {c: p for c in CHANNELS if (p := prefix.with_name(f"{prefix.name}.{c}.json")).exists()}


def migrate(root: Path, dry_run: bool) -> Counter:
    n = Counter()
    for align_fp in sorted(root.rglob("*.json")):
        rel = align_fp.relative_to(root)
        if rel.parts[0] == "_index" or align_fp.name.startswith("."):
            continue
        if align_fp.name.count(".") != 1:            # a sidecar/layer, not the alignment file
            continue
        prefix = align_fp.with_suffix("")            # .../<BOOK>_<hash>
        legacy = _legacy(prefix)
        if not legacy:
            n["already merged or no sidecars"] += 1
            continue
        merged = {c: json.loads(legacy[c].read_text(encoding="utf-8")) if c in legacy else []
                  for c in CHANNELS}
        # every channel must be position-parallel to the alignment array, or something is wrong and we
        # leave this book alone rather than write a file that silently misaligns
        length = len(json.loads(align_fp.read_text(encoding="utf-8")))
        if any(a and len(a) != length for a in merged.values()):
            print(f"[merge] SKIP {prefix} — channel length != alignment length", file=sys.stderr)
            n["skipped: length mismatch"] += 1
            continue
        merged = {c: (a if a else [""] * length) for c, a in merged.items()}
        out = prefix.with_name(f"{prefix.name}.meta.json")
        n["merged"] += 1
        n["legacy files removed"] += len(legacy)
        if dry_run:
            continue
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(out)
        for fp in legacy.values():
            fp.unlink()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    n = migrate(args.root, args.dry_run)
    print(f"[merge] {'DRY RUN — ' if args.dry_run else ''}"
          + ", ".join(f"{k}: {v}" for k, v in sorted(n.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
