"""Stage (a): the $0 deterministic gloss-anchored alignment strategy.

Per verse: score every (Hebrew token × target-token position) pair from the gloss priors
+ proper-noun transliteration fuzz, then assign greedily (best score first, each side
used once, positional proximity as tie-break). No models, no network — mirrors the
English prototype's strategy chain (advanced-docs/aligner-plan.md §English prototype).

Language plug-point: `Normalizer` — per-language token normalization (the Indonesian one
strips clitics/affixes: -lah/-nya/…, me-/di-/ber-/ter-/…). Everything else is generic.
"""
from __future__ import annotations

from dataclasses import dataclass

from lexeme_aligner.hebrew_source import HebToken

# ── language-specific normalization (plug-point) ───────────────────────────────────────

_IND_SUFFIXES = ("lah", "kah", "nya", "pun", "ku", "mu", "an", "i")
_IND_PREFIXES = ("meng", "meny", "mem", "men", "me", "di", "ber", "ter", "per", "pe",
                 "se", "ke")


class Normalizer:
    """Default: lowercase only. Subclass per language."""
    def forms(self, token: str) -> list[str]:
        return [token.lower()]

    def stem(self, token: str) -> str:
        """Single canonical stem (the most-reduced form) — for eflomal's stemmed input. Default = the
        lowercased token (no-op); LearnedNormalizer returns the affix-stripped stem so inflected variants
        collapse to one type."""
        return self.forms(token)[-1]


class IndonesianNormalizer(Normalizer):
    def forms(self, token: str) -> list[str]:
        t = token.lower()
        out = [t]
        stems = {t}
        for suf in _IND_SUFFIXES:                        # strip one clitic/suffix
            if t.endswith(suf) and len(t) - len(suf) >= 3:
                stems.add(t[: -len(suf)])
        for base in list(stems):                          # then optionally one prefix
            for pre in _IND_PREFIXES:
                if base.startswith(pre) and len(base) - len(pre) >= 3:
                    stems.add(base[len(pre):])
        out += [s for s in sorted(stems, key=len, reverse=True) if s != t]
        return out


NORMALIZERS: dict[str, Normalizer] = {"ind": IndonesianNormalizer()}


# ── scoring ────────────────────────────────────────────────────────────────────────────

def _lev(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _word_score(gloss_forms: list[str], tok_forms: list[str]) -> float:
    """One gloss word vs one target token — BOTH sides normalized (a prior like 'berkata'
    must match text 'kata' and vice versa), so affix-stripping applies to both."""
    if gloss_forms[0] == tok_forms[0]:
        return 1.0
    gset = set(gloss_forms)
    if gset.intersection(tok_forms):
        return 0.9                                       # match via an affix-stripped form
    g, t = gloss_forms[0], tok_forms[0]
    if len(g) >= 4 and len(t) >= 4:
        n = min(len(g), len(t))
        if g[:n] == t[:n] or (g[:4] == t[:4] and abs(len(g) - len(t)) <= 3):
            return 0.7
    if len(g) >= 5 and len(t) >= 5 and _lev(g, t) <= 1:
        return 0.6                                       # orthographic variant (isteri/istri)
    return 0.0


_UROMAN = None
_ROMAN_CACHE: dict[str, str] = {}


def romanize(word: str) -> str:
    """M4 (2026-09-27): universal romanization for the name-transliteration prior's TARGET side, via
    `uroman` (MIT, Hermjakob/USC-ISI) -- `pip install uroman` under the `translit` extra. Lazy-imported
    and cached per process (loading its rule tables has real cost, and the same target token recurs
    across a whole corpus); falls back to the word UNCHANGED when `uroman` isn't installed, so this
    stays additive/opt-in at the dependency level, never a hard requirement of the base chain.

    WHY THIS EXISTS: this module's own `is_name` path below compares an ENGLISH gloss ("Ruth") against
    the raw TARGET token via `_name_score`'s plain Levenshtein/prefix check -- for any non-Latin-script
    target (Devanagari, Arabic, Bengali, Cyrillic, Han, ...) that comparison is structurally impossible
    (disjoint character sets can never clear `_name_score`'s distance/prefix thresholds), so the name
    prior was silently INERT for every such language, in BOTH this module's own gloss pass and
    `gapfill_align.py`'s `lex_translit`-based prior -- confirmed by inspection of `_name_score`'s own
    thresholds, not measured as "low precision": it could not fire AT ALL. Romanizing the target token
    before comparison makes the same mechanism reachable for scripts it never worked on before. Verified
    on real Ruth-book names via `uroman.Uroman().romanize_string`: hin "रूत"->"ruut", ben "রূৎ"->"ruuta",
    arb "بوعز"->"bw'z" (all recognizably close to their English gloss). Also lightly normalizes
    already-Latin text (diacritic-stripping: "Élimélek"->"Elimelek"), which `_name_score`'s own
    lowering doesn't do -- a mild accent-insensitivity gain for the already-working Latin-script
    languages, not just a new capability for non-Latin ones."""
    global _UROMAN
    if word in _ROMAN_CACHE:
        return _ROMAN_CACHE[word]
    if _UROMAN is None:
        try:
            import uroman
            _UROMAN = uroman.Uroman()
        except ImportError:
            _UROMAN = False
    out = _UROMAN.romanize_string(word) if _UROMAN else word
    _ROMAN_CACHE[word] = out
    return out


def _name_score(gloss_en: str, token: str) -> float:
    """Proper noun: English per-occurrence gloss vs target surface (Ruth→Rut, Boaz→Boas)."""
    g, t = gloss_en.lower().strip(".,"), token.lower()
    if not g or not t:
        return 0.0
    if g == t:
        return 0.98
    d = _lev(g, t)
    if d <= 1:
        return 0.92
    if d <= 2 and min(len(g), len(t)) >= 4:
        return 0.82
    if len(g) >= 4 and len(t) >= 4 and g[:4] == t[:4]:
        return 0.75
    return 0.0


@dataclass
class Match:
    h_idx: int
    t_idx: list[int]            # target token index(es) — multiword glosses span several
    score: float
    method: str                 # exact | stem | prefix | name | multi
    light: bool = False          # source lexeme is semantically light (low cross-lingual target dominance)


_GLOSS_FORM_CACHE: dict[tuple[str, str], list[str]] = {}


def _gforms(word: str, norm: Normalizer, iso: str) -> list[str]:
    key = (iso, word)
    if key not in _GLOSS_FORM_CACHE:
        _GLOSS_FORM_CACHE[key] = norm.forms(word)
    return _GLOSS_FORM_CACHE[key]


def align_verse(heb: list[HebToken], tokens: list[str], priors, iso: str,
                stopwords=None, cross_lang: dict | None = None, multiword_floor: float = 0.6,
                skip_lexemes: set | None = None, light_last: bool = False,
                source_gated_stopwords: bool = False) -> list[Match]:
    """Deterministic gloss alignment, enhanced with the three language-independent signals:
      • #2 morphology — via `norm` (LearnedNormalizer for langs without a hand-coded one): both the prior
        rendering and the verse token are affix-stripped, so an inflected form matches its dictionary stem.
      • #3 `stopwords` (target_stopwords.StopwordFilter) — TARGET function-word positions can't be the sole
        match for a content lexeme (a fuzzy tier would otherwise land a content rendering on 'de'/'le'); the
        source-side is already keyness-filtered in the priors, this is the target-side mirror. Multiword
        spans are exempt (already tightly constrained to all-words-match-prior).
      • #1 `cross_lang` (cross_lang_prior profile) — a single-token match whose lexeme renders as a phrase in
        most OTHER aligned languages (compound place names, numbers) is extended to its untaken neighbor.
        Additive: never steals a token already matched, so it only lifts recall on compound lexemes.

    Two protections against a semantically LIGHT source lexeme taking a slot a real match needed
    (protection #4, advanced-docs/pipeline-overview.md) — both default OFF pending measurement:
      • `light_last` — light lexemes (config/light_lexemes.json: copulas, `all`, `have`, `one`) are
        sorted BEHIND every non-light candidate, so they can only claim positions no real content
        lexeme wanted. Deliberately NOT a ban: εἰμί→'est' is a correct alignment (fra 'est' genuinely
        renders grc:1510 at 77% share) and is kept whenever nothing contests the slot. Same
        last-resort shape that fixed gap-fill's phrase tier, where firing greedily alongside the
        vocabulary priors cost eng 404→327 correct fills.
      • `source_gated_stopwords` — the target-side stopword gate, but applied ONLY when the SOURCE is
        a content, non-light lexeme. The unconditional version craters gloss because it also blocks
        legitimate function↔function alignments (ὁ→'le'); conditioning on the source removes exactly
        that failure mode while keeping the case it was built for."""
    norm = NORMALIZERS.get(iso, Normalizer())
    tok_forms = [norm.forms(t) for t in tokens]
    blocked = {j for j in range(len(tokens)) if stopwords and stopwords.is_function(tokens[j])}  # #3
    tok_roman = [romanize(t) for t in tokens]   # M4: once per verse, not per (h, j) pair

    light_h = {h.idx for h in heb if skip_lexemes and h.lexeme in skip_lexemes}
    _none: set = set()

    def blocked_for(h) -> set:
        """Which target positions this SOURCE token may not be matched onto on its own."""
        if source_gated_stopwords:
            return blocked if (h.is_content and h.idx not in light_h) else _none
        return blocked
    cands: list[Match] = []
    for h in heb:
        if not h.strong:
            continue
        h_blocked = blocked_for(h)
        is_name = (h.sp == "nmpr") or (h.morph or "").endswith("Np")
        if is_name and h.gloss_en:
            for j in range(len(tokens)):
                if j in h_blocked:
                    continue
                s = _name_score(h.gloss_en, tok_roman[j])
                if s:
                    cands.append(Match(h.idx, [j], s, "name"))
        for variant in priors.lookup(h):
            if len(variant) == 1:
                gf = _gforms(variant[0], norm, iso)
                for j in range(len(tokens)):
                    if j in h_blocked:                   # #3: don't let a content lexeme land on a stopword
                        continue
                    s = _word_score(gf, tok_forms[j])
                    if s:
                        m = ("exact" if s == 1.0 else "stem" if s == 0.9
                             else "prefix" if s == 0.7 else "fuzzy")
                        cands.append(Match(h.idx, [j], s, m))
            else:                                        # multiword: consecutive span (stopword-exempt)
                gfs = [_gforms(w, norm, iso) for w in variant]
                for j in range(len(tokens) - len(variant) + 1):
                    ws = [_word_score(gf, tok_forms[j + k]) for k, gf in enumerate(gfs)]
                    if all(s >= 0.7 for s in ws):
                        cands.append(Match(h.idx, list(range(j, j + len(variant))),
                                           0.95 * min(ws) / 0.7 if min(ws) < 1 else 0.95,
                                           "multi"))
                # head-word fallback: 'anak perempuan' matching just 'anak(ku)'
                head = gfs[0]
                for j in range(len(tokens)):
                    if j in h_blocked:
                        continue
                    s = _word_score(head, tok_forms[j])
                    if s >= 0.9:
                        cands.append(Match(h.idx, [j], 0.65, "head"))

    # greedy assignment: best score first; positional proximity breaks ties
    n_h = max((h.idx for h in heb), default=0) + 1
    def pos_penalty(m: Match) -> float:
        return abs(m.h_idx / max(1, n_h) - m.t_idx[0] / max(1, len(tokens)))
    # light_last: a light lexeme's candidates sort behind EVERY non-light candidate, at any score, so
    # they claim only what no real match wanted (see the docstring).
    cands.sort(key=lambda m: ((m.h_idx in light_h) if light_last else False, -m.score, pos_penalty(m)))
    used_h: set[int] = set()
    used_t: set[int] = set()
    out: list[Match] = []
    for m in cands:
        if m.h_idx in used_h or any(j in used_t for j in m.t_idx):
            continue
        used_h.add(m.h_idx)
        used_t.update(m.t_idx)
        out.append(m)

    if cross_lang:                                       # #1: cross-lingual span extension (additive)
        by_idx = {h.idx: h for h in heb}
        for m in out:
            if len(m.t_idx) != 1:
                continue
            h = by_idx.get(m.h_idx)
            stats = cross_lang.get(getattr(h, "lexeme", None) or "") if h else None
            if not stats or stats.get("multiword_rate", 0) < multiword_floor:
                continue
            nxt = m.t_idx[-1] + 1
            if nxt < len(tokens) and nxt not in used_t and nxt not in blocked:
                m.t_idx.append(nxt)
                used_t.add(nxt)
                m.method = "multi"
    for m in out:
        if m.h_idx in light_h:
            m.light = True
    return sorted(out, key=lambda m: m.h_idx)
