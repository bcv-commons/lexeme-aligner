#!/usr/bin/env python3
"""Strong's on-ramp — a DERIVED view over the canonical `lexeme-alignments` dataset.

The published dataset is lexeme-anchored and provenance-tagged (one row per surface × lexeme × method).
Ecosystem tools that key on **Strong's numbers** want a simpler shape: "given a Strong's number, the
target words for it, with frequency". This example script produces exactly that — it is NOT a second
source of truth, just a documented reshape you (or a consumer) run on the canonical parquet.

Two things it handles that a naive rollup gets wrong:
  1. The union has one row PER METHOD, so summing `count` across methods double-counts the same
     occurrences. We pick a single **base method** (default `eflomal`) — override with --method.
  2. It rolls the fine `lexeme` up to its `strong` (many lexemes → one Strong's) — `strong` isn't a
     stored column (dropped 2026-07 as a pure, lossless function of `lexeme`; see the dataset
     README's derivation note) — then aggregates per (strong, surface) and recomputes `share` =
     P(surface | strong) within Strong's.

    python3 scripts/strongs_view.py --iso swe                         # → out/strongs_view_swe.tsv
    python3 scripts/strongs_view.py --iso swe --method gloss --min-share 0.02 --hi-conf 0.5

Columns out: strong, surface, count, share, hi_conf   (share = count / Σ count for that Strong's).
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT


def build(iso: str, root: Path, method: str, min_share: float, hi_conf: float, min_count: int,
          base_text: str | None = None):
    import pyarrow.parquet as pq
    from lexeme_aligner.benchmark import norm_strong    # lexeme "hbo:0871a" -> strong "H0871"

    fp = root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        raise SystemExit(f"no lexeme-alignments partition for {iso} at {fp}")
    cnt: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    hic: dict[tuple, float] = collections.defaultdict(float)
    for r in pq.read_table(fp).to_pylist():
        if r.get("method", method) != method:          # single base method — no cross-method double-count
            continue
        if base_text and r.get("base_text") != base_text:   # optional: scope to one edition
            continue
        if r["count"] < min_count or (r.get("hi_conf") or 0) < hi_conf:
            continue
        strong = norm_strong(r["lexeme"])
        cnt[strong][r["surface"]] += r["count"]
        hic[(strong, r["surface"])] += (r.get("hi_conf") or 0) * r["count"]
    rows = []
    for strong, ctr in cnt.items():
        tot = sum(ctr.values())
        for surface, c in ctr.items():
            share = c / tot
            if share < min_share:
                continue
            rows.append((strong, surface, c, round(share, 4), round(hic[(strong, surface)] / c, 4)))
    rows.sort(key=lambda r: (r[0], -r[2]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--root", type=Path, default=LEX_ROOT)
    ap.add_argument("--method", default="eflomal", help="base method to roll up (avoids double-count)")
    ap.add_argument("--base-text", default=None, help="optional: scope to one edition (else pool all)")
    ap.add_argument("--min-share", type=float, default=0.0, help="drop surfaces below this P(surface|strong)")
    ap.add_argument("--hi-conf", type=float, default=0.0, help="keep only rows at/above this hi_conf")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    rows = build(a.iso, a.root, a.method, a.min_share, a.hi_conf, a.min_count, a.base_text)
    dest = a.out or OUT / f"strongs_view_{a.iso}.tsv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("strong\tsurface\tcount\tshare\thi_conf\n"
                    + "\n".join("\t".join(map(str, r)) for r in rows) + "\n", encoding="utf-8")
    print(f"[strongs_view] {a.iso}: {len(rows)} (strong→surface) rows (base method={a.method}) → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
