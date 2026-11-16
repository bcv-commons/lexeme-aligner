"""Pooled priors for one text from the language's published lexeme-alignments partition (video-sync request, 2026-11-16).

full_chain aligns a language's editions together: every edition's eflomal export is pooled into publish/lexeme-alignments/iso=<iso>/ and
the gloss step of each edition bootstraps from that pool. `align-text` aligns ONE outside text, so before this its gloss seed came from
that text alone, and eflomal had no lexical priors at all: a small text (a film's narration, a few hundred verses) was aligned with
almost nothing known. This module lends it the language's published renderings in the two places the chain can take them:

  eflomal  `pairs(recs, partition)` -> LEX priors (<source Strong's> <target word> <alpha>) for the content words of the text being
           aligned: each lexeme's renderings in the partition's eflomal rows (single-word surfaces), top `top_k` with share >=
           `min_share`, alpha = weight x share x scale. The same rule the NT bridge's pooled priors use (nt_bridge.pool_renderings,
           on since 2026-11-09), here for any text and both testaments. run_pilot --pool-priors.
  gloss    `merge(own, partition, out)` -> a lexroot whose partition holds the text's own eflomal rows plus the partition's eflomal rows,
           the pool's counts scaled so that its total equals the text's own total: where the text has a lexeme, its own renderings and
           the pool's weigh about the same; where it has none, the pool's renderings are the seed. BootstrapPriors reads it unchanged.

`exclude` drops partition rows by base_text (case-insensitive): for a measurement where the text IS one of the pooled editions, or when
a user aligns an edition we have published.

    python -m lexeme_aligner.pool_priors merge --iso eng --own OUT/lexroot --pool FILE --out OUT/lexroot-pool [--exclude BSB]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

WEIGHT, TOP_K, MIN_SHARE, SCALE = 0.5, 5, 0.1, 2.0     # = config/nt_bridge.json's pool weight, top_k, min_share, scale


def settings() -> dict:
    return {"weight": WEIGHT, "top_k": TOP_K, "min_share": MIN_SHARE, "scale": SCALE}


def pairs(recs, partition: Path, exclude=(), weight: float = WEIGHT, top_k: int = TOP_K, min_share: float = MIN_SHARE,
          scale: float = SCALE) -> list[tuple[str, str, float]]:
    """(source Strong's, target word, alpha) for the content tokens of `recs`, from the partition's eflomal rows."""
    from lexeme_aligner.nt_bridge import pool_renderings
    lex = {t.lexeme for r in recs for t in r.heb if t.is_content and t.strong}
    pr = pool_renderings(Path(partition), set(exclude), lex)
    best: dict[tuple[str, str], float] = {}
    for r in recs:
        for t in r.heb:
            if not (t.is_content and t.strong) or t.lexeme not in pr:
                continue
            c = pr[t.lexeme]
            tot = sum(c.values())
            for w, k in c.most_common(top_k):
                if k / tot >= min_share:
                    best[(t.strong, w)] = max(best.get((t.strong, w), 0.0), weight * k / tot)
    return [(s, w, v * scale) for (s, w), v in best.items()]


def merge(own_root: Path, iso: str, partition: Path, out_root: Path, exclude=()) -> dict:
    """Write out_root/iso=<iso>/data.parquet = the text's own eflomal rows + the partition's eflomal rows (scaled, see the module doc)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = ["surface", "lexeme", "method", "base_text", "count"]
    own_fp = Path(own_root) / f"iso={iso}" / "data.parquet"
    own = [r for r in pq.read_table(own_fp, columns=cols).to_pylist() if r["method"] == "eflomal"] if own_fp.exists() else []
    ex = {e.lower() for e in exclude}
    pool = [r for r in pq.read_table(partition, columns=cols, filters=[("method", "=", "eflomal")]).to_pylist()
            if r["base_text"].lower() not in ex]
    own_total, pool_total = sum(r["count"] for r in own), sum(r["count"] for r in pool)
    s = (own_total / pool_total) if own_total and pool_total else 1.0
    rows = [dict(r, count=float(r["count"])) for r in own] + [dict(r, count=r["count"] * s) for r in pool]
    dest = Path(out_root) / f"iso={iso}" / "data.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([("surface", pa.string()), ("lexeme", pa.string()), ("method", pa.string()),
                                                                ("base_text", pa.string()), ("count", pa.float64())])), dest)
    return {"own_rows": len(own), "pool_rows": len(pool), "pool_editions": len({r["base_text"] for r in pool}),
            "own_count": own_total, "pool_count": pool_total, "pool_scale": s}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--iso", required=True)
    m.add_argument("--own", type=Path, required=True, help="the text's own lexroot (export_lex --root)")
    m.add_argument("--pool", type=Path, required=True, help="the language's lexeme-alignments data.parquet")
    m.add_argument("--out", type=Path, required=True, help="lexroot to write (gloss reads it through ALIGNER_LEX_ROOT)")
    m.add_argument("--exclude", default="", help="base_texts to leave out of the pool (comma-separated)")
    a = ap.parse_args(argv)
    rep = merge(a.own, a.iso, a.pool, a.out, [e for e in a.exclude.split(",") if e])
    print(json.dumps(rep), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
