"""Gzip-compress a language's raw out/ jsonl — `make clean-out ISO=xxx`. Opt-in, only ever run
manually (never automatically by full_chain.py or any batch driver) — see the Makefile's top-of-file
note on why this is deferred (aligned_mwe/senses_attested/compact-alignments all need the raw jsonl
too, so only clean once every dataset that needs it has already been exported for this language).

Compresses rather than deletes (2026-09-11) — see full_chain.py's own clean-out block for the full
reasoning: these files are cheap to keep (~1/10th their size gzipped) and get needed again by any
retroactive gapfill/compact-alignments fix. `align_files.tag_files_any_method`/`AlignPath` already
read `.jsonl.gz` transparently, so nothing downstream needs to change."""
from __future__ import annotations

import argparse
import gzip
import shutil
from pathlib import Path

from lexeme_aligner.align_files import tag_files_any_method
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

    compressed = 0
    for tag in tags:
        for fp in tag_files_any_method(Path(OUT), tag):
            if fp.suffix == ".gz":
                continue                       # already compressed
            with fp.open("rb") as src, gzip.open(fp.with_name(fp.name + ".gz"), "wb") as dst:
                shutil.copyfileobj(src, dst)
            fp.unlink()
            compressed += 1
    print(f"[clean-out] '{args.iso}': gzip-compressed {compressed} raw jsonl file(s) for tag(s) "
          f"{sorted(tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
