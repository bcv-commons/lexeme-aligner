"""Publish ONE already-exported language's local files, WITHOUT re-aggregating from out/ — so this
still works even after that language's raw jsonl was cleaned up (--clean-out). Reads each dataset's own
manifest.json entry for the iso and pushes exactly that file.

Covers: lexeme-alignments, aligned_mwe, senses_attested — the three datasets that are genuinely ONE
parquet per language, via export_lex.publish_to_hf (same manifest-entry pattern for all three).

NOT covered (no per-language publish path exists for these — each pushes its ENTIRE local tree every
time by design, so isolating one language's HF diff isn't possible): compact-alignments (many small
per-book files sharing a common `_index/`), target-stopwords, target-morphology (bulk-manifest-per-call
datasets). Use `make publish-all` for those.

    python3 pipeline/scripts/publish_lang.py --iso ceb --create
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lexeme_aligner.export_lex import publish_to_hf

_DATASETS = [
    ("publish/lexeme-alignments", "bcv-commons/lexeme-alignments"),
    ("publish/aligned_mwe", "bcv-commons/aligned-mwe"),
    ("publish/senses_attested", "bcv-commons/senses-attested"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for root_str, repo_id in _DATASETS:
        root = Path(root_str)
        manifest_fp = root / "manifest.json"
        if not manifest_fp.exists():
            continue
        manifest = json.loads(manifest_fp.read_text(encoding="utf-8"))
        entry = manifest.get("languages", {}).get(args.iso)
        if not entry:
            print(f"[publish_lang] {root_str}: no entry for '{args.iso}' — skipping", file=sys.stderr)
            continue
        rel_file = entry["file"]
        publish_to_hf(root, args.iso, rel_file, entry, repo_id, args.create, args.dry_run)

    print(f"[publish_lang] '{args.iso}' done. Note: compact-alignments/target-stopwords/"
          f"target-morphology have no per-language publish path — use `make publish-all` for those.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
