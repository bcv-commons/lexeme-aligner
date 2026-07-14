"""Empirical trust matrix — (mode × POS × tier) precision across ALL gold languages, WITH cross-language
variance. The discipline the 6 gold standards buy us: don't trust a hypothesis unless it holds across
languages. So each cell carries not just a pooled precision but the per-language spread —

  • low spread + high precision  → ROBUST structural trust (POS is source-side → generalizes to new targets)
  • high spread                  → LANGUAGE-DEPENDENT (a signal itself; conservative weight, don't over-trust)

Feeds a category×tier-weighted merge. The weight is deliberately CONSERVATIVE: pooled precision with a
Wilson lower bound (shrinks thin cells) minus a volatility penalty (shrinks cells that disagree across
languages). Thin cells back off to the coarser (mode×tier → mode) estimate.

    python3 -m lexeme_aligner.trust_profile              # build + report + write trust_matrix.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

from lexeme_aligner.benchmark import agrees, load_gold_lexicon, norm_surface
from lexeme_aligner.config import OUT, PRIOR_PACK, RESOURCES
from lexeme_aligner.score_tiers import _gold_clear, _load_pos, _tier

GOLD = {"fra": "clear", "arb": "clear", "eng": "clear", "hau": "clear", "swk": "lexicon", "swe": "lexicon"}
MODES = ["eflomal", "gloss", "gapfill"]


def _wilson_lb(correct: int, n: int, z: float = 1.96) -> float:
    """Wilson score lower bound — a small sample can't earn a high weight (anti-overfit)."""
    if n == 0:
        return 0.0
    p = correct / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / d)


def score_lang_mode(iso, method, gold_type, out, res, pos_map, cache):
    """(pos, tier) → [n, correct] for one (language, mode), judged against that language's gold."""
    cells: dict[tuple, list] = collections.defaultdict(lambda: [0, 0])
    files = sorted(out.glob(f"align_{method}_{iso}_*.jsonl"))
    if not files:
        return cells
    if gold_type == "clear":
        gold = _gold_clear(iso, res)

        def judged(strong, ref):
            return (f"{ref:08d}", strong) in gold

        def hit(strong, words, ref):
            return any(w in gold[(f"{ref:08d}", strong)] for w in words)
    else:
        heb = load_gold_lexicon("karnbibeln", "hebrew", cache)
        grk = load_gold_lexicon("karnbibeln", "greek", cache)

        def _g(strong):
            return (heb if strong.startswith("H") else grk).get(strong)

        def judged(strong, ref):
            return _g(strong) is not None

        def hit(strong, words, ref):
            return agrees([" ".join(words)], _g(strong))

    for fp in files:
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if not (p.get("content") and p.get("strong") and (p.get("target") or "").strip()):
                        continue
                    if not judged(p["strong"], rec["ref"]):
                        continue
                    words = [norm_surface(w) for w in p["target"].split()]
                    cell = cells[(pos_map.get(p.get("lexeme"), "?"), _tier(method, p))]
                    cell[0] += 1
                    cell[1] += hit(p["strong"], words, rec["ref"])
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclude", default=None, help="hold this language OUT (leave-one-out matrix)")
    ap.add_argument("--quiet", action="store_true", help="write json, skip the table")
    ap.add_argument("--min-n", type=int, default=25, help="per-language floor to count a cell in the spread")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--cache", type=Path, default=Path("data/karnbibeln"))
    ap.add_argument("--write", type=Path, default=Path("data/trust_matrix.json"))
    args = ap.parse_args()

    pos_map = _load_pos(args.prior_pack)
    langs = {k: v for k, v in GOLD.items() if k != args.exclude}      # leave-one-out support
    # matrix[(mode, pos, tier)] = {lang: [n, correct]}
    matrix: dict[tuple, dict] = collections.defaultdict(dict)
    for iso, gt in langs.items():
        for mode in MODES:
            for cell, (n, c) in score_lang_mode(iso, mode, gt, args.out, args.resources, pos_map, args.cache).items():
                if n:
                    matrix[(mode,) + cell][iso] = [n, c]

    rows = []
    for (mode, pos, tier), per_lang in matrix.items():
        tot_n = sum(v[0] for v in per_lang.values())
        tot_c = sum(v[1] for v in per_lang.values())
        pooled = tot_c / tot_n
        precs = [v[1] / v[0] for v in per_lang.values() if v[0] >= args.min_n]
        spread = (max(precs) - min(precs)) if len(precs) >= 2 else 0.0
        weight = round(max(0.0, _wilson_lb(tot_c, tot_n) - 0.5 * spread), 3)   # conservative: shrink + volatility
        rows.append({"mode": mode, "pos": pos, "tier": tier, "n": tot_n, "pooled": round(pooled, 3),
                     "langs": len(per_lang), "spread": round(spread, 3), "weight": weight,
                     "per_lang": {k: round(v[1] / v[0], 2) for k, v in per_lang.items() if v[0] >= args.min_n}})

    rows.sort(key=lambda r: (r["mode"], -r["weight"]))
    args.write.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.quiet:
        return 0

    print(f"\n=== trust matrix — {len(rows)} cells (mode × pos × tier) over {len(langs)} gold langs ===")
    print(f"  {'mode':8} {'pos':7} {'tier':14} {'n':>7} {'pooled':>7} {'spread':>7} {'weight':>7}  langs")
    for r in rows:
        if r["n"] >= 50:
            print(f"  {r['mode']:8} {r['pos']:7} {r['tier']:14} {r['n']:>7} {r['pooled']:>7.2f} "
                  f"{r['spread']:>7.2f} {r['weight']:>7.2f}  {r['per_lang']}")
    print("\n  ROBUST (high weight, low spread) = trust everywhere · VOLATILE (high spread) = language-dependent.")
    print(f"  → {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
