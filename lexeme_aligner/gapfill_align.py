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

Only strong/name priors can ever fire a fill (no embedding tier — there is no embedding). Every accepted
fill is hi-conf (score 0.9), matching export_lex's uniform `score >= 0.9` hi_conf criterion.
"""
from __future__ import annotations

from lexeme_aligner.gloss_align import Match, _name_score


class GapFiller:
    """Fills gap tokens (eflomal+gloss missed) onto untaken targets, ranked by the priors above."""

    def __init__(self, pos_weight: float = 0.2, strong_boost: float = 0.6,
                name_boost: float = 0.6, pos_boost: float = 0.15, cross_edition_boost: float = 0.5):
        self.pos_weight, self.strong_boost, self.name_boost, self.pos_boost, self.cross_edition_boost = (
            pos_weight, strong_boost, name_boost, pos_boost, cross_edition_boost)

    def align_gap(self, heb, tokens: list[str], gap_idx: set, taken: set,
                  strong_surfaces: dict | None = None, anchors: dict | None = None,
                  lex_pos: dict | None = None, lex_translit: dict | None = None,
                  target_pos: dict | None = None, stopwords=None,
                  cross_lang: dict | None = None, multiword_floor: float = 0.6,
                  cross_edition_vocab: dict | None = None) -> list[tuple]:
        """Align ONLY the gap source tokens (`gap_idx`) onto the UNTAKEN targets. Returns (Match, prior)
        pairs — prior is 'strong', 'name', or 'cross_edition' (the only three tiers that can ever fire,
        model-free)."""
        content = [h for h in heb if h.strong and h.idx in gap_idx]
        avail = [j for j in range(len(tokens)) if j not in taken
                and not (stopwords and stopwords.is_function(tokens[j]))]
        if not content or not avail:
            return []
        tnorm = [t.lower() for t in tokens]
        n_trg, n_src = len(tokens), max(len(heb), 1)
        order = {h.idx: k for k, h in enumerate(heb)}                # source token → ordinal position

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
        for i, h in enumerate(content):
            known = strong_surfaces.get(h.strong) if strong_surfaces else None
            known_cross = cross_edition_vocab.get(h.lexeme) if cross_edition_vocab and h.lexeme else None
            spos = lex_pos.get(h.lexeme) if lex_pos else None
            translit = ((lex_translit.get(h.lexeme) or "").replace(".", "").replace("·", "")
                        if lex_translit else "")
            exp = None
            for j in avail:
                is_strong = bool(known and tnorm[j] in known)
                is_name = bool(spos == "name" and translit and _name_score(translit, tokens[j]) >= 0.8)
                is_cross = bool(not is_strong and known_cross and tnorm[j] in known_cross)
                if not (is_strong or is_name or is_cross):          # model-free: only these can fire
                    continue
                if exp is None:
                    exp = expected(h.idx)
                pos_ok = bool(spos and target_pos and target_pos.get(tnorm[j]) == spos)
                s = ((self.strong_boost if is_strong else 0.0) + (self.name_boost if is_name else 0.0)
                     + (self.cross_edition_boost if is_cross else 0.0)
                     + (self.pos_boost if pos_ok else 0.0) - self.pos_weight * abs(j - exp) / n_trg)
                scored.append((s, i, j, is_strong, is_name, is_cross))
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

        if cross_lang:                                       # #1: cross-lingual span extension (additive)
            by_idx = {h.idx: h for h in content}
            for m, prior in out:
                h = by_idx.get(m.h_idx)
                stats = cross_lang.get(h.lexeme) if h and h.lexeme else None
                if not stats or stats.get("multiword_rate", 0) < multiword_floor:
                    continue
                nxt = m.t_idx[-1] + 1
                if (nxt < len(tokens) and nxt not in used and nxt not in taken
                        and not (stopwords and stopwords.is_function(tokens[nxt]))):
                    m.t_idx.append(nxt)
                    used.add(nxt)
        return sorted(out, key=lambda mp: mp[0].h_idx)
