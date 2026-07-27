"""Phrase-coherence annotation — tags each aligned pair `"coherent": true|false` in the align_*.jsonl
in place, OT-only (BHSA `phrase_id`, 2026-07-25 spine). See `phrase_coherence.py` for the gold
validation this rests on: within the SAME confidence tier, a scattered pair (no aligned phrase-mate
within `threshold` target tokens) is measurably less precise than a coherent one — eng hi_conf tier
86.2% (coherent) vs 78.9% (scattered), arb similar direction. `export_lex.py` reads this field to keep
`hi_conf` honest: a pair no longer counts as hi_conf just because score>=0.9 if it's flagged scattered.

Semantics, deliberately conservative:
  coherent=True   — has >=1 phrase-mate aligned within `threshold` target tokens (demoted NOTHING)
  coherent=False  — has phrase-mate(s) aligned, none within threshold (the suspect case — demoted)
  (absent)        — no phrase data to judge (NT tokens, or phrase has no other aligned member) — NOT
                    penalized; absence of evidence isn't evidence of scatter.

A pure ANNOTATION pass — never changes score, target, or t_idx; only adds/updates one field. Idempotent
(safe to re-run after a re-align) and JSONL-line-preserving (same book/verse/pair order untouched).

    python3 -m lexeme_aligner.annotate_coherence --iso bsb --usj-dir pipeline/work/ingest-cache/usj-eng
    python3 -m lexeme_aligner.annotate_coherence --iso arb_vdv --usj-dir pipeline/work/ingest-cache/usj-arb
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import OT_BOOKS, build_corpus
from lexeme_aligner.versification import remapper

_OT_BOOK_SET = frozenset(OT_BOOKS)


def annotate(tag: str, usj_dir: Path, out_dir: Path = OUT, methods=("eflomal", "gloss", "gapfill"),
            threshold: int = 3) -> dict:
    """Rewrites each align_<method>_<tag>_<BOOK>.jsonl (OT books only) in place. Returns counts."""
    heb = HebrewSource()
    recs = build_corpus(OT_BOOKS, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    phrase_of: dict[tuple, str] = {}                  # (ref, h_idx) -> phrase_id
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        for t in r.heb:
            if t.phrase_id and t.strong and t.is_content:
                phrase_of[(ref, t.idx)] = t.phrase_id

    counts = collections.Counter()
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            book = fp.stem.rsplit("_", 1)[-1]
            if book not in _OT_BOOK_SET:
                continue                                # NT: no phrase data, leave file untouched
            lines = fp.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            out_lines = []
            for line in lines:
                rec = json.loads(line)
                ref = rec["ref"]
                pos, phr = {}, {}
                for p in rec["pairs"]:
                    ti = p.get("t_idx")
                    if p.get("content") and ti and (ref, p["h_idx"]) in phrase_of:
                        pos[p["h_idx"]] = ti[0]
                        phr[p["h_idx"]] = phrase_of[(ref, p["h_idx"])]
                by_phrase: dict = collections.defaultdict(list)
                for h_idx, pid in phr.items():
                    by_phrase[pid].append(h_idx)
                for p in rec["pairs"]:
                    h_idx = p["h_idx"]
                    if h_idx not in pos:
                        counts["no-phrase-data"] += 1
                        continue
                    mates = [x for x in by_phrase[phr[h_idx]] if x != h_idx]
                    if not mates:
                        counts["no-mate"] += 1
                        continue
                    d = min(abs(pos[h_idx] - pos[x]) for x in mates)
                    p["coherent"] = d <= threshold
                    counts["coherent" if p["coherent"] else "scattered"] += 1
                out_lines.append(json.dumps(rec, ensure_ascii=False))
            fp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return dict(counts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="produced TAG")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--methods", default="eflomal,gloss,gapfill")
    ap.add_argument("--threshold", type=int, default=3)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()

    counts = annotate(args.iso, args.usj_dir, args.out_dir,
                      tuple(m.strip() for m in args.methods.split(",")), args.threshold)
    total_judged = counts.get("coherent", 0) + counts.get("scattered", 0)
    print(f"[annotate_coherence] {args.iso}: {counts} "
          f"({100*counts.get('scattered',0)/max(1,total_judged):.1f}% of judged pairs scattered)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
