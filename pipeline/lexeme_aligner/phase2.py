"""Phase 2 — per-language disagreement tilt, and whether a GOLD-FREE signal can drive it.

The universal rule ≈ always-eflomal; the contested win-rate is language-dependent (arb→gloss, fra→eflomal).
This measures, per language:
  • TAILORED ceiling  — the disagreement rule fit on THAT language's own contested win-rates (needs gold).
  • UNIVERSAL / always-eflomal baselines.
  • GOLD-FREE features from the eflomal↔gloss comparison (agreement rate, eflomal low-conf share, contested
    share) — do any of them PREDICT how far to tilt toward gloss (the tailored rule's gloss-share)?

If a gold-free feature tracks the tilt, we can tailor non-gold languages (Indonesian, …) from the two runs
alone. If not, per-language tailoring is a gold-language-only lever and non-gold falls back to universal.

    python3 -m lexeme_aligner.phase2
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.config import OUT, RESOURCES
from lexeme_aligner.contest_rule import LANGS, _key, acc, collect, rule_from


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    args = ap.parse_args()

    data = collect(args.out, args.resources)
    feats = {iso: data[iso][1] for iso in LANGS}
    universal = rule_from(data)

    print("\n=== Phase 2 — per-language tailoring ceiling vs universal ===")
    print(f"  {'lang':5} {'contested':>9} {'universal':>9} {'TAILORED':>9} {'always-ef':>9} {'oracle':>7} "
          f"{'gl-share':>8}")
    tilt = {}
    for iso in LANGS:
        toks = data[iso][0]
        tail = rule_from({iso: data[iso]})                       # rule fit on this language's own gold
        n, c, ga, ea, orc = acc(toks, tail)
        _, cu, _, _, _ = acc(toks, universal)
        gl_share = sum(1 for tok in toks if tail.get(_key(tok, "tier"), "ef") == "gl") / max(1, n)
        tilt[iso] = gl_share
        print(f"  {iso:5} {n:>9} {100*cu/n:>8.1f}% {100*c/n:>8.1f}% {100*ea/n:>8.1f}% {100*orc/n:>6.1f}% "
              f"{100*gl_share:>7.1f}%")

    print("\n=== does a GOLD-FREE feature predict the tilt (tailored gloss-share)? ===")
    print(f"  {'lang':5} {'gl-share':>8} {'agree_rate':>11} {'ef09_share':>11} {'contested':>10}")
    for iso in LANGS:
        f = feats[iso]
        print(f"  {iso:5} {100*tilt[iso]:>7.1f}% {f['agree_rate']:>11.3f} {f['ef09_share']:>11.3f} "
              f"{f['contested_share']:>10.3f}")
    # rank-correlation sanity: sort langs by each feature, compare to tilt order
    order_tilt = sorted(LANGS, key=lambda i: tilt[i])
    print(f"\n  tilt order (low→high gloss):   {order_tilt}")
    for k in ("agree_rate", "ef09_share", "contested_share"):
        print(f"  by {k:16}: {sorted(LANGS, key=lambda i: feats[i][k])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
