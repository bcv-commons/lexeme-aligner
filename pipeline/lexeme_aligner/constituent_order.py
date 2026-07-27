"""Constituent-order discovery — per-TARGET-language word-order profiles, learned from the taken pool
using BHSA phrase `function` (Subj/Pred/Objc/Time/Loca/Cmpl/Adju/...; 2026-07-25 spine, OT-only).

Every language aligns to the same Hebrew source, so each aligned verse is a tiny parallel-order
experiment: Hebrew put the Predicate before the Subject (VSO-leaning) — did the target keep that order
or flip it? Aggregated over every verse where BOTH phrases of a pair have aligned members, this yields:

  • pairwise order-preservation — for each (function_a, function_b) pair that appears in source order
    a-then-b, P(target keeps a-then-b). A language that consistently flips (Pred, Subj) is
    subject-first (SVO/SOV) regardless of what its grammar books say — measured, not assumed.
  • positional drift per function — mean(normalized target position − normalized source position):
    which constituent types a language systematically fronts or defers.

Uses phrases with >=1 aligned CONTENT member (phrase target position = mean of its members' anchor
positions); pairs limited to ADJACENT phrases in source order (long-distance pairs mostly re-measure
the same reorderings while adding noise). Same edition-deduped "one language, one vote" philosophy as
cross_lang_prior if aggregated cross-language later — this module is PER-language.

Output: a per-language JSON profile (resources/constituent_order/<tag>.json) — a discovery artifact
first (typological word-order statistics for potentially hundreds of languages, measured against the
same fixed source), a gap-fill positional prior candidate second (see docs: the phrase-anchored
placement's measured no-op for vocab fills tempers expectations — position priors only matter when a
candidate is ambiguous).

    python3 -m lexeme_aligner.constituent_order --iso bsb --usj-dir pipeline/work/ingest-cache/usj-eng
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill import load_covered, load_priors
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import OT_BOOKS, build_corpus
from lexeme_aligner.versification import remapper


def profile(tag: str, usj_dir: Path, out_dir: Path = OUT, methods=("eflomal", "gloss")) -> dict:
    heb = HebrewSource()
    lex_pos, _ = load_priors(PRIOR_PACK)
    covered_h, _, anchors, _, _ = load_covered(tag, out_dir, methods, 0.0, lex_pos)
    recs = build_corpus(OT_BOOKS, usj_dir, heb, remap=remapper(tag, str(usj_dir)))

    pair_keep: dict[tuple, list] = collections.defaultdict(lambda: [0, 0])   # (fa,fb) -> [kept, total]
    drift: dict[str, list] = collections.defaultdict(list)                   # function -> [drifts]
    n_verses = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch or not r.toks:
            continue
        # phrase -> (function, source order = min member idx, target pos = mean member anchor)
        by_phrase: dict = {}
        for t in r.heb:
            if t.phrase_id and t.function and t.strong and t.is_content and t.idx in anch:
                fn, src, tgts = by_phrase.get(t.phrase_id, (t.function, t.idx, []))
                tgts.append(anch[t.idx])
                by_phrase[t.phrase_id] = (fn, min(src, t.idx), tgts)
        phrases = sorted(((src, fn, sum(tgts) / len(tgts)) for fn, src, tgts in by_phrase.values()))
        if len(phrases) < 2:
            continue
        n_verses += 1
        n_src, n_trg = max(len(r.heb), 1), max(len(r.toks), 1)
        for (src_a, fa, ta), (src_b, fb, tb) in zip(phrases, phrases[1:]):    # adjacent pairs only
            cell = pair_keep[(fa, fb)]
            cell[1] += 1
            cell[0] += ta < tb                                               # target kept source order
        for src, fn, tpos in phrases:
            drift[fn].append(tpos / n_trg - src / n_src)

    return {
        "tag": tag,
        "verses_measured": n_verses,
        "pair_order_kept": {f"{fa}>{fb}": {"kept": k, "total": n, "rate": round(k / n, 3)}
                            for (fa, fb), (k, n) in sorted(pair_keep.items()) if n >= 30},
        "function_drift": {fn: {"n": len(ds), "mean_drift": round(sum(ds) / len(ds), 4)}
                           for fn, ds in sorted(drift.items()) if len(ds) >= 30},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="produced TAG (align_*_<iso>_*.jsonl)")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--methods", default="eflomal,gloss")
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--out", type=Path, default=None,
                    help="default: resources/constituent_order/<tag>.json")
    args = ap.parse_args()

    prof = profile(args.iso, args.usj_dir, args.out_dir, tuple(m.strip() for m in args.methods.split(",")))
    out = args.out or Path("config/constituent_order") / f"{args.iso}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prof, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"[constituent_order] {args.iso}: {prof['verses_measured']} verses → {out}", file=sys.stderr)
    print("  most-reordered adjacent pairs (source order a>b, low rate = target flips):", file=sys.stderr)
    ranked = sorted(prof["pair_order_kept"].items(), key=lambda kv: kv[1]["rate"])
    for key, v in ranked[:8]:
        print(f"    {key:14s} kept {v['rate']*100:5.1f}%  (n={v['total']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
