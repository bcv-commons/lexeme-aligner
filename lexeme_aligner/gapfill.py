"""Gap-fill driver — fills content tokens eflomal + gloss both missed. Fully model-free (see
gapfill_align.py docstring for why the earlier neural/embedding approach was retired).

Runs ONLY on the source tokens eflomal+gloss both missed, and only onto the target positions those modes
left UNTAKEN. Writes a gap-only `align_gapfill_<iso>_*.jsonl` that `merge_align`/`export_lex` pick up like
any other method — so it adds coverage in the holes but never out-votes eflomal/gloss (no vote where they
already have one).

Support signals fed in (all extracted algorithmically from already-established data — no model, no
download, works on any language with a Bible):
  • covered (ref, h_idx) from eflomal+gloss   → which source tokens still need a signal (the gaps)
  • taken target positions from their `t_idx`  → constrain fill to leftover targets (bijection prior)
  • #3 target function-words (target_stopwords) → excluded from the candidate pool
  • #1 cross-lingual span profile (cross_lang_prior) → extends compound-lexeme fills to their neighbor
  • #4 cross-edition vocab (lexeme-alignments/iso=<iso>) → a known surface of the gap's LEXEME from
    ANOTHER pooled edition of the SAME language (not just this translation's own eflomal+gloss run) —
    see gapfill_align.py's docstring. Defaults to this same --iso's own published pool; --cross-edition-iso
    to point elsewhere; --no-cross-edition to disable. Silently skipped if nothing's been exported yet.

    python3 -m lexeme_aligner.gapfill --iso fra --all --usj-dir data/usj-fra-lsg
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files as _tag_files
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill_align import GapFiller
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.reverse_align_check import load_lexeme_vocab
from lexeme_aligner.run_pilot import build_corpus, OT_BOOKS, NT_BOOKS
from lexeme_aligner.target_stopwords import StopwordFilter
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


def load_covered(iso: str, out_dir: Path, methods, min_score: float, lex_pos: dict,
                 topk_strong: int = 5, min_surface_share: float = 0.1):
    """From the other modes' jsonl (the 'taken pool'), extract the gap-fill support signals:
      covered_h[ref]  = source h_idx already aligned      (→ what still needs a signal)
      taken_t[ref]    = target positions already consumed (→ untaken-only constraint)
      anchors[ref]    = {covered h_idx: target pos}        (→ positional/diagonal prior)
      strong_surf     = {strong: {top target words}}       (→ strong-rollup back-off)
      target_pos      = {target word: majority source POS} (→ BOOTSTRAPPED target POS, grammatical prior)

    `min_surface_share`: a word only counts as a Strong's "known surface" if it represents ≥ this share of
    that Strong's OWN aligned occurrences — not just raw top-5 count. Verified necessary: a high-frequency
    word (fra 'est'/'is', 'par'/'by', 'd'\\'/elision) can pick up a THIN eflomal co-occurrence sliver with an
    unrelated Strong's (H0430/God: 'd' at 1.0% share; H3478/Israel: 'd' at 0.1%) purely from noise — global
    stopword filtering (#3) can't catch these since the word IS legitimately content elsewhere (fra 'est'
    genuinely renders grc:1510 'to be' at 77% share). The floor is per-Strong's, so a word can be a known
    surface for its true partner while excluded everywhere else it merely brushed against by chance."""
    covered_h: dict[int, set] = collections.defaultdict(set)
    taken_t: dict[int, set] = collections.defaultdict(set)
    anchors: dict[int, dict] = collections.defaultdict(dict)
    strong_words: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    tpos: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for m in methods:
        for fp in _tag_files(out_dir, m, iso):
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
    strong_surf = {}
    for s, c in strong_words.items():
        total = sum(c.values())
        kept = {w for w, n in c.most_common(topk_strong) if total and n / total >= min_surface_share}
        if kept:
            strong_surf[s] = kept
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
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK, help="for pos + translit priors")
    ap.add_argument("--cross-lang", type=Path, default=Path("resources/cross_lang_prior/profile.json"),
                    help="#1 cross-lingual span-length profile (cross_lang_prior.py); '' to disable")
    ap.add_argument("--multiword-floor", type=float, default=0.6,
                    help="extend a hi-conf single-token fill to its neighbor when the OTHER languages we've "
                         "aligned render this lexeme as a phrase at least this often")
    ap.add_argument("--cross-edition-iso", default=None,
                    help="#4: iso to load the cross-edition vocab from (default: --iso's own published pool)")
    ap.add_argument("--no-cross-edition", action="store_true", help="disable prior #4")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    books = (OT_BOOKS + NT_BOOKS if args.all else OT_BOOKS if args.ot else NT_BOOKS if args.nt
             else [b.upper() for b in (args.book or ["RUT"])])
    methods = tuple(m.strip() for m in args.methods.split(","))
    heb = HebrewSource()
    recs = build_corpus(books, args.usj_dir, heb, remap=remapper(args.iso, str(args.usj_dir)))  # auto-detected scheme, match eflomal/gloss numbering
    stopwords = StopwordFilter(args.iso, str(args.usj_dir))   # #3: target function-word gate (cached)
    lex_pos, lex_translit = load_priors(args.prior_pack)
    covered_h, taken_t, anchors, strong_surf, target_pos = load_covered(
        args.iso, args.out, methods, args.min_score, lex_pos)
    cross_lang = (json.loads(args.cross_lang.read_text(encoding="utf-8"))
                 if args.cross_lang and args.cross_lang.exists() else {})
    cross_edition_vocab = {}
    if not args.no_cross_edition:
        try:
            cross_edition_vocab = load_lexeme_vocab(args.cross_edition_iso or args.iso, hi_conf_only=True)
        except SystemExit as e:
            print(f"[gapfill] #4 cross-edition vocab unavailable ({e}) — skipping that prior", file=sys.stderr)
    filler = GapFiller()
    print(f"[gapfill] {args.iso}: {len(recs)} verses · covered-by {methods}\n"
          f"  priors: {len(strong_surf)} strong-surfaces · {len(target_pos)} bootstrapped target-POS · "
          f"{len(lex_pos)} lexeme-POS · {len(lex_translit)} translit · positional · "
          f"{len(stopwords.words)} target function-words (#3, gated out) · "
          f"{len(cross_lang)} cross-lingual span profiles (#1, floor={args.multiword_floor}) · "
          f"{len(cross_edition_vocab)} cross-edition lexeme-vocab entries (#4, hi_conf-only, "
          f"from iso={args.cross_edition_iso or args.iso})",
          file=sys.stderr)

    for fp in _tag_files(args.out, "gapfill", args.iso):
        fp.unlink()

    by_book: dict[str, list] = collections.defaultdict(list)
    prior_counts: collections.Counter = collections.Counter()
    n_gap = n_filled = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        gap_idx = {t.idx for t in r.heb if t.strong and t.is_content} - covered_h.get(ref, set())
        if not gap_idx or not r.toks:
            continue
        n_gap += len(gap_idx)
        matches = filler.align_gap(r.heb, r.toks, gap_idx, taken_t.get(ref, set()),
                                   strong_surfaces=strong_surf, anchors=anchors.get(ref),
                                   lex_pos=lex_pos, lex_translit=lex_translit, target_pos=target_pos,
                                   stopwords=stopwords, cross_lang=cross_lang,
                                   multiword_floor=args.multiword_floor,
                                   cross_edition_vocab=cross_edition_vocab)
        pairs = []
        for m, prior in matches:
            t = next((h for h in r.heb if h.idx == m.h_idx), None)
            if not t:
                continue
            pairs.append({"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma,
                          "stem": t.stem, "surface": t.surface, "gloss_en": t.gloss_en, "sense": t.sense,
                          "target": " ".join(r.toks[j] for j in m.t_idx), "t_idx": list(m.t_idx),
                          "score": m.score, "method": "gapfill", "content": True, "prior": prior})
        if pairs:
            n_filled += len(pairs)
            for p in pairs:
                prior_counts[p["prior"]] += 1
            by_book[r.book].append({"ref": ref, "book": r.book, "chapter": r.ch, "verse": r.v,
                                    "pairs": pairs})

    for book, out_recs in by_book.items():
        out_recs.sort(key=lambda x: (x["chapter"], x["verse"]))
        with (args.out / f"align_gapfill_{args.iso}_{book}.jsonl").open("w", encoding="utf-8") as fh:
            for x in out_recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"[gapfill] {n_gap} gap tokens · filled {n_filled} ({100*n_filled/max(1,n_gap):.1f}%) by prior "
          f"{dict(prior_counts)} → align_gapfill_{args.iso}_*.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
