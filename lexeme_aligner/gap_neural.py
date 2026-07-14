"""Gap-targeted neural — the smart, cheap way to use neural (see benchmarks: full neural dilutes gloss).

Run neural ONLY on the source tokens eflomal+gloss both missed, and only onto the target positions those
modes left UNTAKEN. Writes a gap-only `align_neural_<iso>_*.jsonl` that the existing `merge_align` picks
up — so neural adds coverage in the holes but never out-votes gloss (it has no vote where the others do).

Support signals fed in (all extracted algorithmically from already-established data):
  • covered (ref, h_idx) from eflomal+gloss   → which source tokens still need a signal (the gaps)
  • taken target positions from their `t_idx`  → constrain neural to leftover targets (bijection prior;
                                                 also skips the function-word hubs the others consumed)

    HF_HUB_OFFLINE=1 python3 -m lexeme_aligner.gap_neural --iso fra --all \\
      --usj-dir data/usj-fra-lsg --neural-model BAAI/bge-m3 --neural-layer 16 --neural-device mps
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import build_corpus, OT_BOOKS, NT_BOOKS
from lexeme_aligner.versification import remapper


def load_priors(prior_pack: Path):
    """prior-pack → (lexeme→pos, lexeme→translit) for the grammatical + name-transliteration priors."""
    if not Path(prior_pack).exists():
        return {}, {}
    import pyarrow.parquet as pq
    cols = pq.read_schema(prior_pack).names
    if "pos" not in cols:                                          # older pack without the new columns
        return {}, {}
    rows = pq.read_table(prior_pack, columns=["lexeme", "pos", "translit"]).to_pylist()
    return ({r["lexeme"]: r["pos"] for r in rows if r.get("pos")},
            {r["lexeme"]: r["translit"] for r in rows if r.get("translit")})


def load_covered(iso: str, out_dir: Path, methods, min_score: float, lex_pos: dict, topk_strong: int = 5):
    """From the other modes' jsonl (the 'taken pool'), extract the gap-neural support signals:
      covered_h[ref]  = source h_idx already aligned      (→ what still needs a signal)
      taken_t[ref]    = target positions already consumed (→ untaken-only constraint)
      anchors[ref]    = {covered h_idx: target pos}        (→ positional/diagonal prior)
      strong_surf     = {strong: {top target words}}       (→ strong-rollup back-off)
      target_pos      = {target word: majority source POS} (→ BOOTSTRAPPED target POS, grammatical prior)"""
    covered_h: dict[int, set] = collections.defaultdict(set)
    taken_t: dict[int, set] = collections.defaultdict(set)
    anchors: dict[int, dict] = collections.defaultdict(dict)
    strong_words: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    tpos: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in methods:
        for fp in sorted(out_dir.glob(f"align_{m}_{iso}_*.jsonl")):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    for p in rec["pairs"]:
                        if not (p.get("content") and (p.get("target") or "").strip()
                                and (p.get("score") or 0) >= min_score):
                            continue
                        covered_h[ref].add(p["h_idx"])
                        ti = p.get("t_idx") or []
                        for j in ti:
                            taken_t[ref].add(j)
                        if ti:
                            anchors[ref].setdefault(p["h_idx"], ti[0])
                        words = (p.get("target") or "").lower().split()
                        if p.get("strong"):
                            for w in words:
                                strong_words[p["strong"]][w] += 1
                        pos = lex_pos.get(p.get("lexeme"))          # source POS → vote for the target word's POS
                        if pos:
                            for w in words:
                                tpos[w][pos] += 1
    strong_surf = {s: {w for w, _ in c.most_common(topk_strong)} for s, c in strong_words.items()}
    target_pos = {w: c.most_common(1)[0][0] for w, c in tpos.items()}
    return covered_h, taken_t, anchors, strong_surf, target_pos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true", help="OT+NT")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default="eflomal,gloss", help="modes that define 'covered'")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--neural-model", default="BAAI/bge-m3")
    ap.add_argument("--neural-layer", type=int, default=16)
    ap.add_argument("--neural-device", default="auto")
    ap.add_argument("--threshold", type=float, default=0.4)
    ap.add_argument("--accept-priors", default="strong,name",
                    help="which gap-fill priors to WRITE (publish-safe default: keep strong+name — the "
                         "50-100%% precision fills — and DROP embedding-only, ~5-38%% precision noise). "
                         "Pass 'strong,name,embedding' to keep all (e.g. for the coverage/confidence signal).")
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK, help="for pos + translit priors")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    from lexeme_aligner.neural_align import NeuralAligner
    books = (OT_BOOKS + NT_BOOKS if args.all else OT_BOOKS if args.ot else NT_BOOKS if args.nt
             else [b.upper() for b in (args.book or ["RUT"])])
    methods = tuple(m.strip() for m in args.methods.split(","))
    accept = {p.strip() for p in args.accept_priors.split(",") if p.strip()}
    heb = HebrewSource()
    recs = build_corpus(books, args.usj_dir, heb, remap=remapper(args.iso, str(args.usj_dir)))  # auto-detected scheme, match eflomal/gloss numbering
    lex_pos, lex_translit = load_priors(args.prior_pack)
    covered_h, taken_t, anchors, strong_surf, target_pos = load_covered(
        args.iso, args.out, methods, args.min_score, lex_pos)
    neu = NeuralAligner(model_name=args.neural_model, layer=args.neural_layer,
                        device=args.neural_device, threshold=args.threshold)
    print(f"[gap_neural] {args.iso}: {len(recs)} verses · covered-by {methods} · device={neu.device}\n"
          f"  priors: {len(strong_surf)} strong-surfaces · {len(target_pos)} bootstrapped target-POS · "
          f"{len(lex_pos)} lexeme-POS · {len(lex_translit)} translit · positional\n"
          f"  accept-priors={sorted(accept)} "
          f"({'PUBLISH-SAFE: dropping embedding-only' if 'embedding' not in accept else 'keeping ALL incl. noisy embedding'})",
          file=sys.stderr)

    # clear any prior (full) neural jsonl so `neural` becomes gap-only
    for fp in args.out.glob(f"align_neural_{args.iso}_*.jsonl"):
        fp.unlink()

    by_book: dict[str, list] = collections.defaultdict(list)
    prior_counts: collections.Counter = collections.Counter()
    n_gap = n_filled = n_dropped = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        gap_idx = {t.idx for t in r.heb if t.strong and t.is_content} - covered_h.get(ref, set())
        if not gap_idx or not r.toks:
            continue
        n_gap += len(gap_idx)
        matches = neu.align_gap(r.heb, r.toks, gap_idx, taken_t.get(ref, set()),
                                strong_surfaces=strong_surf, anchors=anchors.get(ref),
                                lex_pos=lex_pos, lex_translit=lex_translit, target_pos=target_pos)
        pairs = []
        for m, prior in matches:
            if prior not in accept:                       # publish-safe gate: drop noisy embedding-only fills
                n_dropped += 1
                continue
            t = next((h for h in r.heb if h.idx == m.h_idx), None)
            if not t:
                continue
            pairs.append({"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma,
                          "stem": t.stem, "surface": t.surface, "gloss_en": t.gloss_en, "sense": t.sense,
                          "target": " ".join(r.toks[j] for j in m.t_idx), "t_idx": list(m.t_idx),
                          "score": m.score, "method": "neural", "content": True, "prior": prior})
        if pairs:
            n_filled += len(pairs)
            for p in pairs:
                prior_counts[p["prior"]] += 1
            by_book[r.book].append({"ref": ref, "book": r.book, "chapter": r.ch, "verse": r.v,
                                    "pairs": pairs})

    for book, out_recs in by_book.items():
        out_recs.sort(key=lambda x: (x["chapter"], x["verse"]))
        with (args.out / f"align_neural_{args.iso}_{book}.jsonl").open("w", encoding="utf-8") as fh:
            for x in out_recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"[gap_neural] {n_gap} gap tokens · filled {n_filled} ({100*n_filled/max(1,n_gap):.1f}%) by prior "
          f"{dict(prior_counts)} · dropped {n_dropped} below accept-priors → align_neural_{args.iso}_*.jsonl",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
