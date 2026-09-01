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
    see gapfill_align.py's docstring. Defaults to --publish-iso's own published pool (NOT --iso — the
    tag is never what lexeme-alignments is keyed on when it differs from the bare iso); --cross-edition-iso
    to point elsewhere; --no-cross-edition to disable. Silently skipped if nothing's been exported yet.

    python3 -m lexeme_aligner.gapfill --iso fra --all --usj-dir pipeline/work/ingest-cache/usj-fra-lsg
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
                 topk_strong: int = 5, min_surface_share: float = 0.1,
                 release_light: bool = True, reserve_function_slots: bool = False):
    """From the other modes' jsonl (the 'taken pool'), extract the gap-fill support signals:
      covered_h[ref]  = source h_idx already aligned      (→ what still needs a signal)
      taken_t[ref]    = target positions already consumed (→ untaken-only constraint)
      anchors[ref]    = {covered h_idx: target pos}        (→ positional/diagonal prior)
      strong_surf     = {strong: {top target words}}       (→ strong-rollup back-off)
      target_pos      = {target word: majority source POS} (→ BOOTSTRAPPED target POS, grammatical prior)

    `release_light` (#3, docs/pipeline-overview.md): a pair whose source lexeme is semantically LIGHT
    (config/light_lexemes.json — copulas, `all`, `have`, `one`) stays in `covered_h`, so gap-fill never
    wastes a fill trying to re-align it, but its target positions are NOT added to `taken_t` — so a real
    content gap may claim the slot it is sitting on. This is the asymmetry the project actually wants:
    we do not care about getting light words right, only about them not holding a slot a real match
    needed. Light pairs are also kept out of `anchors` and out of `strong_surf`/`target_pos`, since a
    slot that may move is a bad positional anchor and a light lexeme's rendering is a bad vocabulary
    exemplar. Measured: these lexemes land ENTIRELY on target stopwords 55.7/76.0/62.2% of the time
    (fra/hin/eng) against a 6.3/10.3/6.6% all-content base rate.

    `reserve_function_slots` (#5, docs/pipeline-overview.md): a NON-CONTENT source pair (waw, article,
    ὁ, καί, prepositions) is dropped at export, so this used to skip it entirely — leaving the target
    position it consumed looking FREE to gap-fill, which could then claim it while eflomal still held it.
    Two source tokens then claimed one target position with nothing recording the conflict. Reserving the
    position looked like the better direction: gap-fill fills landing on a function-word-held slot score
    WORSE than fills onto a genuinely free one in all three gold languages (fra 46.2% vs 47.8%, hin 51.9%
    vs 65.3%, eng 43.9% vs 44.8%).

    DEFAULT OFF ANYWAY — the ablation says the signal is real but far too small to act on. Reserving cuts
    gap fills by 29-42% (fra 286->204, hin 717->459, eng 174->101) for a token-precision change of
    +0.0/+0.1/+0.0: gap fills are only ~0.2-0.7% of all claimed target tokens, so removing even their
    worst tier cannot move the aggregate. And the double-claim it prevents is INTERNAL only — non-content
    rows are dropped at export and compact-alignments indexes content lexemes exclusively, so no consumer
    ever sees the conflict. Losing a third of gap-fill's coverage to tidy an invisible wart is a bad
    trade. Enable with `--reserve-function-slots` if a future consumer needs the guarantee.

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
    n_light_released = [0]
    for m in methods:
        for fp in _tag_files(out_dir, m, iso):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    for p in rec["pairs"]:
                        if not ((p.get("target") or "").strip()
                                and (p.get("score") or 0) >= min_score):
                            continue
                        if not p.get("content"):
                            # never a gap and never a vocabulary exemplar — but it IS holding a slot
                            if reserve_function_slots:
                                taken_t[ref].update(p.get("t_idx") or [])
                            continue
                        covered_h[ref].add(p["h_idx"])
                        ti = p.get("t_idx") or []
                        if release_light and p.get("light"):
                            n_light_released[0] += len(ti)
                            continue          # covered (never re-filled), but its slot is released
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
    if n_light_released[0]:
        print(f"[gapfill] #3 light-lexeme slot release: {n_light_released[0]} target position(s) "
              f"freed from semantically-light source tokens", file=sys.stderr)
    return covered_h, taken_t, anchors, strong_surf, target_pos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--publish-iso", default=None,
                    help="the bare published iso, when --iso is an edition TAG that differs from it "
                         "(e.g. --iso arb_vdv --publish-iso arb) — the #3 stopword filter and the #4 "
                         "cross-edition vocab default both read/cache against THIS iso's published "
                         "lexeme-alignments/target-stopwords, not the tag's (which is never published "
                         "under that key). Defaults to --iso, so single-edition/grandfathered calls "
                         "are unaffected.")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true", help="OT+NT")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default="eflomal,gloss", help="modes that define 'covered'")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK, help="for pos + translit priors")
    # DEFAULT OFF since 2026-08-31 — the shipped profile cannot fire and carries no signal:
    #   * only 7 of 14,040 lexemes (0.05%) clear `multiword_floor` 0.6; the profile's MAXIMUM rate is
    #     0.683, so the extension is unreachable for ~everything.
    #   * validated against Clear gold spans for the first time (1,912 lexemes, >=20 gold occurrences):
    #     our mean rate 0.081 vs gold 0.756, correlation r=0.075 — no per-lexeme signal, not merely a
    #     scale offset. Cause: it is learned from OUR OWN alignments, which structurally under-fill
    #     (one target + an optional rightward +1, blocked by the stopword gate), so it measures our own
    #     limitation rather than the phenomenon.
    #   * A/B on 9 gold languages, on vs off: byte-identical scorable/correct/precision in all 9.
    # The MECHANISM is kept and still works, but DO NOT go looking for a better profile: controls
    # (2026-08-31, 9 gold languages, 6,547 scorable fills) showed the per-lexeme rates are worth -3 fills
    # and a profile's coverage a further -5. "Always extend by one" (every spine lexeme at rate 1.0, no
    # external data) scored 3,132 correct vs 3,127 for the best real profile and 2,935 with no prior at
    # all. The whole gain is that extension is too CONSERVATIVE — capped at +1, rightward-only, and with
    # the stopword gate blocking 85.2% of legitimate span members — not that we lack a span predictor.
    # See internal-docs/subject-fusion-span-prior.md §0d before building anything here.
    ap.add_argument("--cross-lang", type=Path, default=None,
                    help="span-length profile for the #1 extension prior; default OFF — the shipped "
                         "cross-lingual-span-profile was measured to carry no signal (see code comment)")
    ap.add_argument("--max-extend", type=int, default=1,
                    help="how many target tokens a fill may absorb beyond its own (each step tries RIGHT "
                         "then LEFT). Gold: a source content token takes 2.18 target words on average and "
                         "is multi-word 76.6%% of the time, so 1 is likely too conservative")
    ap.add_argument("--extend-over-stopwords", action="store_true",
                    help="let an extension absorb a target STOPWORD. 85.2%% of gold targets our stopword "
                         "lists flag are span MEMBERS (not sole targets), so the gate blocks legitimate "
                         "span material — see internal-docs/subject-fusion-span-prior.md 0b/0c")
    ap.add_argument("--multiword-floor", type=float, default=0.6,
                    help="extend a hi-conf single-token fill to its neighbor when the OTHER languages we've "
                         "aligned render this lexeme as a phrase at least this often")
    ap.add_argument("--cross-edition-iso", default=None,
                    help="#4: iso to load the cross-edition vocab from (default: --publish-iso's own "
                         "published pool)")
    ap.add_argument("--no-cross-edition", action="store_true", help="disable prior #4")
    ap.add_argument("--no-phrase", action="store_true",
                    help="disable the BHSA phrase-syntax prior (placement + last-resort fills) — ablation switch")
    ap.add_argument("--no-func-order", action="store_true",
                    help="disable cross-phrase func-order fallback only (keep within-phrase 'phrase' prior) — ablation switch")
    ap.add_argument("--reserve-function-slots", action="store_true",
                    help="#5 (default OFF, measured not worth it): stop gap-fill claiming a target "
                         "position a non-content source token already holds. Prevents an internal "
                         "double-claim, but costs 29-42%% of gap fills for ~0 precision")
    ap.add_argument("--no-release-light", action="store_true",
                    help="disable #3: keep the target slots held by semantically-light source lexemes "
                         "reserved, instead of releasing them to real content gaps — ablation switch")
    ap.add_argument("--no-morph", action="store_true",
                    help="disable the number/gender morphology-agreement tie-break — ablation switch")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    publish_iso = args.publish_iso or args.iso

    books = (OT_BOOKS + NT_BOOKS if args.all else OT_BOOKS if args.ot else NT_BOOKS if args.nt
             else [b.upper() for b in (args.book or ["RUT"])])
    methods = tuple(m.strip() for m in args.methods.split(","))
    heb = HebrewSource()
    recs = build_corpus(books, args.usj_dir, heb, remap=remapper(args.iso, str(args.usj_dir)))  # auto-detected scheme, match eflomal/gloss numbering
    stopwords = StopwordFilter(publish_iso, str(args.usj_dir))   # #3: target function-word gate (cached)
    lex_pos, lex_translit = load_priors(args.prior_pack)
    covered_h, taken_t, anchors, strong_surf, target_pos = load_covered(
        args.iso, args.out, methods, args.min_score, lex_pos,
        release_light=not args.no_release_light,
        reserve_function_slots=args.reserve_function_slots)
    # `.is_file()`, not `.exists()`: the documented "'' to disable" arrives as Path('') == Path('.'),
    # which EXISTS as a directory, so the old check tried to read_text() the cwd and raised
    # IsADirectoryError instead of disabling the prior.
    cross_lang = (json.loads(args.cross_lang.read_text(encoding="utf-8"))
                 if args.cross_lang and args.cross_lang.is_file() else {})
    cross_edition_vocab = {}
    if not args.no_cross_edition:
        try:
            cross_edition_vocab = load_lexeme_vocab(args.cross_edition_iso or publish_iso, hi_conf_only=True)
        except SystemExit as e:
            print(f"[gapfill] #4 cross-edition vocab unavailable ({e}) — skipping that prior", file=sys.stderr)
    filler = GapFiller()
    # Mechanism C — learn this language's construct-order from the taken pool: for every phrase where
    # BOTH the head (rela=NA) and a construct-governed dependent (rela=rec) are already aligned, does
    # the dependent's target come after the head's? Hebrew is head-first; most targets preserve that
    # ("fils d'Israël"), some don't — one number per language, learned from data we already have,
    # steering which side of the mate's span phrase-anchored placement prefers. None below 50
    # observations (sparse → order-preserving default inside align_gap).
    rec_after = rec_total = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch:
            continue
        by_phrase: dict = {}
        for t in r.heb:
            if getattr(t, "phrase_id", None) and t.strong and t.is_content:
                by_phrase.setdefault(t.phrase_id, []).append(t)
        for toks_ in by_phrase.values():
            heads = [t for t in toks_ if t.rela == "NA" and t.idx in anch]
            deps = [t for t in toks_ if t.rela == "rec" and t.idx in anch]
            for hd in heads:
                for dp in deps:
                    rec_total += 1
                    rec_after += anch[dp.idx] > anch[hd.idx]
    rec_after_rate = (rec_after / rec_total) if rec_total >= 50 else None
    # Confidence gate (gold-validated 2026-07-25, see internal-docs/): phrase-tier gold precision falls
    # off a CLIFF, not a slope — languages within ~0.01 of a coin-flip rate (swe_fol 0.489/swk 0.488)
    # scored 0.8-1.2%; every language at >=0.20 deviation scored double-digit (hin 0.203->17.1%, the
    # WEAKEST confirmed positive). Nothing observed in between. Anchored to that weakest confirmed
    # positive rather than a midpoint — deliberately conservative: a language with a real but smaller
    # deviation gets disabled by default until more gold data confirms it, rather than risk trusting
    # noise (magnitude ALSO doesn't predict precision cleanly above the cliff — spa 0.337->7.6% underperformed
    # hin's 0.203->17.1% — so this is an on/off gate, not a dial). Disables the WHOLE last-resort phrase
    # mechanism below threshold (not just the direction default), per the swe/swk finding.
    _PHRASE_CONFIDENCE_MARGIN = 0.20
    phrase_confident = rec_after_rate is not None and abs(rec_after_rate - 0.5) >= _PHRASE_CONFIDENCE_MARGIN
    phrase_enabled = phrase_confident and not args.no_phrase

    # Step 2/Track A — order-aware phrase placement: generalize past within-phrase construct chains
    # (mechanism C above) to CROSS-phrase BHSA function order (Subj/Pred/Objc/...), for gap tokens whose
    # own phrase has no aligned member at all (previously a dead end — see phrase_coherence.py's
    # "no-mate" bucket). Same pair-order math as constituent_order.py's pair_order_kept, computed inline
    # here (not imported — that module's profile() re-derives anchors/recs from scratch, wasteful when
    # gapfill already has them). Same conservative confidence gate as rec_after_rate (deviation from 0.5
    # >=0.20, gold-anchored to hin's weakest-confirmed 0.203) — PLUS a higher per-pair sample floor
    # (n>=100, not 50) since there are dozens of function pairs, not one global number, so more room for
    # a lucky/unlucky small-n pair to clear the deviation bar by chance.
    func_pair_counts: dict = collections.defaultdict(lambda: [0, 0])   # (fa,fb) -> [kept, total]
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch:
            continue
        by_phrase_fn: dict = {}
        for t in r.heb:
            if t.phrase_id and t.function and t.strong and t.is_content and t.idx in anch:
                fn, src, tgts = by_phrase_fn.get(t.phrase_id, (t.function, t.idx, []))
                tgts.append(anch[t.idx])
                by_phrase_fn[t.phrase_id] = (fn, min(src, t.idx), tgts)
        phrases = sorted((src, fn, sum(tgts) / len(tgts)) for fn, src, tgts in by_phrase_fn.values())
        for (src_a, fa, ta), (src_b, fb, tb) in zip(phrases, phrases[1:]):
            cell = func_pair_counts[(fa, fb)]
            cell[1] += 1
            cell[0] += ta < tb
    func_order = ({pair: k / n for pair, (k, n) in func_pair_counts.items()
                  if n >= 100 and abs(k / n - 0.5) >= _PHRASE_CONFIDENCE_MARGIN}
                 if phrase_confident and not args.no_func_order else {})

    # Step 4/Track A — morphology agreement prior: the SAME Strong's can render as different target
    # surfaces depending on number/gender (a plural vs singular occurrence of one root within a verse) —
    # strong_surfaces' top-k back-off has no way to prefer the occurrence-specific surface. Derived
    # directly from recs+anchors (not the jsonl pairs — no schema change/re-alignment needed), same
    # pattern as rec_after_rate/func_order above. Per-key floor (n>=3) is much lower than the language-
    # level confidence gates above: this is per-(strong,feature) evidence, not one global rate, and a
    # wrong tie-break only matters when it's ALSO the top vocabulary candidate (see gapfill_align.py).
    morph_surf: dict = collections.defaultdict(collections.Counter)
    if not args.no_morph:
        for r in recs:
            ref = encode(r.book, r.ch, r.v)
            anch = anchors.get(ref)
            if not anch or not r.toks:
                continue
            for h in r.heb:
                if not (h.strong and h.idx in anch and (h.number or h.gender)):
                    continue
                j = anch[h.idx]
                if j >= len(r.toks):
                    continue
                word = r.toks[j].lower()
                if h.number:
                    morph_surf[(h.strong, "number", h.number)][word] += 1
                if h.gender:
                    morph_surf[(h.strong, "gender", h.gender)][word] += 1
    morph_surf_top = {k: {w for w, _ in c.most_common(3)} for k, c in morph_surf.items()
                      if sum(c.values()) >= 3}

    print(f"[gapfill] {args.iso}: {len(recs)} verses · covered-by {methods}\n"
          f"  priors: {len(strong_surf)} strong-surfaces · {len(target_pos)} bootstrapped target-POS · "
          f"{len(lex_pos)} lexeme-POS · {len(lex_translit)} translit · positional · "
          f"{len(stopwords.words)} target function-words (#3, gated out) · "
          f"{len(cross_lang)} cross-lingual span profiles (#1, floor={args.multiword_floor}) · "
          f"{len(cross_edition_vocab)} cross-edition lexeme-vocab entries (#4, hi_conf-only, "
          f"from iso={args.cross_edition_iso or publish_iso}) · "
          f"construct-order: {rec_after}/{rec_total} dep-after-head "
          f"(rate={'%.2f' % rec_after_rate if rec_after_rate is not None else 'sparse, default'}) · "
          f"phrase prior: {'enabled' if phrase_enabled else 'DISABLED (below confidence gate)' if not phrase_confident and not args.no_phrase else 'disabled (--no-phrase)'} · "
          f"cross-phrase func-order: {len(func_order)} confident pair(s) · "
          f"morph-agreement: {len(morph_surf_top)} (strong,feature,value) cell(s)",
          file=sys.stderr)

    # Self-training round 3/Track A: when the caller passes `--methods eflomal,gloss,gapfill`, this run's
    # "covered" pool (anchors/strong_surf/construct-order/func_order — everything computed above from
    # load_covered) already includes the PRIOR gapfill pass's own fills, so a residual gap gets a richer
    # taken pool to reason from (more anchors to interpolate between, an updated construct/func-order rate
    # now measured over more of the verse). This does NOT re-decide anything the prior pass already filled
    # — gap_idx below is still "whatever no method (incl. gapfill) has touched yet" — it only extends
    # coverage into what's STILL a gap. The prior pass's own pairs must be preserved, not discarded: seed
    # `by_ref` from the existing gapfill files before they're deleted, and MERGE new fills into the same
    # per-verse record instead of overwriting it.
    self_train = "gapfill" in methods
    by_ref: dict[str, dict] = {}
    if self_train:
        for fp in _tag_files(args.out, "gapfill", args.iso):
            for line in fp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    by_ref[rec["ref"]] = rec

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
                                   max_extend=args.max_extend,
                                   extend_over_stopwords=args.extend_over_stopwords,
                                   cross_edition_vocab=cross_edition_vocab,
                                   rec_after_rate=rec_after_rate,
                                   phrase_enabled=phrase_enabled,
                                   func_order=func_order,
                                   morph_surf=morph_surf_top)
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
            if self_train and ref in by_ref:
                by_ref[ref]["pairs"].extend(pairs)
            else:
                by_ref[ref] = {"ref": ref, "book": r.book, "chapter": r.ch, "verse": r.v, "pairs": pairs}

    for rec in by_ref.values():
        by_book[rec["book"]].append(rec)
    for book, out_recs in by_book.items():
        out_recs.sort(key=lambda x: (x["chapter"], x["verse"]))
        with (args.out / f"align_gapfill_{args.iso}_{book}.jsonl").open("w", encoding="utf-8") as fh:
            for x in out_recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    n_prior_fills = sum(len(r["pairs"]) for r in by_ref.values()) - n_filled if self_train else 0
    print(f"[gapfill] {n_gap} gap tokens · filled {n_filled} ({100*n_filled/max(1,n_gap):.1f}%) by prior "
          f"{dict(prior_counts)}" +
          (f" · +{n_prior_fills} carried over from prior pass" if self_train else "") +
          f" → align_gapfill_{args.iso}_*.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
