"""Eflomal-based versification validator (part b) — an INDEPENDENT check on the structure-fingerprint
detector (versification.detect_scheme).

Aligns the target under each candidate scheme and compares alignment quality **restricted to the Psalms**
— the only OT region where the schemes diverge. This is the RIGHT signal: whole-corpus coverage does NOT
discriminate versification (the wrong scheme can score HIGHER overall by rewarding spurious matches — rus
scored 94.3% coverage under wrong `hebrew` vs 93.6% under correct `septuagint`). Psalm hi-conf does: only
the scheme that lines the Psalter's verses up with the spine gets the intersection-backed (score≥0.9) core.

    python3 -m lexeme_aligner.validate_versification --iso rus --usj-dir data/usj-rus \\
        --schemes protestant,hebrew,septuagint
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lexeme_aligner.gloss_align import NORMALIZERS, Normalizer
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.run_pilot import build_corpus, OT_BOOKS, _hi
from lexeme_aligner.versification import remapper_for_scheme, detect_scheme

_SENSITIVE = "PSA"                                          # the book where schemes actually diverge


def _psalm_quality(recs, eflo) -> tuple[int, int, int]:
    """(content, covered, hi) tallied over the sensitive book only."""
    content = covered = hi = 0
    for rec in recs:
        if rec.book != _SENSITIVE or not rec.toks:
            continue
        by_h = {m.h_idx: m for m in eflo.decode(rec)}
        for t in rec.heb:
            if not (t.is_content and t.strong):
                continue
            content += 1
            m = by_h.get(t.idx)
            if m:
                covered += 1
                if _hi(m):
                    hi += 1
    return content, covered, hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--schemes", default="protestant,hebrew,septuagint",
                    help="candidate scheme labels to compare (default the 3 we hold tables for)")
    ap.add_argument("--books", default=None,
                    help="comma-sep books to ALIGN on (default full OT for lexical statistics); "
                         "quality is always measured on PSA")
    args = ap.parse_args()

    books = ([b.strip().upper() for b in args.books.split(",")] if args.books else OT_BOOKS)
    schemes = [s.strip() for s in args.schemes.split(",") if s.strip()]
    heb = HebrewSource()
    norm: Normalizer = NORMALIZERS.get(args.iso, Normalizer())

    det_label, det_cdn, det_scores = detect_scheme(str(args.usj_dir))
    print(f"[validate] {args.iso}: structure-fingerprint says → {det_label} "
          f"(CDN {det_cdn} {100*det_scores.get(det_cdn,0):.1f}%)\n"
          f"[validate] eflomal-aligning under {schemes}, measuring PSA hi-conf …", file=sys.stderr)

    from lexeme_aligner.eflomal_align import EflomalAligner
    rows = []
    for sc in schemes:
        recs = build_corpus(books, args.usj_dir, heb, remap=remapper_for_scheme(sc))
        eflo = EflomalAligner()
        eflo.run(recs, norm)
        content, covered, hi = _psalm_quality(recs, eflo)
        rows.append((sc, content, covered, hi))
        print(f"  {sc:12s} PSA: hi-conf {100*hi/max(1,content):5.1f}%  coverage {100*covered/max(1,content):5.1f}%"
              f"  ({hi}/{content} hi)", file=sys.stderr)

    winner = max(rows, key=lambda r: r[3])                  # most PSA hi-conf pairs
    agree = "✓ agrees with fingerprint" if winner[0] == det_label else "✗ DISAGREES with fingerprint"
    print(f"\n[validate] winner by PSA hi-conf: {winner[0]}  →  {agree} ({det_label})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
