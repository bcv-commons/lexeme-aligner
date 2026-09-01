"""Residual re-alignment — run eflomal AGAIN on what the first pass could not explain.

The idea (2026-09-01): every protection tried so far acted on ALLOCATION — which source token gets
which target slot once eflomal has spoken — and docs/pipeline-overview.md records that all of them
failed or were negligible, because eflomal's output is already near the best allocation ITS MODEL
supports. The one lever that remains is the model itself. This is that lever, without needing any
external knowledge: strip the corpus down to what is still unexplained, and let eflomal learn a fresh
co-occurrence model on THAT.

Per verse the residual corpus keeps:
  source  — content tokens eflomal+gloss both missed (the gap set), nothing else;
  target  — tokens that are (a) not already consumed by a content pair, (b) not target stopwords, and
            (c) not "light renderings": word forms whose aligned mass goes predominantly to a
            semantically light source lexeme (config/light_lexemes.json). We have no target-side light
            list, so it is induced here from the taken pool — the target-side mirror of the source-side
            list, the same way target_stopwords induces target function words from the target's own text.

Why this can beat gap-fill on the same tokens: gap-fill can only fire from four hand-built priors
(strong-rollup / name-translit / cross-edition / phrase-adjacency) and reaches 13-30% of gaps at
24-29% precision. A residual eflomal pass has no such vocabulary restriction — it LEARNS the residual
distribution, and it learns it in a corpus where the high-frequency function words that dominate the
first pass are gone, so the co-occurrence statistics are not swamped by them.

Why it might not: the residual is small. Whether there is enough of it to estimate anything is the
first thing to measure, not to assume — see --stats.

    python3 -m lexeme_aligner.residual_align --iso fra --nt --usj-dir ... --stats   # size it first
    python3 -m lexeme_aligner.residual_align --iso fra --nt --usj-dir ...           # write the jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill import load_covered, load_priors
from lexeme_aligner.gloss_align import NORMALIZERS, Normalizer
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, build_corpus
from lexeme_aligner.target_stopwords import StopwordFilter, _load_light_lexemes
from lexeme_aligner.versification import remapper

_LIGHT_FORM_SHARE = 0.5     # a target form is a "light rendering" if >= this much of its mass is light


@dataclass
class _Rec:
    """A residual verse — the shape EflomalAligner.run/decode expects."""
    book: str
    ch: int
    v: int
    heb: list
    toks: list
    orig: list = field(default_factory=list)     # residual target index -> ORIGINAL verse position


def light_target_forms(iso: str, out_dir: Path, methods, light: set,
                       share: float = _LIGHT_FORM_SHARE) -> set[str]:
    """Induce the TARGET-side light list: forms whose aligned mass goes mostly to a light source lexeme.

    A share floor rather than "ever aligned to a light lexeme" matters: fra 'est' renders grc:1510 (light)
    most of the time but is a real content rendering elsewhere, while a form that merely brushed a light
    lexeme once must not be stripped from the whole corpus."""
    tot: collections.Counter = collections.Counter()
    lit: collections.Counter = collections.Counter()
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            for line in fp.open(encoding="utf-8"):
                for p in json.loads(line)["pairs"]:
                    if not p.get("content"):
                        continue
                    is_light = p.get("lexeme") in light
                    for w in (p.get("target") or "").lower().split():
                        tot[w] += 1
                        if is_light:
                            lit[w] += 1
    return {w for w, n in lit.items() if tot[w] and n / tot[w] >= share}


def build_residual(recs, covered_h, taken_t, stopwords, light_forms) -> list[_Rec]:
    out: list[_Rec] = []
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        if not r.toks:
            continue
        gap = [t for t in r.heb if t.strong and t.is_content and t.idx not in covered_h.get(ref, set())]
        if not gap:
            continue
        taken = taken_t.get(ref, set())
        keep = [j for j, w in enumerate(r.toks)
                if j not in taken
                and not (stopwords and stopwords.is_function(w))
                and w.lower() not in light_forms]
        if not keep:
            continue
        out.append(_Rec(r.book, r.ch, r.v, gap, [r.toks[j] for j in keep], keep))
    return out


def combine_with_gapfill(pairs_by_ref: dict, out_dir: Path, iso: str,
                         agree_score: float, fill_score: float) -> collections.Counter:
    """Stratify residual links against gap-fill's fills on the SAME tokens (measured 2026-09-01).

    Gap-fill and this pass reach the same token pool by completely independent routes — hand-built
    vocabulary priors versus a freshly-estimated model — so their agreement is a real confidence signal,
    the same reasoning merge_align already uses for eflomal/gloss. Three outcomes, each measured against
    Clear gold on fra/hin/eng:

      AGREE (both pick the same target)  57.1 / 81.1 / 58.8%  -> score `agree_score`. This BEATS gap-fill
                                          alone (47.1 / 59.9 / 44.2) by +10 / +21 / +15pt: the highest-
                                          confidence tier available on tokens that had no alignment.
      CONTESTED (both fire, differ)      gap-fill wins: 68.8 vs 50.5 (hin), 51.9 vs 40.7 (eng); fra is
                                          36.4 vs 34.1 at n=44, i.e. noise. So the residual link is
                                          DROPPED and gap-fill's own jsonl keeps the token.
      RESIDUAL-ONLY                      33.7 / 38.5 / 28.4% -> score `fill_score`, below hi_conf.

    Net over gap-fill alone: 3.2x / 2.2x / 6.6x more tokens reached and 2.6x / 1.8x / 4.6x more CORRECT
    fills, while the top tier is MORE precise than gap-fill was. That is the "higher confidence AND more
    tokens" combination — obtainable only because the tiers stay separately scored, never blended."""
    gf: dict = {}
    for fp in tag_files(out_dir, "gapfill", iso):
        for line in fp.open(encoding="utf-8"):
            rec = json.loads(line)
            for p in rec["pairs"]:
                gf[(rec["ref"], p["h_idx"])] = tuple(sorted(p.get("t_idx") or []))
    tally: collections.Counter = collections.Counter()
    for ref, rec in list(pairs_by_ref.items()):
        kept = []
        for p in rec["pairs"]:
            other = gf.get((ref, p["h_idx"]))
            if other is None:
                p["tier"] = "residual_only"
                p["score"] = fill_score
            elif other == tuple(sorted(p["t_idx"])):
                p["tier"] = "agree_gapfill"
                p["score"] = agree_score
            else:
                tally["contested_dropped"] += 1        # gap-fill wins; its jsonl already holds the token
                continue
            tally[p["tier"]] += 1
            kept.append(p)
        if kept:
            rec["pairs"] = kept
        else:
            del pairs_by_ref[ref]
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--publish-iso", default=None)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default="eflomal,gloss", help="passes that define 'already explained'")
    ap.add_argument("--no-strip-stopwords", action="store_true", help="ablation: keep target stopwords")
    ap.add_argument("--no-strip-light", action="store_true", help="ablation: keep light renderings")
    ap.add_argument("--explained-min-score", type=float, default=0.0,
                    help="a pair counts as 'already explained' only at or above this score. 0.0 (default) "
                         "= any pair, so the residual is purely what nothing touched. 0.9 = only "
                         "INTERSECTION-backed pairs (both alignment directions agreed) count, releasing "
                         "the unreliable 0.6 tier back into the residual — 3-5x more material, and it "
                         "re-decides exactly the tier we already know is weak")
    ap.add_argument("--keep-weak", action="store_true",
                    help="also emit residual links that are NOT intersection-backed. Default drops them: "
                         "measured gold precision of the two halves is 36.0/43.9/30.0%% (intersection) "
                         "vs 14.7/20.9/13.9%% (weak) on fra/hin/eng — a 2.1-2.4x separation, so the weak "
                         "half is mostly noise")
    ap.add_argument("--fill-score", type=float, default=0.75,
                    help="score written on a residual pair. DELIBERATELY below export_lex's hi_conf bar "
                         "of 0.9: the intersection-backed half measures 30-44%% against gold, which is "
                         "additive coverage, not high confidence. Same treatment gap-fill's phrase tier "
                         "gets for the same reason")
    ap.add_argument("--no-combine-gapfill", action="store_true",
                    help="emit every residual link instead of stratifying against gap-fill's own fills "
                         "on the same tokens — ablation switch, see combine_with_gapfill()")
    ap.add_argument("--agree-score", type=float, default=0.9,
                    help="score for a residual link an INDEPENDENT mechanism (gap-fill) picked too")
    ap.add_argument("--stats", action="store_true", help="size the residual corpus and exit")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    publish_iso = args.publish_iso or args.iso

    books = (OT_BOOKS + NT_BOOKS if args.all else OT_BOOKS if args.ot else NT_BOOKS if args.nt
             else [b.upper() for b in (args.book or ["RUT"])])
    methods = tuple(m.strip() for m in args.methods.split(","))
    heb = HebrewSource()
    recs = build_corpus(books, args.usj_dir, heb, remap=remapper(args.iso, str(args.usj_dir)))
    stopwords = None if args.no_strip_stopwords else StopwordFilter(publish_iso, str(args.usj_dir))
    lex_pos, _ = load_priors(PRIOR_PACK)
    covered_h, taken_t, _anchors, _ss, _tp = load_covered(args.iso, args.out, methods,
                                                          args.explained_min_score, lex_pos)
    light_forms = (set() if args.no_strip_light
                   else light_target_forms(args.iso, args.out, methods, _load_light_lexemes()))
    res = build_residual(recs, covered_h, taken_t, stopwords, light_forms)

    n_src = sum(len(r.heb) for r in res)
    n_trg = sum(len(r.toks) for r in res)
    orig_trg = sum(len(r.toks) for r in recs)
    print(f"[residual] {args.iso}: {len(res)} verses with an unexplained source token\n"
          f"  residual SOURCE tokens {n_src}   residual TARGET tokens {n_trg} "
          f"(of {orig_trg} original, {100*n_trg/max(1,orig_trg):.1f}%)\n"
          f"  stripped: {0 if stopwords is None else len(stopwords.words)} stopword forms, "
          f"{len(light_forms)} light-rendering forms\n"
          f"  mean per residual verse: {n_src/max(1,len(res)):.2f} source, {n_trg/max(1,len(res)):.2f} target",
          file=sys.stderr)
    if args.stats:
        return 0

    from lexeme_aligner.eflomal_align import EflomalAligner
    norm: Normalizer = NORMALIZERS.get(publish_iso, Normalizer())
    eflo = EflomalAligner()
    eflo.run(res, norm)

    by_ref: dict = {}
    for r in res:
        pairs = []
        for m in eflo.decode(r):
            if m.score < 0.9 and not args.keep_weak:      # drop the noisy half — see --keep-weak
                continue
            t = next((h for h in r.heb if h.idx == m.h_idx), None)
            if not t:
                continue
            orig_idx = [r.orig[j] for j in m.t_idx if j < len(r.orig)]
            if not orig_idx:
                continue
            pairs.append({"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma,
                          "stem": t.stem, "surface": t.surface, "gloss_en": t.gloss_en,
                          "sense": t.sense, "target": " ".join(r.toks[j] for j in m.t_idx),
                          "t_idx": sorted(orig_idx), "score": args.fill_score, "method": "residual",
                          "content": True})
        if pairs:
            by_ref[encode(r.book, r.ch, r.v)] = {"ref": encode(r.book, r.ch, r.v), "book": r.book,
                                                 "chapter": r.ch, "verse": r.v, "pairs": pairs}
    tally: collections.Counter = collections.Counter()
    if not args.no_combine_gapfill:
        tally = combine_with_gapfill(by_ref, args.out, args.iso, args.agree_score, args.fill_score)
    n_pairs = sum(len(r["pairs"]) for r in by_ref.values())
    by_book: dict[str, list] = collections.defaultdict(list)
    for rec in by_ref.values():
        by_book[rec["book"]].append(rec)
    for fp in tag_files(args.out, "residual", args.iso):
        fp.unlink()
    for book, out_recs in by_book.items():
        out_recs.sort(key=lambda x: (x["chapter"], x["verse"]))
        with (args.out / f"align_residual_{args.iso}_{book}.jsonl").open("w", encoding="utf-8") as fh:
            for x in out_recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"[residual] aligned {n_pairs} of {n_src} unexplained source tokens "
          f"({100*n_pairs/max(1,n_src):.1f}%)" +
          (f" · tiers {dict(tally)}" if tally else "") +
          f" → align_residual_{args.iso}_*.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
