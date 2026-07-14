"""Per-tier precision — is each mode's TRUST CUTOFF in the right place?

Bucket a mode's alignments by its own confidence tier and score each bucket against gold. If a low tier
is mostly wrong, it should be DEMOTED to a gap (untrusted) and gap-filled by another mode — the framework
proven with gapfill, generalised to eflomal + gloss. This is the diagnostic behind "turn eflomal's cutoff
down and let gloss re-run the residual".

  eflomal → tier by score   (0.9 = intersection core, 0.6 = union-only)
  gloss   → tier by method   (exact / stem / prefix / name / multi / head / fuzzy)
  gapfill → tier by prior    (strong / name)

    python3 -m lexeme_aligner.score_tiers --iso fra --method eflomal
    python3 -m lexeme_aligner.score_tiers --iso swk --method gloss --gold lexicon
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.benchmark import agrees, load_gold_lexicon, norm_surface
from lexeme_aligner.config import OUT, PRIOR_PACK, RESOURCES


def _tier(method: str, p: dict) -> str:
    if method == "eflomal":
        return f"score {p.get('score')}"
    if method == "gloss":
        return p.get("method", "?")
    if method == "gapfill":
        return p.get("prior", "embedding")
    return "all"


def _load_pos(prior_pack: Path) -> dict:
    """lexeme → POS (from prior-pack) for categorized trust; {} if the column is absent."""
    if not Path(prior_pack).exists():
        return {}
    import pyarrow.parquet as pq
    if "pos" not in pq.read_schema(prior_pack).names:
        return {}
    return {r["lexeme"]: r["pos"] for r in pq.read_table(prior_pack, columns=["lexeme", "pos"]).to_pylist()}


def _pairs(iso: str, method: str, out_dir: Path, pos_map: dict, by: str):
    """Yield (ref, strong, target-words, bucket). `by` = tier | pos | pos+tier (category × confidence)."""
    files = sorted(out_dir.glob(f"align_{method}_{iso}_*.jsonl"))
    if not files:
        raise SystemExit(f"no align_{method}_{iso}_*.jsonl — run that mode first")
    for fp in files:
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content") and p.get("strong") and (p.get("target") or "").strip():
                        pos = pos_map.get(p.get("lexeme"), "?")
                        tier = _tier(method, p)
                        bucket = pos if by == "pos" else f"{pos:6} {tier}" if by == "pos+tier" else tier
                        yield (rec["ref"], p["strong"],
                               [norm_surface(w) for w in p["target"].split()], bucket)


def _gold_clear(iso: str, res_dir: Path):
    import pyarrow.parquet as pq
    fp = res_dir / "strongs" / "attestations" / f"{iso}.parquet"
    if not fp.exists():
        raise SystemExit(f"no Clear gold for {iso} at {fp}")
    t = pq.read_table(fp, columns=["ref", "strong", "surface"]).to_pydict()
    g: dict[tuple, set] = collections.defaultdict(set)
    for ref, s, su in zip(t["ref"], t["strong"], t["surface"]):
        g[(str(ref), s)].add(norm_surface(su))
    return g


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--method", required=True, choices=["eflomal", "gloss", "gapfill", "stat"])
    ap.add_argument("--gold", choices=["clear", "lexicon"], default="clear")
    ap.add_argument("--by", choices=["tier", "pos", "pos+tier"], default="tier",
                    help="bucket by confidence tier, POS category (bcv-query), or both")
    ap.add_argument("--min-n", type=int, default=1, help="hide cells below this sample count (anti-noise)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--cache", type=Path, default=Path("data/karnbibeln"))
    args = ap.parse_args()

    pos_map = _load_pos(args.prior_pack) if args.by != "tier" else {}
    tally: dict[str, list] = collections.defaultdict(lambda: [0, 0])   # bucket -> [scorable, correct]
    if args.gold == "clear":
        gold = _gold_clear(args.iso, args.resources)
        for ref, strong, words, bucket in _pairs(args.iso, args.method, args.out, pos_map, args.by):
            key = (f"{ref:08d}", strong)
            if key not in gold:
                continue
            tally[bucket][0] += 1
            tally[bucket][1] += any(w in gold[key] for w in words)
    else:
        heb = load_gold_lexicon("karnbibeln", "hebrew", args.cache)
        grk = load_gold_lexicon("karnbibeln", "greek", args.cache)
        for ref, strong, words, bucket in _pairs(args.iso, args.method, args.out, pos_map, args.by):
            gloss = (heb if strong.startswith("H") else grk).get(strong)
            if not gloss:
                continue
            tally[bucket][0] += 1
            tally[bucket][1] += agrees([" ".join(words)], gloss)

    ts = sum(v[0] for v in tally.values())
    tc = sum(v[1] for v in tally.values())
    print(f"\n=== precision by {args.by} — {args.iso} / {args.method} (gold={args.gold}) ===")
    print(f"  {'bucket':22} {'scorable':>9} {'correct':>8} {'precision':>10}")
    for bucket, (s, c) in sorted(tally.items(), key=lambda kv: -kv[1][1] / max(1, kv[1][0])):
        if s >= args.min_n:
            print(f"  {bucket:22} {s:>9} {c:>8} {100*c/max(1,s):>9.1f}%")
    print(f"  {'OVERALL':22} {ts:>9} {tc:>8} {100*tc/max(1,ts):>9.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
