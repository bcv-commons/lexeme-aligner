"""Hebrew (original) side — spine.db word tokens enriched with hbo.db per-occurrence data.

- shoresh/spine/spine.db `spine_words` is the alignment backbone: one row per UHB token,
  keyed (book, chapter, verse, idx), with surface / strong (bare int) / lemma / morph /
  is_content.
- resources/occurrences/hbo.db `occurrence` carries the per-occurrence BHSA layer:
  lex, stem (binyan), sp, English gloss, disambiguated sense + confidence.

The two tokenize differently (spine fuses prefixes: וַ⁠תֹּ֤אמֶר = conj+verb in ONE spine
token; BHSA splits them), so the join is STRONG-IN-ORDER within the verse — the nth spine
token bearing Strong's S matches the nth hbo row with Strong's S — not positional. This is
the pragmatic id-bridge from docs/aligner-plan.md §Design gotchas.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from lexeme_aligner.config import HBO_DB, SPINE_DB
from lexeme_aligner.refs import BOOK_NUMBERS, encode  # vendored — no cross-package import


def _derive_lexeme(padded_strong: str | None, lemma: str | None) -> str | None:
    """Stand-in lexeme until the lexeme-anchored spine lands: `<padded strong>|<lemma>`, which splits
    the homonyms a bare Strong's conflates (finer than strong, rolls up to it). Replaced verbatim by
    the spine's own `lexeme` column (MACULA lang+augmented-Strong's) when present."""
    if padded_strong and lemma:
        return f"{padded_strong}|{lemma}"
    return padded_strong or lemma


@dataclass
class HebToken:
    idx: int
    surface: str
    strong: str | None          # padded, e.g. H0559 (None: suffix pronouns etc.) — the ROLLUP
    lexeme: str | None          # the lexical ANCHOR: spine `lexeme` col if present, else strong|lemma
    lemma: str | None
    morph: str | None
    is_content: bool
    # from hbo.db (best-effort join):
    lex: str | None = None
    stem: str | None = None
    sp: str | None = None
    gloss_en: str | None = None
    sense: str | None = None
    sense_conf: float | None = None
    # True for Psalm-superscription vocabulary (מִזְמוֹר לְדָוִד, "for the choirmaster", tune/instrument
    # names, ...) — real spine `is_superscription` col if present, else a conservative same-verse
    # leading-run heuristic (see _tag_psalm_superscriptions). Also forces is_content=False: most target
    # editions print the superscription as an unnumbered heading, not verse text, so forcing alignment
    # against it produces wrong pairs — not just for the superscription tokens themselves, but by
    # consuming target positions that belong to the REAL verse-1 content. See internal-docs/
    # bcv-query-wishlist.md #8.
    is_superscription: bool = False
    # BHSA phrase-level syntax (2026-07-25 spine; Hebrew/OT only — no Greek layer, all None for NT):
    # phrase_id groups the tokens of one multi-word syntactic unit (construct chains like "Spirit of
    # God", coordinations, ...) under one shared id; function = the phrase's syntactic role
    # (Subj/Pred/Objc/Time/Loca/Adju/Cmpl/...), 100% phrase coverage; rela = the token's fine relation
    # WITHIN the phrase (NA=head, rec=construct-governed dependent, par=coordination).
    phrase_id: str | None = None
    function: str | None = None
    rela: str | None = None
    # Morphological agreement features (same spine build as phrase syntax; sparsely populated —
    # verbs/nouns/adjectives carry number+gender, particles/conjunctions don't). Track A/Step 4: the
    # SAME Strong's can render as different target surfaces depending on number/gender (a plural vs
    # singular occurrence of one root within a verse) — strong_surfaces' top-k back-off previously had
    # no way to prefer the surface matching THIS occurrence's own features over another occurrence's.
    number: str | None = None
    gender: str | None = None
    # filled by the aligner:
    matches: list = field(default_factory=list)


# Conservative, position-gated heuristic used ONLY when the spine lacks a real `is_superscription`
# column (see HebrewSource.__init__). Superscription vocabulary overlaps with ordinary words used
# elsewhere in the text (שִׁיר "song", תְּפִלָּה "prayer" are common outside titles too) — safe ONLY
# because we require a CONSECUTIVE match run starting at the chapter's very first content token, never
# a bare vocabulary lookup anywhere in the text.
_PSALM_SUPERSCRIPTION_LEMMAS = {
    unicodedata.normalize("NFC", w) for w in (
        "מִזְמוֹר", "שִׁיר", "מִכְתָּם", "מַשְׂכִּיל", "תְּפִלָּה", "תְּהִלָּה", "שִׁגָּיוֹן",
        "לַמְנַצֵּחַ", "מְנַצֵּחַ", "נְגִינָה", "נְגִינוֹת", "עֲלָמוֹת", "שְׁמִינִית", "גִּתִּית",
        "מָחֲלַת", "שׁוֹשַׁנִּים", "שׁוּשַׁן", "עֵדוּת", "יוֹנַת", "אֵלֶם", "רְחֹקִים",
        "אַיֶּלֶת", "הַשַּׁחַר", "תַּשְׁחֵת", "נְחִילוֹת", "דָּוִד", "אָסָף", "קֹרַח",
        "יְדוּתוּן", "שְׁלֹמֹה", "מֹשֶׁה", "הֵימָן", "אֵיתָן",
    )
}


def _tag_psalm_superscriptions(toks: list["HebToken"]) -> None:
    """Mark the LEADING RUN of content tokens whose lemma is superscription vocabulary — stops at the
    first content token that doesn't match (e.g. PSA 23:1's מִזְמוֹר/דָוִד get tagged, יְהוָה doesn't
    and ends the run). Mutates in place; call only on a psalm chapter's first verse."""
    for tok in toks:
        if not tok.is_content:
            continue
        if unicodedata.normalize("NFC", tok.lemma or "") not in _PSALM_SUPERSCRIPTION_LEMMAS:
            break
        tok.is_superscription = True
        tok.is_content = False


class HebrewSource:
    def __init__(self, spine_db: Path = SPINE_DB, hbo_db: Path = HBO_DB):
        self.spine = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
        # Forward-compat with the lexeme-anchored spine (docs/data-contracts.md): use the spine's own
        # `lexeme` column when it lands; until then derive a lexeme from (strong, lemma) so the rest of
        # the pipeline is already lexeme-anchored.
        _spine_cols = {r[1] for r in self.spine.execute("PRAGMA table_info(spine_words)")}
        self.has_lexeme = "lexeme" in _spine_cols
        # Enriched lexeme-spine carries gloss/stem/sense inline (bridge-joined upstream); read each
        # independently — NOT one bundled flag. A 2026-07-24 spine update dropped `sense`/`sense_conf`/
        # `sense_source` while KEEPING `gloss`/`stem` (verified: both still carry real data) — a single
        # `has_sense` gate on all four would have silently blanked gloss/stem too, though only sense was
        # actually gone. `has_sense` still separately gates whether to skip the hbo.db sense sidecar join
        # (that join is specifically a sense fallback, not a gloss/stem one).
        self.has_gloss = "gloss" in _spine_cols
        self.has_stem = "stem" in _spine_cols
        self.has_sense = "sense" in _spine_cols
        # Same forward-compat pattern for Psalm superscription tagging (see HebToken.is_superscription):
        # use the spine's own column the moment it lands, heuristic only until then.
        self.has_superscription_col = "is_superscription" in _spine_cols
        # BHSA phrase syntax (phrase_id/function/rela land together — one flag): see HebToken.
        self.has_phrase = "phrase_id" in _spine_cols
        # Morphological agreement features (number/gender land together — same build as phrase syntax).
        self.has_morph_features = "number" in _spine_cols
        # hbo.db is the optional per-occurrence sense sidecar (sense-mining only).
        # Statistical methods (eflomal/IBM-1) need only spine + target USJ, so a
        # missing hbo.db must not be fatal — connect only when the file is present.
        self.hbo = (sqlite3.connect(f"file:{hbo_db}?mode=ro", uri=True)
                    if Path(hbo_db).exists() else None)

    def chapters(self, book: str) -> list[int]:
        return [r[0] for r in self.spine.execute(
            "SELECT DISTINCT chapter FROM spine_words WHERE book=? ORDER BY chapter", (book,))]

    def verses(self, book: str, chapter: int) -> list[int]:
        # verse 0 = psalm superscription (title); skip — not a content-alignment target,
        # and the encode()/versification handling treats titles separately (V1-gated).
        return [r[0] for r in self.spine.execute(
            "SELECT DISTINCT verse FROM spine_words WHERE book=? AND chapter=? AND verse>=1 "
            "ORDER BY verse", (book, chapter))]

    def verse_tokens(self, book: str, chapter: int, verse: int) -> list[HebToken]:
        toks: list[HebToken] = []
        pfx = "G" if BOOK_NUMBERS.get(book, 0) >= 40 else "H"   # NT=Greek(G), OT=Hebrew(H) strongs
        cur = self.spine.execute(
            "SELECT * FROM spine_words WHERE book=? AND chapter=? AND verse=? ORDER BY idx",
            (book, chapter, verse))
        names = [d[0] for d in cur.description]
        for raw in cur:
            r = dict(zip(names, raw))
            strong = r.get("strong")
            padded = f"{pfx}{int(strong):04d}" if strong else None
            lexeme = r.get("lexeme") if self.has_lexeme else _derive_lexeme(padded, r.get("lemma"))
            # Fused multi-token names (בֵּית לֶחֶם = 2 spine tokens, ONE Strong's, one BHSA
            # lexeme): merge consecutive same-strong tokens into one alignment unit — else
            # they inflate the denominator and double-consume target tokens. (Merge on the ROLLUP;
            # a fused name is one Strong's across differing lemmas.)
            if padded and toks and toks[-1].strong == padded:
                toks[-1].surface += " " + r.get("surface", "")
                continue
            tok = HebToken(r.get("idx"), r.get("surface"), padded, lexeme,
                           r.get("lemma"), r.get("morph"), bool(r.get("is_content")))
            if self.has_superscription_col:          # real spine flag — see HebToken.is_superscription
                tok.is_superscription = bool(r.get("is_superscription"))
                if tok.is_superscription:
                    tok.is_content = False
            if self.has_stem:                        # MACULA binyan (qal/piel/hiphil)
                tok.stem = r.get("stem") or None
            if self.has_gloss:                        # inline English gloss
                tok.gloss_en = r.get("gloss") or None
            if self.has_sense:                        # disambiguated sense — shoresh keys its senses
                tok.sense = r.get("sense") or None     # on (lexeme, stem, sense); anchor is the MACULA
                tok.sense_conf = r.get("sense_conf")   # lexeme (BHSA `lex` dropped; CC-BY-clean key)
            if self.has_phrase:                        # BHSA phrase syntax (OT-only; see HebToken)
                tok.phrase_id = r.get("phrase_id") or None
                tok.function = r.get("function") or None
                tok.rela = r.get("rela") or None
            if self.has_morph_features:                # number/gender agreement (OT-only; see HebToken)
                tok.number = r.get("number") or None
                tok.gender = r.get("gender") or None
            toks.append(tok)

        if not self.has_superscription_col and book == "PSA" and toks:
            # heuristic fallback (no real spine flag yet) — only applies to a chapter's first verse
            if verse == min(self.verses(book, chapter)):
                _tag_psalm_superscriptions(toks)

        # strong-in-order join to hbo.db — only when the spine lacks inline sense AND the sidecar exists
        if self.has_sense or self.hbo is None:
            return toks
        ref = encode(book, chapter, verse)
        hbo_rows = list(self.hbo.execute(
            "SELECT lex,stem,sp,strong,gloss,sense,sense_conf FROM occurrence "
            "WHERE ref=? ORDER BY node", (ref,)))
        used = [False] * len(hbo_rows)
        for t in toks:
            if not t.strong:
                continue
            for i, (lex, stem, sp, strong, gloss, sense, conf) in enumerate(hbo_rows):
                if used[i] or strong != t.strong:
                    continue
                used[i] = True
                t.lex, t.stem, t.sp = lex, stem or None, sp
                t.gloss_en = gloss or None
                t.sense, t.sense_conf = sense or None, conf
                break
        return toks
