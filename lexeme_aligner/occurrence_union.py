"""Union the per-source occurrence-alignment extractions (gbt_align.py, bsb_align.py) into one file
per language — the additive-union / provenance-honest pattern already used for `lexeme-alignments`
(docs/publishing-principles.md §3, §5): each source's rows are kept and tagged (`source` = gbt/bsb),
never merged or picked-a-winner. Concatenation only — the two extractors already emit the identical
row schema (source_ids, source_text, source_strong, target_ids, target_gloss, kind, verse_ref, lang,
source), so nothing needs reshaping.

CAVEAT (provenance-honesty, read before joining across sources): `source_ids`/`target_ids` are only
interpretable WITHIN a row's own `source` — gbt's ids are its own `verse_ref*100+word_num` address
scheme over MACULA-tokenized (prefix-split) source words; BSB's ids are plain 0-based within-verse
indices over whole-word tokenization. `verse_ref` is the one field that's directly comparable across
sources (both match lexeme_aligner.refs.encode()).

    python3 -m lexeme_aligner.occurrence_union --lang eng
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_DIR = Path("resources/occurrence_align")


def union_lang(lang: str, dir_: Path = _DIR) -> list[dict]:
    rows = []
    for fp in sorted(dir_.glob(f"*_{lang}.jsonl")):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["verse_ref"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--dir", type=Path, default=_DIR)
    args = ap.parse_args()

    rows = union_lang(args.lang, args.dir)
    if not rows:
        raise SystemExit(f"[occurrence_union] no <source>_{args.lang}.jsonl files found in {args.dir}")

    by_source = collections.Counter(r["source"] for r in rows)
    out_fp = args.dir / f"{args.lang}.jsonl"
    with out_fp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[occurrence_union] {args.lang}: {len(rows)} rows -> {out_fp}\n"
          f"  by source: {dict(by_source)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
