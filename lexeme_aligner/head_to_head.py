"""eflomal ↔ gloss head-to-head — where does eflomal's trust break down so gloss should take over?

On the SAME source tokens (joined by ref+h_idx), classify every scorable token:
  • AGREE     — both picked the same target  → the trusted core (expect very high precision).
  • DISAGREE  — different targets            → who matches gold, bucketed by (eflomal tier × gloss tier)?
  • ef-only / gloss-only                     → solo precision of each mode where the other is silent.

The disagreement table is the cutoff evidence: in cells where gloss wins, eflomal should be demoted (or
down-weighted) and gloss trusted — the "eflomal cutoff down, gloss 2nd run" decision, made empirically.
Positional Clear gold only (fra/arb/eng/hau).

    python3 -m lexeme_aligner.head_to_head --iso fra
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import norm_surface
from lexeme_aligner.config import OUT, RESOURCES
from lexeme_aligner.score_tiers import _gold_clear, _tier


def _index(iso: str, method: str, out_dir: Path) -> dict:
    """(ref, h_idx) → (norm target words, tier, strong) for one mode."""
    idx: dict[tuple, tuple] = {}
    for fp in tag_files(out_dir, method, iso):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content") and p.get("strong") and (p.get("target") or "").strip():
                        idx[(rec["ref"], p["h_idx"])] = (
                            tuple(norm_surface(w) for w in p["target"].split()), _tier(method, p), p["strong"])
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    args = ap.parse_args()

    gold = _gold_clear(args.iso, args.resources)
    ef = _index(args.iso, "eflomal", args.out)
    gl = _index(args.iso, "gloss", args.out)

    def hit(ref, strong, words):
        return any(w in gold.get((f"{ref:08d}", strong), ()) for w in words)

    def judged(ref, strong):
        return (f"{ref:08d}", strong) in gold

    agree = [0, 0]                                             # [n, correct]
    ef_solo, gl_solo = [0, 0], [0, 0]
    dis = collections.defaultdict(lambda: [0, 0, 0, 0])       # (ef_tier, gl_tier) -> [n, ef_win, gl_win, both_wrong]
    for key in set(ef) | set(gl):
        ref, _h = key
        e, g = ef.get(key), gl.get(key)
        strong = (e or g)[2]
        if not judged(ref, strong):
            continue
        if e and g:
            eh, gh = hit(ref, strong, e[0]), hit(ref, strong, g[0])
            if e[0] == g[0]:
                agree[0] += 1
                agree[1] += eh
            else:
                d = dis[(e[1], g[1])]
                d[0] += 1
                d[1] += eh and not gh
                d[2] += gh and not eh
                d[3] += not eh and not gh
        elif e:
            ef_solo[0] += 1
            ef_solo[1] += hit(ref, strong, e[0])
        else:
            gl_solo[0] += 1
            gl_solo[1] += hit(ref, strong, g[0])

    print(f"\n=== eflomal ↔ gloss head-to-head — {args.iso} ===")
    print(f"  AGREE (same target)  : {agree[0]:>7}  precision {100*agree[1]/max(1,agree[0]):.1f}%  ← trusted core")
    print(f"  eflomal-only (solo)  : {ef_solo[0]:>7}  precision {100*ef_solo[1]/max(1,ef_solo[0]):.1f}%")
    print(f"  gloss-only  (solo)   : {gl_solo[0]:>7}  precision {100*gl_solo[1]/max(1,gl_solo[0]):.1f}%")
    tot = sum(d[0] for d in dis.values())
    ew = sum(d[1] for d in dis.values())
    gw = sum(d[2] for d in dis.values())
    print(f"  DISAGREE             : {tot:>7}  eflomal-wins {100*ew/max(1,tot):.1f}%  "
          f"gloss-wins {100*gw/max(1,tot):.1f}%  (both-wrong {100*sum(d[3] for d in dis.values())/max(1,tot):.1f}%)")
    print(f"\n  disagreement by (eflomal tier × gloss tier) — who to trust:")
    print(f"  {'ef-tier':12} {'gloss-tier':10} {'n':>6} {'ef-win':>7} {'gl-win':>7}  verdict")
    for (et, gt), d in sorted(dis.items(), key=lambda kv: -kv[1][0]):
        if d[0] >= args.min_n:
            ewr, gwr = 100 * d[1] / d[0], 100 * d[2] / d[0]
            verdict = "→ trust GLOSS" if gwr > ewr + 10 else "→ trust eflomal" if ewr > gwr + 10 else "~ tie"
            print(f"  {et:12} {gt:10} {d[0]:>6} {ewr:>6.1f}% {gwr:>6.1f}%  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
