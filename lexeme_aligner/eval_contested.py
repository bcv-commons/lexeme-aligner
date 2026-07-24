"""Contested-token decision accuracy — the sensitive test of the trust rules.

The aggregate top-1 can't judge the rules: 95%+ of tokens are agreement (both modes same target), so the
headline barely moves when we change how the contested ~5% is resolved. THIS scores only the contested
tokens — where eflomal ≠ gloss and gold judges — and asks: does the trust-weighted choice beat
always-gloss / always-eflomal, and how close to the oracle (best-possible)?

With --trust pointing at a HELD-OUT matrix (`trust_profile --exclude <iso>`), this is the leave-one-out
proof that the UNIVERSAL rules generalize — the rules are learned without the test language. Clear gold.

    python3 -m lexeme_aligner.eval_contested --iso fra --trust data/trust_matrix.json
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import norm_surface
from lexeme_aligner.config import OUT, PRIOR_PACK, RESOURCES
from lexeme_aligner.merge_align import _tier, load_trust
from lexeme_aligner.score_tiers import _gold_clear


def _index(iso: str, method: str, out_dir: Path) -> dict:
    idx: dict[tuple, tuple] = {}
    for fp in tag_files(out_dir, method, iso):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content") and p.get("strong") and (p.get("target") or "").strip():
                        idx[(rec["ref"], p["h_idx"])] = (
                            tuple(norm_surface(w) for w in p["target"].split()),
                            _tier(method, p), p["strong"], p.get("lexeme"))
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--trust", type=Path, default=Path("data/trust_matrix.json"))
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    gold = _gold_clear(args.iso, args.resources)
    ef, gl = _index(args.iso, "eflomal", args.out), _index(args.iso, "gloss", args.out)
    trust, mode_default, pos_map = load_trust(args.trust, args.prior_pack)

    def hit(ref, strong, words):
        return any(w in gold.get((f"{ref:08d}", strong), ()) for w in words)

    n = 0
    acc: collections.Counter = collections.Counter()
    for key in set(ef) & set(gl):
        e, g = ef[key], gl[key]
        ref, strong = key[0], e[2]
        if (f"{ref:08d}", strong) not in gold or e[0] == g[0]:
            continue                                             # judged + contested only
        n += 1
        eh, gh = hit(ref, strong, e[0]), hit(ref, strong, g[0])
        pos = pos_map.get(e[3], "?")
        ew = trust.get(("eflomal", pos, e[1]), mode_default.get("eflomal", 0.1))
        gw = trust.get(("gloss", pos, g[1]), mode_default.get("gloss", 0.1))
        acc["trust"] += eh if ew >= gw else gh
        acc["always-gloss"] += gh
        acc["always-eflomal"] += eh
        acc["oracle"] += eh or gh
    if args.quiet:
        print(f"  {args.iso:5} contested={n:>6}  trust={100*acc['trust']/max(1,n):5.1f}%  "
              f"gloss={100*acc['always-gloss']/max(1,n):5.1f}%  eflomal={100*acc['always-eflomal']/max(1,n):5.1f}%  "
              f"oracle={100*acc['oracle']/max(1,n):5.1f}%")
        return 0
    print(f"\n=== contested decision accuracy — {args.iso} ({n} contested tokens) ===")
    for strat in ("trust", "always-gloss", "always-eflomal", "oracle"):
        print(f"  {strat:16} {100*acc[strat]/max(1,n):5.1f}%")
    print(f"  (trust should sit at/above the better of gloss/eflomal, approaching oracle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
