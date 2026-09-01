"""Gap-fill — fill the content tokens eflomal + gloss both missed, MODEL-FREE.

Supersedes the earlier neural (LaBSE/bge-m3 cosine) gap-fill: measured (see internal-docs/
gap-fill-scaling-strategy.md) at only a ~7.5% target-selection tie-break contribution on its BEST case
(French), and zero on any language without encoder coverage — i.e. most of the actual target languages.
Retired on mission grounds: this repo's whole point is running on languages with NO LLM/encoder at all, so
a signal that structurally can't exist for the tail has no place in the default pipeline. Every candidate
here is re-ranked by priors extracted algorithmically from data already established by eflomal+gloss (the
"taken pool") — no target-language model, no download, works on any language with a Bible:

  • strong-rollup back-off (`strong_surfaces`) — an untaken target matching a known surface of the gap's
    Strong's, from the taken pool: near-decisive.
  • cross-edition back-off (`cross_edition_vocab`) — an untaken target matching a known surface of the
    gap's LEXEME, from a DIFFERENT source than the taken pool: the published lexeme-alignments/iso=<iso>
    pooled vocabulary, which unions every method AND every pooled edition of the language (not just this
    one translation's own eflomal+gloss run). Live-verified (reverse_align_check.py, 2026-07): ~23% of
    what a single edition's own eflomal+gloss leaves as gaps has a real, present-in-the-verse word already
    attested by another pooled edition for the exact same lexeme — a signal strong-rollup can't see
    because it only knows THIS translation's own choices. Weighted slightly below strong-rollup (a
    same-translation Strong's match is still the more direct signal when both fire).
  • name transliteration (`lex_translit` + `lex_pos`, prior-pack) — for pos=name gaps, an untaken target
    whose surface ≈ the romanized source (edit-distance).
  • grammatical (`target_pos` bootstrapped from taken pool × `lex_pos`) — soft boost when the untaken
    target's inferred POS matches the gap's source POS (tie-break only; never a standalone qualifier).
  • positional/diagonal (`anchors`) — penalise distance from the interpolated expected position.
  • #3 `stopwords` (target_stopwords.StopwordFilter) — TARGET function-word tokens are dropped from the
    candidate pool before scoring: eflomal/gloss already consumed the real content-word rendering, so
    what's left untaken for a content-word gap is often stopword scraps; without this gate a content
    lexeme lands on one anyway (wrong fill). Target-side mirror of the source-side `is_content` filter.
  • #1 `cross_lang` (cross_lang_prior profile) — a lexeme that renders as a fixed multi-word phrase in
    most of the OTHER languages we've aligned (compound place names, compound numbers) almost certainly
    renders as a phrase here too. Post-hoc, additive-only span extension.

  • phrase-adjacency (`phrase_id`/`rela`, BHSA spine syntax, OT-only) — LAST-RESORT tier: a gap token
    whose construct-chain mate (usually the head) is already aligned may claim an untaken slot adjacent
    to the mate's target, direction steered by the language's learned construct order (`rec_after_rate`,
    from the taken pool — eng 0.77 dep-after-head, arb 0.85). Fires ONLY for source tokens and target
    slots no vocabulary prior wanted (measured 2026-07-25: fired greedily it stole slots from
    higher-precision cross_edition fills), and its fills are scored 0.75 — BELOW export_lex's
    `score >= 0.9` hi_conf bar — because measured precision is 12% (eng) / 21% (arb): genuinely
    additive coverage on zero-signal tokens (+33/+89 net gold-correct fills), but embedding-tier
    precision, so flagged and excluded from the hi-conf tier rather than dropped. NOTE: the
    phrase-anchored POSITION penalty for vocab-fired fills measured as a no-op (a vocab match is
    usually unique in its verse — nothing to disambiguate); the tier's value is the last-resort fills.

Only strong/name/cross_edition/phrase priors can ever fire a fill (no embedding tier — there is no
embedding). strong/name/cross_edition fills are hi-conf (score 0.9, matching export_lex's uniform
`score >= 0.9` criterion); phrase fills are 0.75 (see above).
"""
from __future__ import annotations

from lexeme_aligner.gloss_align import Match, _name_score


class GapFiller:
    """Fills gap tokens (eflomal+gloss missed) onto untaken targets, ranked by the priors above."""

    def __init__(self, pos_weight: float = 0.2, strong_boost: float = 0.6,
                name_boost: float = 0.6, pos_boost: float = 0.15, cross_edition_boost: float = 0.5,
                phrase_boost: float = 0.4, phrase_pos_weight: float = 0.6,
                phrase_fill_score: float = 0.75, morph_boost: float = 0.1):
        self.pos_weight, self.strong_boost, self.name_boost, self.pos_boost, self.cross_edition_boost = (
            pos_weight, strong_boost, name_boost, pos_boost, cross_edition_boost)
        # Morphology agreement (number/gender) — TIE-BREAK ONLY, same weight class as pos_boost: prefers
        # the strong-rollup candidate whose surface is independently attested for THIS occurrence's own
        # number/gender over another occurrence's, when a Strong's has more than one known surface.
        # MEASURED NO-OP (2026-07-25, eng/arb gold, controlled A/B --no-morph): byte-identical fills and
        # gold precision with the tie-break on vs off, despite 13k+ learned (strong,feature,value) cells.
        # Same root cause as mechanism A's phrase-position tie-break (also a no-op, see phrase_expected's
        # docstring): by gap-fill time the untaken candidate pool for a given strong-rollup match is
        # almost always already down to ONE option (eflomal+gloss consumed the others), so there's
        # nothing left to disambiguate between. Kept (harmless, gated, off by default impact) rather than
        # ripped out — same "measure honestly, don't force it" treatment as mechanism A.
        self.morph_boost = morph_boost
        # BHSA phrase-syntax prior (OT-only; see docstring): a gap token whose PHRASE-MATE (construct-
        # chain head etc.) is already aligned expects its own target ADJACENT to the mate's — a much
        # sharper position than the diagonal. phrase_pos_weight is deliberately 3x pos_weight (the
        # expectation is confident, so distance from it should cost more); phrase_boost sits BELOW
        # cross_edition_boost (adjacency alone is weaker evidence than a vocabulary match); and a fill
        # fired by adjacency ALONE gets phrase_fill_score < 0.9 so it does NOT enter the hi_conf tier
        # (export_lex hi_conf = score>=0.9) until score_gapfill validates the prior's precision —
        # same skepticism the embedding prior failed and was dropped under.
        self.phrase_boost, self.phrase_pos_weight = phrase_boost, phrase_pos_weight
        self.phrase_fill_score = phrase_fill_score

    def align_gap(self, heb, tokens: list[str], gap_idx: set, taken: set,
                  strong_surfaces: dict | None = None, anchors: dict | None = None,
                  lex_pos: dict | None = None, lex_translit: dict | None = None,
                  target_pos: dict | None = None, stopwords=None,
                  cross_lang: dict | None = None, multiword_floor: float = 0.6,
                  max_extend: int = 1, extend_over_stopwords: bool = False,
                  cross_edition_vocab: dict | None = None,
                  rec_after_rate: float | None = None,
                  phrase_enabled: bool = True,
                  func_order: dict | None = None,
                  morph_surf: dict | None = None) -> list[tuple]:
        """Align ONLY the gap source tokens (`gap_idx`) onto the UNTAKEN targets. Returns (Match, prior)
        pairs — prior is 'strong', 'name', 'cross_edition', 'phrase', or 'phrase_xorder' (the only tiers
        that can ever fire, model-free). `rec_after_rate`: learned P(construct-DEPENDENT's target comes
        after its HEAD's target) for this language (from the taken pool); None → assume source order
        preserved. `func_order`: gated {(function_a, function_b): rate} — P(target keeps a-then-b source
        order) for ADJACENT BHSA phrase-function pairs (constituent_order.py), pre-filtered by the caller
        to confidently one-sided pairs only (see gapfill.py) — Step 2/Track A: generalizes the phrase
        mechanism past within-phrase construct chains to gap tokens whose OWN phrase has no aligned
        member at all (previously a dead end, the "no-mate" bucket in phrase_coherence.py)."""
        content = [h for h in heb if h.strong and h.idx in gap_idx]
        avail = [j for j in range(len(tokens)) if j not in taken
                and not (stopwords and stopwords.is_function(tokens[j]))]
        if not content or not avail:
            return []
        tnorm = [t.lower() for t in tokens]
        n_trg, n_src = len(tokens), max(len(heb), 1)
        order = {h.idx: k for k, h in enumerate(heb)}                # source token → ordinal position

        # BHSA phrase index (OT-only; phrase_id is None throughout the NT → all of this no-ops there)
        by_phrase: dict = {}
        covered_phrases: list = []          # sorted [(min_idx, function, mean_target_pos), ...]
        if phrase_enabled:
            for h in heb:
                if getattr(h, "phrase_id", None) and h.strong and h.is_content:
                    by_phrase.setdefault(h.phrase_id, []).append(h)
            if func_order and anchors:
                cp: dict = {}
                for h in heb:
                    if h.phrase_id and h.function and h.strong and h.is_content and h.idx in anchors:
                        fn, src, tgts = cp.get(h.phrase_id, (h.function, h.idx, []))
                        tgts.append(anchors[h.idx])
                        cp[h.phrase_id] = (fn, min(src, h.idx), tgts)
                covered_phrases = sorted((src, fn, sum(tgts) / len(tgts)) for fn, src, tgts in cp.values())

        def cross_phrase_expected(h) -> tuple[float, str] | None:
            """Fallback when h's OWN phrase has no aligned member at all: anchor to the nearest covered
            ADJACENT phrase and use its function-pair's gated order-preservation rate to predict which
            side of that phrase's target span h's own target should land on."""
            if not covered_phrases or not h.function:
                return None
            other = min(covered_phrases, key=lambda cp: abs(cp[0] - h.idx))
            src_o, fn_o, pos_o = other
            if src_o == h.idx:                                  # h itself is (part of) this phrase — skip
                return None
            if h.idx < src_o:
                rate = func_order.get((h.function, fn_o))
                if rate is None:
                    return None
                kept = rate >= 0.5                                # h (a) expected before other (b)
            else:
                rate = func_order.get((fn_o, h.function))
                if rate is None:
                    return None
                kept = rate >= 0.5                                # other (a) expected before h (b)
            if h.idx < src_o:
                return (pos_o - 1 if kept else pos_o + 1), "phrase_xorder"
            return (pos_o + 1 if kept else pos_o - 1), "phrase_xorder"

        def phrase_expected(h) -> tuple[float, str] | None:
            """Target position expected from an already-aligned phrase-mate (None if no covered mate).
            A BHSA phrase can hold MORE than a clean (head, dependent) pair — e.g. a coordinated name
            sitting alongside an unrelated construct chain in the same phrase (live case: "Baal-Hanan,
            Akbor's SON" — "son"(head)+"Akbor"(its rec-dependent) share a phrase with the unrelated
            preceding name "Baal-Hanan"). So mate selection must find the mate that forms H's OWN
            (head,dependent) relation — not just the nearest same-phrase token — or it can anchor to a
            same-phrase token that has nothing to do with h at all. If h IS the dependent (rela=rec) its
            relevant mate is the head (rela=NA); if h IS the head, its relevant mate is ITS dependent
            (rela=rec) — symmetric, previously only the first direction was searched. Only fall back to
            plain nearest-by-distance when no structurally-matched mate exists (coordination members,
            etc — a real but weaker signal than a matched pair)."""
            if not anchors or not getattr(h, "phrase_id", None):
                return cross_phrase_expected(h)
            mates = [m for m in by_phrase.get(h.phrase_id, []) if m.idx != h.idx and m.idx in anchors]
            if not mates:
                return cross_phrase_expected(h)
            if h.rela == "rec":
                structural = [m for m in mates if m.rela == "NA"]
            elif h.rela == "NA":
                structural = [m for m in mates if m.rela == "rec"]
            else:
                structural = []
            pool = structural or mates
            mate = min(pool, key=lambda m: abs(m.idx - h.idx))
            mate_pos = anchors[mate.idx]
            dep_after = rec_after_rate is None or rec_after_rate >= 0.5
            if h.rela == "rec" and mate.rela == "NA":       # h is the construct dependent, mate the head
                return mate_pos + (1 if dep_after else -1), "phrase"
            if h.rela == "NA" and mate.rela == "rec":       # h is the head, mate the dependent
                return mate_pos + (-1 if dep_after else 1), "phrase"
            return mate_pos + (1 if h.idx > mate.idx else -1), "phrase"   # order-preserving default

        def expected(hidx: int) -> float:
            p = order.get(hidx, 0)
            if anchors:                                             # interpolate between nearest anchors
                below = [(order.get(a, 0), tp) for a, tp in anchors.items() if order.get(a, 0) <= p]
                above = [(order.get(a, 0), tp) for a, tp in anchors.items() if order.get(a, 0) >= p]
                b = max(below, default=None)
                a = min(above, default=None)
                if b and a and a[0] != b[0]:
                    return b[1] + (p - b[0]) / (a[0] - b[0]) * (a[1] - b[1])
                if b:
                    return b[1]
                if a:
                    return a[1]
            return p / n_src * n_trg                                # diagonal fallback

        scored = []
        phrase_only: list[tuple] = []                    # (score, i, j) — last-resort pass, see below
        for i, h in enumerate(content):
            known = strong_surfaces.get(h.strong) if strong_surfaces else None
            known_cross = cross_edition_vocab.get(h.lexeme) if cross_edition_vocab and h.lexeme else None
            spos = lex_pos.get(h.lexeme) if lex_pos else None
            translit = ((lex_translit.get(h.lexeme) or "").replace(".", "").replace("·", "")
                        if lex_translit else "")
            exp = None
            p_result = phrase_expected(h)                            # (pos, prior) or None
            p_exp, p_prior = p_result if p_result else (None, None)
            for j in avail:
                is_strong = bool(known and tnorm[j] in known)
                is_name = bool(spos == "name" and translit and _name_score(translit, tokens[j]) >= 0.8)
                is_cross = bool(not is_strong and known_cross and tnorm[j] in known_cross)
                # phrase-adjacency: within 1 of where the aligned phrase-mate predicts this token —
                # can FIRE a fill alone (rare construct dependents with no vocabulary anywhere), and
                # boosts the ranking of vocab-fired candidates sitting in the syntactically right spot.
                is_phrase = bool(p_exp is not None and abs(j - p_exp) <= 1)
                if not (is_strong or is_name or is_cross or is_phrase):   # model-free: only these fire
                    continue
                pos_ok = bool(spos and target_pos and target_pos.get(tnorm[j]) == spos)
                morph_ok = bool(is_strong and morph_surf and (
                    (h.number and tnorm[j] in morph_surf.get((h.strong, "number", h.number), ()))
                    or (h.gender and tnorm[j] in morph_surf.get((h.strong, "gender", h.gender), ()))))
                if p_exp is not None:                               # mechanism A: sharper, tighter penalty
                    pos_pen = self.phrase_pos_weight * abs(j - p_exp) / n_trg
                else:
                    if exp is None:
                        exp = expected(h.idx)
                    pos_pen = self.pos_weight * abs(j - exp) / n_trg
                s = ((self.strong_boost if is_strong else 0.0) + (self.name_boost if is_name else 0.0)
                     + (self.cross_edition_boost if is_cross else 0.0)
                     + (self.phrase_boost if is_phrase else 0.0)
                     + (self.pos_boost if pos_ok else 0.0) + (self.morph_boost if morph_ok else 0.0) - pos_pen)
                if is_strong or is_name or is_cross:
                    scored.append((s, i, j, is_strong, is_name, is_cross))
                else:
                    phrase_only.append((s, i, j, p_prior))
        scored.sort(key=lambda x: -x[0])
        out: list[tuple] = []                                       # (Match, prior) — prior tags the scorer
        done_src: set[int] = set()
        used: set[int] = set()
        for s, i, j, is_strong, is_name, is_cross in scored:
            if i in done_src or j in used:
                continue
            prior = "strong" if is_strong else "name" if is_name else "cross_edition"
            out.append((Match(content[i].idx, [j], 0.9, "gapfill"), prior))
            done_src.add(i)
            used.add(j)
        # LAST-RESORT pass: phrase-adjacency alone may only claim source tokens and target slots that
        # NO vocabulary prior wanted — purely additive coverage on otherwise-zero tokens. Measured
        # (2026-07-25, eng/arb gold): fired greedily alongside the vocab priors it STOLE target slots
        # from higher-precision cross_edition fills (eng net correct fills fell 404→327); as a second
        # pass it can only add. Its fills stay sub-hi_conf (phrase_fill_score) — see __init__.
        phrase_only.sort(key=lambda x: -x[0])
        for s, i, j, prior in phrase_only:
            if i in done_src or j in used:
                continue
            out.append((Match(content[i].idx, [j], self.phrase_fill_score, "gapfill"), prior))
            done_src.add(i)
            used.add(j)

        # Span extension (additive, post-hoc). Gold says a source content token takes 2.18 target words
        # on average and is multi-word 76.6% of the time, while everything above emits ONE target — so
        # this is where the under-filling is corrected.
        #
        # `cross_lang` is OPTIONAL and gates WHICH lexemes extend. Controls (2026-08-31, 9 gold
        # languages) showed a profile's per-lexeme rates are worth -3 correct fills and its coverage a
        # further -5, i.e. nothing: extending UNCONDITIONALLY scored best. So with no profile passed,
        # every fill is eligible — see internal-docs/subject-fusion-span-prior.md §0d.
        if max_extend > 0:
            by_idx = {h.idx: h for h in content}
            for m, prior in out:
                if cross_lang:                           # a NON-EMPTY profile gates; {} means no gate
                    # (gapfill.py passes {} when --cross-lang is absent, so `is not None` would wrongly
                    # take the gate path with an empty profile and silently skip every extension)
                    h = by_idx.get(m.h_idx)
                    stats = cross_lang.get(h.lexeme) if h and h.lexeme else None
                    if not stats or stats.get("multiword_rate", 0) < multiword_floor:
                        continue
                for _ in range(max_extend):
                    # Try RIGHT then LEFT. Leftward matters because the words a source token pulls in
                    # often PRECEDE it — an article ("the sons"), a preposition ("of Japheth"), or the
                    # free subject pronoun a fused finite verb needs ("he said"); the pre-2026-08-31
                    # code could only ever go right, so those were unreachable.
                    for cand in (m.t_idx[-1] + 1, m.t_idx[0] - 1):
                        if not (0 <= cand < len(tokens)) or cand in used or cand in taken:
                            continue
                        if stopwords and stopwords.is_function(tokens[cand]) and not extend_over_stopwords:
                            continue
                        m.t_idx.append(cand) if cand > m.t_idx[-1] else m.t_idx.insert(0, cand)
                        used.add(cand)
                        break
                    else:
                        break                            # neither side available — stop extending
        return sorted(out, key=lambda mp: mp[0].h_idx)
