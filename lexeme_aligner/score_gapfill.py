"""Direct gap-fill scorer — the honest metric for gapfill.

Of the source tokens eflomal+gloss BOTH missed (the gaps), how many did gapfill fill CORRECTLY,
broken down by which prior fired (strong-back-off / name-translit / embedding). These tokens had ZERO
alignment before, so a correct fill is strictly-additive coverage — which the aggregate top-1 benchmark
HIDES (mixing hard new tokens into an easy average pulls the average down even when fills are right).

Two gold backends (mirror the general benchmark):
  --gold clear   : positional Clear gold — did we pick the right target AT THIS VERSE (ref, strong)?
  --gold lexicon : karnbibeln — is our target a known rendering of the Strong's (stem-tolerant)? [swk/swe]

    python3 -m lexeme_aligner.score_gapfill --iso fra
    python3 -m lexeme_aligner.score_gapfill --iso swk --gold lexicon
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.benchmark import agrees, load_gold_lexicon, norm_surface
from lexeme_aligner.config import OUT, RESOURCES

_PRIORS = ["strong", "name", "embedding"]


def _gap_pairs(iso: str, out_dir: Path):
    """Yield (ref, strong, target_words, prior) for every gapfill fill (align_gapfill jsonl)."""
    files = sorted(out_dir.glob(f"align_gapfill_{iso}_*.jsonl"))
    if not files:
        raise SystemExit(f"no align_gapfill_{iso}_*.jsonl — run gapfill --iso {iso} first")
    for fp in files:
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content") and p.get("strong") and (p.get("target") or "").strip():
                        yield (rec["ref"], p["strong"],
                               [norm_surface(w) for w in p["target"].split()], p.get("prior", "embedding"))


def score_clear(iso: str, out_dir: Path, res_dir: Path):
    import pyarrow.parquet as pq
    base = res_dir / "strongs" / "attestations"
    fp = base / f"{iso}.parquet"
    if not fp.exists():
        raise SystemExit(f"no Clear gold for {iso} at {fp}")
    gold: dict[tuple, set] = collections.defaultdict(set)      # (ref, strong) -> {norm target surfaces}
    t = pq.read_table(fp, columns=["ref", "strong", "surface"]).to_pydict()
    for ref, strong, surf in zip(t["ref"], t["strong"], t["surface"]):
        gold[(str(ref), strong)].add(norm_surface(surf))
    tally = {pr: [0, 0] for pr in _PRIORS}                     # prior -> [scorable, correct]
    for ref, strong, words, prior in _gap_pairs(iso, out_dir):
        key = (f"{ref:08d}", strong)
        if key not in gold:
            continue                                          # gold has no truth for this token → not scorable
        tally.setdefault(prior, [0, 0])[0] += 1
        tally[prior][1] += any(w in gold[key] for w in words)
    return tally


def score_lexicon(iso: str, out_dir: Path, cache_dir: Path):
    heb = load_gold_lexicon("karnbibeln", "hebrew", cache_dir)
    grk = load_gold_lexicon("karnbibeln", "greek", cache_dir)
    tally = {pr: [0, 0] for pr in _PRIORS}
    for ref, strong, words, prior in _gap_pairs(iso, out_dir):
        gloss = (heb if strong.startswith("H") else grk).get(strong)
        if not gloss:
            continue
        tally.setdefault(prior, [0, 0])[0] += 1
        tally[prior][1] += agrees([" ".join(words)], gloss)
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--gold", choices=["clear", "lexicon"], default="clear")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--cache", type=Path, default=Path("data/karnbibeln"))
    args = ap.parse_args()

    tally = (score_clear(args.iso, args.out, args.resources) if args.gold == "clear"
             else score_lexicon(args.iso, args.out, args.cache))
    tot_s = sum(v[0] for v in tally.values())
    tot_c = sum(v[1] for v in tally.values())
    print(f"\n=== gap-fill direct score — {args.iso} (gold={args.gold}) ===")
    print(f"  {'prior':10} {'scorable':>9} {'correct':>8} {'precision':>10}")
    for pr in _PRIORS + [k for k in tally if k not in _PRIORS]:
        s, c = tally.get(pr, [0, 0])
        if s:
            print(f"  {pr:10} {s:>9} {c:>8} {100*c/s:>9.1f}%")
    print(f"  {'OVERALL':10} {tot_s:>9} {tot_c:>8} {100*tot_c/max(1,tot_s):>9.1f}%")
    print(f"  → {tot_c} source tokens that had ZERO alignment are now CORRECTLY aligned "
          f"(of {tot_s} gap fills the gold can judge).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
