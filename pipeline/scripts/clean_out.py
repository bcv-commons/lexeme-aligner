"""Delete a language's raw out/ jsonl — `make clean-out ISO=xxx`. Opt-in, only ever run manually
(never automatically by full_chain.py or any batch driver) — see the Makefile's top-of-file note on
why this is deferred (aligned_mwe/senses_attested/compact-alignments all need the raw jsonl too, so
only clean once every dataset that needs it has already been exported for this language)."""
from __future__ import annotations

import argparse
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iso", required=True)
    args = ap.parse_args()

    import json
    manifest_fp = LEX_ROOT / "manifest.json"
    entry = json.loads(manifest_fp.read_text(encoding="utf-8"))["languages"].get(args.iso, {}) \
        if manifest_fp.exists() else {}
    tags = set(entry.get("base_texts", [])) | {args.iso}

    removed = 0
    for tag in tags:
        for fp in Path(OUT).glob(f"align_*_{tag}_*.jsonl"):
            fp.unlink()
            removed += 1
    print(f"[clean-out] '{args.iso}': removed {removed} raw jsonl file(s) for tag(s) {sorted(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
