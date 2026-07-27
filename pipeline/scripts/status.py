"""Coverage report across every dataset — `make status`."""
from __future__ import annotations

import json
import re
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT


def _coverage(root: str) -> int:
    fp = Path(root) / "manifest.json"
    if not fp.exists():
        return 0
    return len(json.loads(fp.read_text(encoding="utf-8")).get("languages", {}))


def main() -> int:
    lex = json.loads((LEX_ROOT / "manifest.json").read_text(encoding="utf-8"))
    isos = lex["languages"]
    methods = {iso: set(e.get("methods", [])) for iso, e in isos.items()}
    full = sum(1 for m in methods.values() if {"eflomal", "gloss", "gapfill"} <= m)
    eflomal_only = sum(1 for m in methods.values() if m == {"eflomal"})

    print(f"lexeme-alignments:      {len(isos)} languages "
          f"({full} full 9-step chain, {eflomal_only} eflomal-only)")
    print(f"aligned_mwe:            {_coverage('publish/aligned_mwe')} languages")
    print(f"senses_attested:        {_coverage('publish/senses_attested')} languages (OT-only)")
    print(f"compact-alignments:     {_coverage('publish/compact-alignments')} languages")
    print(f"target-stopwords:       {_coverage('publish/target-stopwords')} languages")
    print(f"target-morphology:      {_coverage('publish/target-morphology')} languages")

    out_dir = Path(OUT)
    tags = set()
    if out_dir.exists():
        for fp in out_dir.glob("align_*.jsonl"):
            m = re.match(r"^align_[a-z]+_(.+)_[A-Z0-9]+\.jsonl$", fp.name)
            if m:
                tags.add(m.group(1))
    print(f"out/ raw jsonl:         {len(tags)} distinct tag(s) still cached "
          f"(transient — safe to clean once fully exported)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
