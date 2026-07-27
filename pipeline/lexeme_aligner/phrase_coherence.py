"""Phrase-coherence — a source-anchored confidence signal for existing alignments (BHSA `phrase_id`,
2026-07-25 spine, OT-only).

Tokens of one source phrase ("Spirit-of-God", "the-sons-of-Israel") almost always render close
together in the target. So for every aligned CONTENT token whose phrase has >=2 aligned members,
measure the distance between its own target position and its nearest phrase-mate's target position:

  coherent   — within `threshold` target tokens of a mate (the phrase landed as a unit)
  scattered  — no mate within the threshold (this token's alignment points somewhere else entirely)

A scattered pair is *suspect* — exactly the shape of the wrong pairs the PSA 23:1 spot check caught by
hand (a token force-aligned to leftover material far from where its phrase actually landed). This
module VALIDATES that suspicion against Clear gold: if scattered pairs are markedly less precise than
coherent ones, coherence is a real confidence tier (usable to demote/flag pairs downstream), not just
an aesthetic. Judged per method and per eflomal score tier, so the value ON TOP of the existing
confidence tiers is visible — a signal that merely restates "score 0.6 is worse than 0.9" adds nothing.

    python3 -m lexeme_aligner.phrase_coherence --iso bsb --gold-iso eng --usj-dir pipeline/work/ingest-cache/usj-eng
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import norm_surface
from lexeme_aligner.config import OUT, RESOURCES
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import OT_BOOKS, build_corpus
from lexeme_aligner.versification import remapper


def load_gold(gold_iso: str, res_dir: Path) -> dict:
    import pyarrow.parquet as pq
    fp = res_dir / "strongs" / "attestations" / f"{gold_iso}.parquet"
    if not fp.exists():
        raise SystemExit(f"no Clear gold for {gold_iso} at {fp}")
    gold: dict = collections.defaultdict(set)
    t = pq.read_table(fp, columns=["ref", "strong", "surface"]).to_pydict()
    for ref, strong, surf in zip(t["ref"], t["strong"], t["surface"]):
        gold[(str(ref), strong)].add(norm_surface(surf))
    return gold


def measure(tag: str, usj_dir: Path, gold: dict, out_dir: Path = OUT,
            methods=("eflomal", "gloss"), threshold: int = 3) -> dict:
    heb = HebrewSource()
    recs = build_corpus(OT_BOOKS, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    phrase_of = {}                                # (ref, h_idx) -> phrase_id
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        for t in r.heb:
            if t.phrase_id and t.strong and t.is_content:
                phrase_of[(ref, t.idx)] = t.phrase_id

    # bucket -> [judged, correct]; bucket = (method, tier, coherent|scattered|no-mate)
    tally: dict = collections.defaultdict(lambda: [0, 0])
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            if fp.stem.rsplit("_", 1)[-1] not in set(OT_BOOKS):
                continue
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                ref = rec["ref"]
                pos, phr = {}, {}
                for p in rec["pairs"]:
                    ti = p.get("t_idx")
                    if p.get("content") and ti and (ref, p["h_idx"]) in phrase_of:
                        pos[p["h_idx"]] = ti[0]
                        phr[p["h_idx"]] = phrase_of[(ref, p["h_idx"])]
                by_phrase: dict = collections.defaultdict(list)
                for h_idx, pid in phr.items():
                    by_phrase[pid].append(h_idx)
                for p in rec["pairs"]:
                    h_idx = p["h_idx"]
                    if h_idx not in pos or not p.get("strong") or not (p.get("target") or "").strip():
                        continue
                    mates = [x for x in by_phrase[phr[h_idx]] if x != h_idx]
                    if not mates:
                        bucket = "no-mate"
                    else:
                        d = min(abs(pos[h_idx] - pos[x]) for x in mates)
                        bucket = "coherent" if d <= threshold else "scattered"
                    key = (f"{ref:08d}", p["strong"])
                    if key not in gold:
                        continue
                    tier = f"{m}:{p.get('score')}" if m == "eflomal" else f"{m}:{p.get('method', '?')}"
                    cell = tally[(tier, bucket)]
                    cell[0] += 1
                    cell[1] += any(norm_surface(w) in gold[key] for w in p["target"].split())
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="produced TAG")
    ap.add_argument("--gold-iso", required=True)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--methods", default="eflomal,gloss")
    ap.add_argument("--threshold", type=int, default=3)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    args = ap.parse_args()

    gold = load_gold(args.gold_iso, args.resources)
    tally = measure(args.iso, args.usj_dir, gold, args.out_dir,
                    tuple(m.strip() for m in args.methods.split(",")), args.threshold)

    print(f"\n=== phrase-coherence vs gold — {args.iso} (gold={args.gold_iso}, "
          f"threshold={args.threshold}) ===", file=sys.stderr)
    print(f"  {'tier':22} {'bucket':10} {'judged':>8} {'precision':>10}", file=sys.stderr)
    for (tier, bucket) in sorted(tally):
        n, c = tally[(tier, bucket)]
        if n >= 30:
            print(f"  {tier:22} {bucket:10} {n:>8} {100*c/n:>9.1f}%", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
