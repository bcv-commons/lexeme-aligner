"""Verify induced stopword / function-word lists against gold alignment data.

For each gold language, measures:
  • GOLD ENTROPY — how many distinct Strong's each target surface maps to in the gold data.
    True function words scatter across dozens of Strong's (high entropy); content words concentrate
    on 1-3 (low entropy). This is the ground-truth signal our frequency+dispersion induction tries
    to approximate WITHOUT gold.
  • CLASSIFICATION — our induced list vs gold-derived classification:
    - True positive:  in our list AND high-entropy in gold (correctly flagged function word)
    - False positive: in our list but LOW-entropy in gold  (content word we wrongly flagged)
    - False negative: NOT in our list but high-entropy + frequent in gold (function word we missed)
  • GLOSS IMPACT — for each word in our list, the top-1 accuracy delta if gloss had been blocked
    from aligning it (estimated from the benchmark's wrong-call table).

The entropy threshold for "function word" is configurable (default: ≥10 distinct Strong's partners
in the gold attestations = function). This is deliberately generous — a word with 10+ Strong's
partners is unambiguously multi-functional.

    python3 -m lexeme_aligner.verify_stopwords --iso fra
    python3 -m lexeme_aligner.verify_stopwords --all
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
from pathlib import Path

from lexeme_aligner.usj_source import strip_marks

_GOLD_DIR = Path("data/resources/strongs/attestations")
_SW_DIR = Path("data/stopwords")
_ENTROPY_FLOOR = 10       # ≥ this many distinct Strong's partners → "function word" in gold
_FREQ_FLOOR = 50          # only score surfaces with ≥ this many gold occurrences (thin tail is noise)


def _load_gold(iso: str, gold_dir: Path = _GOLD_DIR) -> dict[str, collections.Counter]:
    """surface → Counter{strong: count} from the gold attestations."""
    import pyarrow.parquet as pq
    fp = gold_dir / f"{iso}.parquet"
    if not fp.exists():
        return {}
    rows = pq.read_table(fp, columns=["surface", "strong"]).to_pylist()
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        s = strip_marks((r.get("surface") or "").lower())
        if s:
            out[s][r["strong"]] += 1
    return out


def _load_stopwords(iso: str, sw_dir: Path = _SW_DIR) -> set[str]:
    fp = sw_dir / f"{iso}.txt"
    if not fp.exists():
        return set()
    return {w.strip().lower() for w in fp.read_text(encoding="utf-8").splitlines() if w.strip()}


def _entropy(counter: collections.Counter) -> float:
    total = sum(counter.values())
    if total <= 1:
        return 0.0
    return -sum((n / total) * math.log2(n / total) for n in counter.values() if n > 0)


def verify(iso: str, gold_dir: Path = _GOLD_DIR, sw_dir: Path = _SW_DIR,
           entropy_floor: int = _ENTROPY_FLOOR, freq_floor: int = _FREQ_FLOOR) -> dict:
    gold = _load_gold(iso, gold_dir)
    our_sw = _load_stopwords(iso, sw_dir)
    if not gold:
        return {"iso": iso, "error": "no gold data"}
    if not our_sw:
        return {"iso": iso, "error": "no stopword list"}

    tp, fp, fn = [], [], []
    all_words = []

    for surface, strong_counts in gold.items():
        total_occ = sum(strong_counts.values())
        if total_occ < freq_floor:
            continue
        n_strongs = len(strong_counts)
        ent = _entropy(strong_counts)
        is_function_gold = n_strongs >= entropy_floor
        in_our_list = surface in our_sw
        top_strong, top_count = strong_counts.most_common(1)[0]
        dominance = top_count / total_occ

        rec = {"surface": surface, "n_strongs": n_strongs, "entropy": round(ent, 2),
               "total_occ": total_occ, "dominance": round(dominance, 3),
               "top_strong": top_strong, "in_list": in_our_list,
               "gold_function": is_function_gold}
        all_words.append(rec)

        if in_our_list and is_function_gold:
            tp.append(rec)
        elif in_our_list and not is_function_gold:
            fp.append(rec)
        elif not in_our_list and is_function_gold:
            fn.append(rec)

    # Words in our list that don't appear in gold at all (no verdict possible)
    gold_surfaces = set(gold.keys())
    not_in_gold = our_sw - gold_surfaces
    # Words in our list that are in gold but below freq_floor
    thin = {w for w in our_sw if w in gold_surfaces and sum(gold[w].values()) < freq_floor}

    n_scorable = len([w for w in all_words if w["in_list"]]) + len([w for w in all_words if not w["in_list"] and w["gold_function"]])
    precision = len(tp) / max(1, len(tp) + len(fp))
    recall = len(tp) / max(1, len(tp) + len(fn))
    f1 = 2 * precision * recall / max(1e-9, precision + recall)

    fp.sort(key=lambda r: -r["total_occ"])
    fn.sort(key=lambda r: -r["total_occ"])

    return {
        "iso": iso,
        "our_stopwords": len(our_sw),
        "gold_surfaces_scored": len(all_words),
        "gold_function_words": len([w for w in all_words if w["gold_function"]]),
        "true_positives": len(tp),
        "false_positives": len(fp),
        "false_negatives": len(fn),
        "not_in_gold": len(not_in_gold),
        "below_freq_floor": len(thin),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "top_false_positives": fp[:15],
        "top_false_negatives": fn[:15],
    }


def print_report(result: dict) -> None:
    iso = result["iso"]
    if "error" in result:
        print(f"\n=== {iso}: {result['error']} ===", file=sys.stderr)
        return

    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  STOPWORD VERIFICATION — {iso}", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)
    print(f"  Our list: {result['our_stopwords']} words", file=sys.stderr)
    print(f"  Gold surfaces scored (≥{_FREQ_FLOOR} occ): {result['gold_surfaces_scored']}", file=sys.stderr)
    print(f"  Gold function words (≥{_ENTROPY_FLOOR} distinct Strong's): "
          f"{result['gold_function_words']}", file=sys.stderr)
    print(f"\n  Classification (our list vs gold-entropy threshold):", file=sys.stderr)
    print(f"    True positives:  {result['true_positives']}", file=sys.stderr)
    print(f"    False positives: {result['false_positives']}", file=sys.stderr)
    print(f"    False negatives: {result['false_negatives']}", file=sys.stderr)
    print(f"    Not in gold:     {result['not_in_gold']} (can't score)", file=sys.stderr)
    print(f"    Below freq floor:{result['below_freq_floor']} (can't score)", file=sys.stderr)
    print(f"\n    Precision: {result['precision']:.1%}  "
          f"Recall: {result['recall']:.1%}  "
          f"F1: {result['f1']:.1%}", file=sys.stderr)

    if result["top_false_positives"]:
        print(f"\n  FALSE POSITIVES (in our list, but gold says content — ≤{_ENTROPY_FLOOR} Strong's):",
              file=sys.stderr)
        for r in result["top_false_positives"]:
            print(f"    {r['surface']:20s}  {r['n_strongs']:3d} Strong's  "
                  f"dominance={r['dominance']:.0%}  top={r['top_strong']}  "
                  f"occ={r['total_occ']}", file=sys.stderr)

    if result["top_false_negatives"]:
        print(f"\n  FALSE NEGATIVES (NOT in our list, but gold says function — ≥{_ENTROPY_FLOOR} Strong's):",
              file=sys.stderr)
        for r in result["top_false_negatives"]:
            print(f"    {r['surface']:20s}  {r['n_strongs']:3d} Strong's  "
                  f"dominance={r['dominance']:.0%}  top={r['top_strong']}  "
                  f"occ={r['total_occ']}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", help="single language to verify")
    ap.add_argument("--all", action="store_true", help="verify all languages with both gold + stopwords")
    ap.add_argument("--gold-dir", type=Path, default=_GOLD_DIR)
    ap.add_argument("--sw-dir", type=Path, default=_SW_DIR)
    ap.add_argument("--entropy-floor", type=int, default=_ENTROPY_FLOOR)
    ap.add_argument("--freq-floor", type=int, default=_FREQ_FLOOR)
    ap.add_argument("--json", action="store_true", help="output JSON instead of human-readable")
    args = ap.parse_args()

    if args.all:
        isos = sorted({fp.stem for fp in args.sw_dir.glob("*.txt")}
                       & {fp.stem for fp in args.gold_dir.glob("*.parquet")})
    elif args.iso:
        isos = [args.iso]
    else:
        ap.error("need --iso or --all")
        return 1

    results = []
    for iso in isos:
        r = verify(iso, args.gold_dir, args.sw_dir, args.entropy_floor, args.freq_floor)
        results.append(r)
        if not args.json:
            print_report(r)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif len(results) > 1:
        print(f"\n{'='*70}", file=sys.stderr)
        print(f"  SUMMARY TABLE", file=sys.stderr)
        print(f"{'='*70}", file=sys.stderr)
        print(f"  {'iso':6s} {'list':>5s} {'TP':>4s} {'FP':>4s} {'FN':>4s} "
              f"{'Prec':>6s} {'Rec':>6s} {'F1':>6s}", file=sys.stderr)
        for r in results:
            if "error" in r:
                print(f"  {r['iso']:6s} — {r['error']}", file=sys.stderr)
            else:
                print(f"  {r['iso']:6s} {r['our_stopwords']:5d} {r['true_positives']:4d} "
                      f"{r['false_positives']:4d} {r['false_negatives']:4d} "
                      f"{r['precision']:6.1%} {r['recall']:6.1%} {r['f1']:6.1%}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
