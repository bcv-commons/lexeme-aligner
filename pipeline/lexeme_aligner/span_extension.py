"""Widen a name/noun span by one adjacent, unclaimed target-language function word — narrowly gated by
analyze_language.py's own phase-1 audit (a real per-language-confirmed anomaly, not a blind cap) plus a
Grambank-derived DIRECTION telling which side of the span to check.

WHY THIS EXISTS: eflomal is statistical — it learns "this source word needs N target words" from repeated
co-occurrence. Common nouns recur often enough to learn a correct multi-word span; a specific proper NAME
may occur only a handful of times, so the model has far less evidence and defaults to the single most
literal word, silently dropping an adjacent case-marker/article/etc. it should have included. This mirrors
exactly the base-chain undershoot found by hand for por/hin/arb/eng this session (config/llm_conventions/
*.md) — this module is that same finding turned into an actual pipeline fix, not just an LLM prompt caveat.

NOT the general "blind extension" already measured harmful (grambank_fetch.py's own docstring: marginal
token precision decayed 22.5% -> 12.8% -> 10.5% as an untargeted extension cap rose, against a 24.4%
no-extension baseline). This is narrow on three axes at once: (1) only the POS a RISK_RULES entry already
names, (2) only languages/POS analyze_language.py's phase-1 audit ACTUALLY flags for THIS base-chain run
(same anomaly threshold as the audit, not "any Grambank-flagged language"), (3) only in the Grambank-
derived DIRECTION for that category — never both directions, never a guess when the language's own order
is mixed/ambiguous.

MEASURED (this session, real manual Clear gold, name/noun lexemes only, before -> after):
  hin case_marking (postpositional, extend forward):  exact 1,941->2,091  F1 .748->.786  AER .252->.214
  arb case_marking (prepositional,  extend backward): exact 44,913->45,280 F1 .881->.886 AER .119->.114
  eng articles      (prenominal,    extend backward): exact 34,445->50,624 F1 .632->.741 AER .368->.259
All three: precision held roughly flat (a modest, expected cost) while recall/F1/AER improved — net gain,
not a wash, in three languages with opposite word orders. See internal-docs/llm-align-experiment-plan.md.

DIRECTION_FEATURES only lists categories with a *validated* direction signal AND a *validated* real
undershoot (checked this session). `possession_affix`/`tam_auxiliary`/`tam_affix`/`subject_indexing` are
deliberately absent — extending those needs its own gold check first, not an assumption this mechanism
generalizes automatically just because the Grambank shape is similar.

`case_marking` ALSO gets a second, ADDITIVE trigger (`case_marking_direction` below): a content word whose
own occurrence carries the spine's STRUCTURED morphology (Hebrew `state == "construct"`, Greek
`case_ in {"genitive", "dative"}`) extends even when its lexeme-level POS tag (from the prior pack,
sometimes missing or not one of the RISK_RULES-flagged categories) never made it into `active` — a
per-occurrence hard signal the original design didn't have access to, built alongside the LLM-alignment
prompt's own `[construct]`/`[genitive]` tags (llm_prompt.py's PROMPT_VERSION changelog) this same session.

MEASURED 2026-09-24 (real Clear gold, whole hin NT / whole arb + eng Bible, all content lexemes — a
broader, more diluted grain than the original 2026-07 citation below, since span_extension only ever
touches name/noun tokens and this scoring includes every content word): tried REPLACING the pos-tag gate
with a state/case_-REQUIRED gate first (an AND) — this measured WORSE than the original pos-tag-only
design on both languages (hin F1 .624->.616, arb F1 .843->.842): the coarse pos-tag gate already catches
genuine cases the strict genitive/dative/construct check misses (Hindi's postposition need isn't always
literally "genitive/dative" in the Greek source), so narrowing trades away more recall than it gains in
precision. The ADDITIVE (OR) form shipped instead — never removes anything the pos-tag gate already
catches, only adds occurrences it would otherwise miss — and this one is a clean, unambiguous win over
BOTH the baseline and the original pos-tag-only design:
  hin case_marking, whole NT:      F1 .603(baseline) -> .624(pos-tag only) -> .627(+ additive)
  arb case_marking, whole Bible:   F1 .841(baseline) -> .843(pos-tag only) -> .844(+ additive)
`articles` (eng) is untouched by this — no comparably reliable structured signal exists yet for Hebrew
definiteness (the spine's `morph` column is present but empty for every row; `state` marks Aramaic
`determined` state specifically, far too rare — 849 rows spine-wide — to serve as a general "has the
definite article" signal). Confirmed unaffected: eng articles still shows F1 .598->.664 after this change.

TRIED, NOT SHIPPED (same session): `subject_indexing` (pro-drop -> supply a subject pronoun), using the
spine's own `person` field as the per-occurrence hard signal (a verb whose own inflection carries person
marking) and a derived `subject_order` direction (Grambank has no dedicated subject-pronoun-position
feature — checked the full parameter list — but verb-final order (GB133) places the subject before the
verb and verb-initial (GB131) places it after, close to tautologically). This ALSO needed to bypass
analyze_language's phase-1 rate-anomaly audit entirely (not route through `active`/DIRECTION_FEATURES):
a verb's own baseline multiword rate already runs high (~8% for hin, vs ~2-5% for name/noun, from
unrelated auxiliary/phrasal-verb renderings), which dilutes the rate-anomaly signal below its 0.05
threshold even for hin (GB089=0/GB090=0, genuinely no verb-internal subject indexing at all) — so the
audit never flags it regardless of the real need. Direction derivation checked out against textbook
typology (hin SOV -> before, arb VSO -> after). But measured on real Clear gold (hin whole NT, combined
with the case_marking additive fix above) it was a WASH, not a win: F1 unchanged (.627 -> .627), with
exact_span and precision both slightly worse (14,803->14,627; .842->.824) for a small recall gain —
likely target-position contention with the already-working case_marking extensions, or the "vah"/"voh"-
type candidate words the stopword filter accepts not reliably being the correct rendering. Reverted
rather than shipped; not registered in DIRECTION_FEATURES or grambank_fetch.FEATURES (see that module's
own comment) to avoid dead code a reader would reasonably assume is in active use. `tam_auxiliary` was
not even attempted — Grambank has no comparable order feature (checked) and no plausible tautological
derivation exists the way verb-order gives one for subject position.

`possession_affix` (a possessed noun with a Hebrew/Greek pronominal suffix -> supply a free possessive
word, "his house") IS wired (`possession_direction_for`, GB065 — a single ternary value, 1=possessor-
precedes/2=follows/3=both, not a before/after pair like the others) and routes through the SAME pos-tag
path as `articles`/`case_marking`, correctly flagged for eng (the one language checked that has it).
Found and fixed a real bug while testing it: `active` used to keep only the FIRST flagged risk per pos, so
`articles` (which precedes `possession_affix` in RISK_RULES) silently blocked it from ever firing at all
for eng's "noun" — now `active[pos]` holds every flagged (direction, risk) for that pos, tried in order,
falling through when an earlier one's own candidate position isn't valid for THIS occurrence. Verified via
a synthetic test with genuinely different directions per risk. HONEST STATUS, not a demonstrated win like
case_marking above: for eng specifically, `articles` and `possession_affix` both derive "before" and the
candidate check is content-agnostic (any function word, not specifically "the" vs "his"), so `articles`
already captures whatever sits there either way — engbsb's own extension counts are IDENTICAL before and
after wiring possession_affix in (42,943 pairs, unchanged). And the two other candidate languages checked
(hin, fra) both have GB430-433 all '0' (no affixal possession at all) — a case that plausibly NEEDS a free
possessive word most of all — yet RISK_RULES' own "any_one" polarity for this category never flags a
language whose affix features are all-zero (it flags eng only because English's unrelated 's-clitic
happens to trip GB432=1).

STEP 0 (aim1-typology-source-structure-plan.md, 2026-09-24): tried the "miscalibrated polarity" fix noted
above — a new `all_zero` polarity in analyze_language.RISK_RULES (flag possession_affix only when every
GB430-433 is exactly "0"). MEASURED, real Clear gold, whole Bible, isolated via an A/B against the
already-shipped case_marking/articles triggers (i.e. "with possession_affix" vs "without", both on top of
the same base eflomal+gloss):
  hin: F1 .5508(without) -> .5516(with)  +0.0008   precision .7399 -> .7328   -0.0071
  fra: F1 .8274(without) -> .8059(with)  -0.0215   precision .7795 -> .7417   -0.0380
hin is a wash (F1 flat, inside noise) with a precision cost past the plan's own -0.005 bar; fra is a clear
loss on top of an ALREADY-REGRESSED baseline (see below). REVERTED — `possession_affix` is back to
`any_one` in analyze_language.py, matching its original (never-shipped-as-a-win, but not measured-worse
either) status. The `all_zero` polarity itself stays implemented and tested in analyze_language.py as a
generically correct mechanism for a plain-existence Grambank category; it just isn't this one.

**New finding surfaced while isolating the above (fra-lsg had never been scored with spanext before):**
`articles` alone — already-shipped, unchanged this session — reads as a REGRESSION for French at
whole-Bible scale: eflomal+gloss F1 .8749 -> spanext(articles only)+eflomal+gloss F1 .8274, a 4.75pt
drop (exact_span 42619->37274, precision .8687->.7795). Contradicts the eng articles win (.598->.664)
cited above and was NOT previously measured (fra was untested until this A/B). **ROOT-CAUSED
2026-09-24 (a follow-up investigation, not the same session as the number above) — the ORIGINAL
hypothesis here (French is dense with short ambiguous function words, over-triggering broadly) was
WRONG in its main claim.** Broke every "regressed" gold link into two buckets by checking whether the
extra target position the extension grabbed was one **Clear's own gold left completely unclaimed**
(no source word at all) vs one gold **explicitly assigns to a DIFFERENT source word** (a real
conflict):
  whole-Bible fra, 5,406 flagged regressions total: 4,978 (92%) gold-unclaimed · 428 (8%) real conflict
  the same breakdown on the "name" subset (680 of the 5,406): 592 (87%) gold-unclaimed · 88 (13%) conflict
- **The 92% is a GOLD-CURATION-CONVENTION ARTIFACT, not an alignment defect.** Checked Clear's own raw
  parquet rows directly (not our re-derived surfaces): for Matt 1:20 G0032 ("an angel"), Clear's
  **French** (LSG) and **Spanish** (RV09) manual gold both link G0032 to ONLY the noun ("ange" /
  "ángel") — the indefinite article is simply never tagged to any source word. Clear's **English**
  (BSB) gold, by contrast, DOES tag it — TWO rows, "an" + "angel", both -> G0032. Bengali/Assamese tag
  a numeral-origin indefinite marker ("এক"/"one") but not a pure grammaticalized article. So whether an
  article is "in the gold span" for a given occurrence is a per-language ANNOTATION CONVENTION, not a
  fact about the translation — the SAME extension ("un ange" is unambiguously the correct French
  rendering, confirmed by inspection of every sampled case) can only ever look like a win against a
  gold that includes articles (English) and can only ever look like a loss against one that excludes
  them (French, Spanish, plausibly most others), independent of whether extending is a good idea.
  **Methodological consequence for this whole plan**: exact-span F1 against Clear gold cannot, by
  itself, validate or reject an extension whose "correctness" depends on whether the annotator chose
  to tag a function word at all — a language-pair confound, not a signal about the mechanism. The
  eng articles win is real ON ENG's OWN GOLD but was never good evidence the mechanism generalizes;
  it happened to be tested on the one Clear language whose convention rewards it.
- **The 8% (428 cases) is a genuine, specific, worth-fixing bug**: `articles`'s candidate check
  accepts ANY stopword, including POSSESSIVE DETERMINERS (son/sa/ses/leur/leurs/…) — a grammatically
  different closed class from articles (possession-marking, not definiteness-marking). Sample, hand-
  checked against Greek: "ses disciples" grabbed "ses" for the noun's span, but Clear's gold assigns
  that exact position to G0846 (αὐτοῦ, "his") — a genitive personal pronoun that is a SEPARATE source
  token, one our OWN base eflomal+gloss chain had simply failed to align (confirmed: the position was
  UNCLAIMED by our alignment before spanext ran, not already correctly linked and then stolen) — so
  `articles` greedily filled a real upstream gap with the wrong attribution instead of leaving it for
  gap-fill or a genitive-pronoun-aware mechanism. Same pattern for "son nom"/G0846, "leurs trésors"/
  G0846, "ses miracles"/G0846 — always the same lexeme, autos/G0846, the single most common Greek
  3rd-person genitive pronoun. **Not patched here**: Step 1's relation-aware redesign (requiring an
  occurrence-level structural signal — Hebrew `rela`, Greek `case_` — instead of blind stopword
  membership) will not accept a bare possessive determiner as an article candidate at all once built,
  which resolves this category of error by construction rather than needing a French-specific
  possessive-word list bolted onto the current mechanism.
- **Net assessment, corrected from the earlier caution**: `articles` for fra is NOT demonstrated to be
  harmful — the aggregate F1 drop is overwhelmingly a scoring artifact, and the one real bug found
  (possessive-determiner stealing) affects <1% of all gold links, not the >4pt this metric implies.
  Still NOT wired into any production consumer by default (unchanged), but the earlier blanket "do not
  ship for fra" framing overstated the risk; the honest status is "can't be validated by this metric,
  one small known bug, likely net-neutral-to-positive linguistically" — revisit once Step 1 ships or a
  conflict-aware metric (ignore gold-unclaimed extensions, only penalize real steals) is built.
Relevant to Step 1's Correction 2 (widening `case_marking`'s existence gate to Romance languages via
adposition direction instead of morphological case): the SAME possessive-determiner-vs-genitive-
pronoun confusion is a real risk for the new trigger too, and Step 1's structural-signal requirement
is the fix for both, not a French-specific word list.

STEP 1 (aim1-typology-source-structure-plan.md §4, 2026-09-24) — three pieces, one shipped unconditionally,
two built and measured as opt-in flags that stay OFF by default (neither is a win yet):

**Correction 2 (SHIPPED, unconditional, config-only)**: `case_marking`'s existence gate in
`grambank_fetch.FEATURES` now ALSO fires on a directional adposition (GB074/075), not just
morphological case (GB070/072) — eng/fra/por (GB072=0, GB074=1) get the genitive trigger they were
wrongly denied. MEASURED: eng whole Bible F1 .664->.667 (+658 exact_span), precision flat (.864->.864)
— a clean, real win (English's gold DOES include function words in spans — see the fra-regression
root-cause above, so this number is not the artifact that one was). hin/arb unaffected (already
GB070/072=1, no config change reaches them). fra whole Bible: F1 .8274->.8206 (further down) — EXPECTED,
not a new problem: this is the SAME gold-convention artifact already diagnosed above, now also touching
the newly-enabled genitive "de" extensions; not separately re-verified position-by-position, but nothing
about Correction 2 changes the mechanism, only which languages' existence gate it clears.

**`--relation-trigger` (Correction 1, BUILT, MEASURED, NOT shipped — real conflict rate too high).**
The `rela_of`-driven possessor path in `extend_spans` extends the Hebrew POSSESSOR (`rela=="rec"`)
additively, a token the existing `state=="construct"` (head) check
structurally never reaches; fixes the "wrong token" bug identified in the plan's research (the
shipped hin win above was measured on NT/Greek, where `case_`=genitive already marks the possessor
directly — hin's Hebrew/OT half was never tested until now). MEASURED, hin OT, isolated to just the
311 possessor-triggered pairs (whole-Bible aggregate barely moved: F1 .518->.518, exact_span
21607->21636): of the 169 pairs with a checkable gold link, only **47 correct (27.8%), 79 real
conflicts (46.7%, stealing a position gold assigns to a DIFFERENT source token), 43 gold-unclaimed
(25.4%)** — a MUCH worse conflict rate than the fra articles case (8%), so this is NOT primarily a
gold-convention artifact this time. Root cause, hand-checked: multi-member construct chains (3+ levels,
e.g. Hindi "बेलबूटे की कढाई का काम") — the SAME genitive postposition (का/की/के) recurred across
several sampled conflicts, always attributed to the WRONG adjacent rectum in the chain. The naive
"immediately adjacent + unclaimed + is_function" candidate check has no notion of WHICH position within
a multi-level chain a given postposition belongs to; `construct_group` carries the whole chain but this
implementation doesn't yet use its internal ORDER to disambiguate. **Not shipped — stays opt-in,
default off.** A real fix needs the chain's own member order (from `construct_group` + verse position),
not attempted here; flagged as the natural next refinement, not a dead end like `subject_indexing`.

**`--definite-trigger` (derived definiteness, BUILT, MEASURED, NOT shipped — no evidence of value).**
`compute_definite` (previous-token-is-article / assimilated-article / proper-name, with construct-head
inheritance) as an additive trigger on `article_order` direction. MEASURED, eng OT (the only Clear-gold,
Hebrew-source language with an unambiguous `article_order` direction — hin has NO articles at all,
GB022/023 both 0; arb's GB022=GB023=1, ambiguous): whole-OT aggregate unchanged (F1 .646->.646,
exact_span 38801->38800). Isolated to the 401 definite-triggered pairs: **391 (97.5%) have no
checkable gold link at all**; of the 10 that do, 0 correct, 7 conflict, 3 gold-unclaimed — a sample too
small to trust on its own, but combined with zero aggregate movement, there is no evidence this trigger
adds value as built. Plausible cause (not confirmed): the proper-name rule dominates `compute_definite`'s
output (every occurrence of a name lexeme is flagged, and names are exactly the category Clear's gold
tends to leave unclaimed for a leading article/genitive — see the fra-regression section above), so most
firings land where the metric structurally can't confirm or deny them. **Not shipped — stays opt-in,
default off**; the mechanism and its tests are kept (a correct implementation of the plan's design) in
case a language or a conflict-aware metric someday shows it earns its keep, same "kept, not proven"
status as `possession_affix`'s wiring.

Ships as its OWN opt-in method layer (`align_spanext_<iso>_<BOOK>.jsonl`), the same additive-union
precedent as `residual`/`llm` (compact_align.py's LAYER_METHODS, export_lex.py's `_METHODS`) — never
touches the base chain's own eflomal/gloss files, and a consumer that ignores it keeps today's exact
behavior. Wire it in explicitly (e.g. `merge_align --methods eflomal,gloss,spanext`,
`export_lex --methods eflomal,gloss,gapfill,spanext`) once you've decided to trust it for a language.

    python3 -m lexeme_aligner.span_extension --iso hinirv --publish-iso hin --usj-dir <dir> --nt \\
        --methods eflomal,gloss
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.analyze_language import analyze
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill import load_priors
from lexeme_aligner.grambank_fetch import FEATURES as GRAMBANK_FEATURES
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, build_corpus
from lexeme_aligner.target_stopwords import StopwordFilter
from lexeme_aligner.usj_source import tokenize
from lexeme_aligner.versification import remapper

# risk_key (from analyze_language.RISK_RULES) -> the grambank_fetch.FEATURES direction pair that tells us
# which side of the span the missing word falls on. [0] = "before" (extend backward from the span's own
# minimum position), [1] = "after" (extend forward from the span's own maximum position).
DIRECTION_FEATURES = {
    "case_marking": "adposition_order",
    "articles": "article_order",
}


def load_grambank_raw(publish_iso: str, path=None) -> dict[str, str] | None:
    from lexeme_aligner.analyze_language import load_grambank
    return load_grambank(publish_iso, path)


# grambank_fetch's "<x>_order" feature-group name -> typology.py's short slot name — the two modules
# were built at different times with slightly different naming conventions; kept as an explicit table
# rather than a string-suffix trick so a future feature-group name doesn't silently map to nothing.
_TYPOLOGY_SLOT = {"adposition_order": "adposition", "article_order": "article",
                  "possession_order": "possessor", "subject_verb_order": "subject_verb",
                  "object_verb_order": "object_verb"}


def direction_for(grambank: dict[str, str], feature_group: str, iso: str | None = None) -> str | None:
    """"before" / "after" / None (mixed, ambiguous, or absent — never guess). Step 2: when Grambank
    alone is silent (`grambank` missing the relevant codes for this language — the ~51% of published
    languages Grambank doesn't cover) and `iso` is given, falls back to the pre-built typology table
    (`typology.direction`, built ONLY from sources whose agreement with Grambank was measured and
    cleared 90% — see typology.py's own docstring). `iso=None` (the default) keeps this
    Grambank-only, unchanged from before Step 2 — every existing call site that doesn't pass `iso`
    is unaffected."""
    before_id, after_id = GRAMBANK_FEATURES[feature_group]
    before = grambank.get(before_id) == "1"
    after = grambank.get(after_id) == "1"
    if before and not after:
        return "before"
    if after and not before:
        return "after"
    if iso is not None:
        from lexeme_aligner import typology
        slot = _TYPOLOGY_SLOT.get(feature_group)
        if slot:
            return typology.direction(iso, slot)
    return None


def possession_direction_for(grambank: dict[str, str], iso: str | None = None) -> str | None:
    """"before" / "after" / None, from GB065 directly — a single ternary value (1=possessor precedes
    possessum, 2=possessor follows, 3=both/free), not a before_id/after_id binary pair like
    direction_for()'s other callers, so it gets its own small helper instead of forcing GB065 through
    that shape. Same Step 2 typology-table fallback as `direction_for` when `iso` is given."""
    v = grambank.get(GRAMBANK_FEATURES["possession_order"][0])
    if v == "1":
        return "before"
    if v == "2":
        return "after"
    if iso is not None:
        from lexeme_aligner import typology
        return typology.direction(iso, "possessor")
    return None


# The article token itself — precedes a content token 22,673 times spine-wide (Step 1's own research,
# internal-docs/aim1-typology-source-structure-plan.md §2.4). Free-standing, distinct from the
# assimilated (swallowed-by-a-preposition) case `HebrewSource.assimilated_after_idx` covers.
_DEFINITE_ARTICLE_LEXEME = "hbo:1886a"


def compute_definite(heb_toks: list, lex_pos: dict[str, str], assimilated_after: set[int]
                     ) -> dict[int, bool]:
    """Step 1 (C', internal-docs/aim1-typology-source-structure-plan.md §4): {h_idx: definite}, derived
    without any bcv-query ask — three deterministic signals, all from data already in hand:
      1. the previous token IS the free-standing definite article (`hbo:1886a`);
      2. this token's idx is `after_idx + 1` for an assimilated (preposition-swallowed) article in this
         verse (`HebrewSource.assimilated_after_idx`);
      3. the token is a PROPER NAME (`lex_pos[lexeme] == "name"`, the prior-pack's own target-agnostic
         classification, already trusted everywhere else in this module) — a name is inherently
         definite cross-linguistically, with no source article needed to mark it.
    Then construct-HEAD inheritance: Hebrew grammar makes an entire construct chain definite when its
    FINAL member (the rectum, or the rectum's own rectum in a longer chain) is definite, even though
    the head noun itself never takes an article ("melek ha-arets", lit. "king the-land" = "the king of
    the land", not "the king of land") — so if ANY member of a `construct_group` is definite (typically
    the last, but a chain's own construct_group already groups every member, checked once, not walked
    positionally), every OTHER member whose own `state == "construct"` (a head, never a bare absolute
    non-head) inherits it. A member with no `construct_group` at all is judged on rules 1-3 alone.
    Greek (no `state`/`construct_group`, no assimilated-article table) always returns everything False
    here — `articles` already has no comparable structured signal for Greek (only for gloss-derived
    `the.` markers, proven unreliable — see this module's `case_marking` section) and this function
    doesn't invent one; callers gate on `heb.has_state`/`has_assimilated_articles` before trusting it."""
    by_idx = {t.idx: t for t in heb_toks}
    definite: dict[int, bool] = {}
    for t in heb_toks:
        prev = by_idx.get(t.idx - 1)
        definite[t.idx] = bool(
            (prev and prev.lexeme == _DEFINITE_ARTICLE_LEXEME)
            or (t.idx - 1) in assimilated_after
            or lex_pos.get(t.lexeme) == "name"
        )
    groups: dict[str, list] = collections.defaultdict(list)
    for t in heb_toks:
        if t.construct_group:
            groups[t.construct_group].append(t)
    for members in groups.values():
        if any(definite.get(m.idx) for m in members):
            for m in members:
                if m.state == "construct":
                    definite[m.idx] = True
    return definite


def _books(a) -> list[str]:
    return (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in (a.book or ["MAT"])])


def extend_spans(iso: str, publish_iso: str, usj_dir: Path, books: list[str], out_dir: Path = OUT,
                 methods: tuple[str, ...] = ("eflomal", "gloss"), prior_pack: Path = PRIOR_PACK,
                 definite_trigger: bool = False, relation_trigger: bool = False,
                 typology_fallback: bool = False) -> tuple[dict[str, list[dict]], dict]:
    """{BOOK: [verse record, ...]} of ONLY the pairs that got widened, plus stats. Never mutates the base
    chain's own jsonl — this is a separate, additive layer (see module docstring). `definite_trigger`:
    Step 1's derived-definiteness additive trigger (`compute_definite`). `relation_trigger`: Step 1's
    Correction 1 fix — extend the Hebrew POSSESSOR (`rela=="rec"`) additively, a token the existing
    `state=="construct"` (head) check structurally never reaches. `typology_fallback`: Step 2's
    WALS/lang2vec-backed direction table for languages Grambank doesn't cover — MEASURED net positive
    for spa, net negative for ben/asm (see `analyze_language.analyze`'s own docstring for numbers);
    a genuine per-language split, not a metric artifact. All three OFF by default, opt-in via
    `--definite-trigger`/`--relation-trigger`/`--typology-fallback`, until measured per-language
    (internal-docs/aim1-typology-source-structure-plan.md §4)."""
    lex_pos, _ = load_priors(prior_pack)
    grambank_raw = load_grambank_raw(publish_iso)
    stats: collections.Counter = collections.Counter()
    if grambank_raw is None:
        # Step 2: a language absent from Grambank entirely (~51% of published languages) can still
        # have a typology-table entry (WALS/lang2vec, validated against Grambank elsewhere — see
        # typology.py) — but ONLY reachable when `typology_fallback` is explicitly on (see docstring
        # above: measured net-negative for 2 of the 3 languages tested). Without it, a Grambank-absent
        # language skips exactly as it always has.
        if not typology_fallback:
            return {}, {"skipped": "no Grambank coverage for this language"}
        from lexeme_aligner import typology
        if not any(typology.direction(publish_iso, slot) is not None for slot in typology.SLOTS):
            return {}, {"skipped": "no Grambank or typology coverage for this language"}
    grambank = grambank_raw or {}   # downstream code calls grambank.get(...) unconditionally

    # Which (risk, pos) combinations are ACTUALLY flagged for this base-chain run, and in which direction —
    # reuses analyze_language's own anomaly detection so this mechanism never fires on a category the
    # phase-1 audit itself wouldn't flag as worth checking.
    report = analyze(iso, publish_iso, out_dir, prior_pack, method=methods[0], use_typology=typology_fallback)
    _typology_iso = publish_iso if typology_fallback else None
    # pos -> [(direction, risk), ...], in RISK_RULES' own findings order (its priority). A pos can carry
    # MORE THAN ONE flagged risk — e.g. English "noun" gets both `articles` ("the servant") and
    # `possession_affix` ("his servant"), genuinely different needs for different occurrences of the same
    # POS, not competing guesses about the SAME occurrence. An earlier version kept only the first match
    # per pos, so `possession_affix` never got a chance at all whenever `articles` (which precedes it in
    # RISK_RULES) was also flagged — caught by testing on real engbsb data: possession_affix produced
    # ZERO extensions despite being correctly flagged, because "noun" was already claimed. The widening
    # loop below tries each in order, falling through only when an earlier one's own candidate position
    # isn't valid (already claimed or not a function word) for THIS specific occurrence.
    active: dict[str, list[tuple[str, str]]] = {}
    for f in report.get("findings", []):
        risk, pos = f.get("risk"), f.get("pos")
        if risk == "possession_affix":
            d = possession_direction_for(grambank, iso=_typology_iso)   # GB065 is ternary, not before/after
        elif risk in DIRECTION_FEATURES:
            d = direction_for(grambank, DIRECTION_FEATURES[risk], iso=_typology_iso)
        else:
            continue
        if d:
            active.setdefault(pos, []).append((d, risk))
    if not active:
        return {}, {"skipped": "no flagged (pos, direction) combination for this language", **dict(stats)}
    # case_marking's own direction (GB072/adposition_order is a LANGUAGE-level fact, not really
    # POS-specific) — used below as a SEPARATE, ADDITIVE trigger alongside the pos-tag gate above, for a
    # content word whose occurrence-level state/case_ confirms the marking even when its own lexeme-level
    # POS tag (from the prior pack, sometimes missing/imperfect) didn't make it into `active`. Measured
    # (2026-09-24, real Clear gold, whole hin NT / whole arb Bible): REPLACING the pos-tag gate with a
    # state/case_-REQUIRED gate (AND) modestly hurt both languages (hin F1 .624->.616, arb F1 .843->.842)
    # — the coarse pos-tag gate already catches genuine cases the strict genitive/dative check misses, so
    # narrowing loses more recall than it gains in precision. This additive (OR) form is what's active.
    case_marking_direction = None
    for f in report.get("findings", []):
        if f.get("risk") == "case_marking":
            d = direction_for(grambank, DIRECTION_FEATURES["case_marking"], iso=_typology_iso)
            if d:
                case_marking_direction = d
            break
    # Step 1 (C'): the derived-definiteness additive trigger reads Grambank's article_order DIRECTLY,
    # not gated on analyze_language's own multiword-rate audit flagging "articles" for some POS the way
    # case_marking_direction above is — the whole point of a hard per-occurrence structural signal
    # (compute_definite) is to catch occurrences a coarse lexeme-level audit has no way to single out.
    # Step 2: also falls through to the typology table when Grambank itself has no article_order pair.
    article_order_direction = direction_for(grambank, DIRECTION_FEATURES["articles"], iso=_typology_iso)

    heb = HebrewSource()
    recs = build_corpus(books, usj_dir, heb, remap=remapper(iso, str(usj_dir)))
    lexeme_of: dict[int, dict[int, str]] = {}             # ref -> h_idx -> lexeme (for POS lookup)
    # ref -> h_idx -> (state, case_): the spine's own STRUCTURED per-occurrence morphology (Hebrew
    # construct/absolute state, Greek case), not gloss text or a lexeme-level POS guess. Used below to
    # require the SPECIFIC occurrence actually carry the marking `case_marking` extends for, instead of
    # extending every noun/name in a Grambank-flagged language uniformly (this module's original design,
    # which had no per-occurrence signal available yet). Falls back to the original POS-only gate when
    # the structured field isn't populated for this testament/spine build, so recall on an unstructured
    # build never regresses below what was already measured and shipped.
    struct_of: dict[int, dict[int, tuple[str | None, str | None]]] = {}
    # ref -> h_idx -> rela: the Hebrew POSSESSOR/rectum flag (Correction 1, Step 1). `state=="construct"`
    # (struct_of above) marks the construct chain's HEAD, not its possessor — a postpositional language
    # needs the ADJACENT function word attached to the POSSESSOR ("prabhu KA sevak"), a different token
    # entirely, so this is tracked separately rather than folded into struct_of.
    rela_of: dict[int, dict[int, str | None]] = {}
    definite_of: dict[int, dict[int, bool]] = {}          # ref -> h_idx -> definite (Step 1, Hebrew-only)
    verse_toks: dict[int, list[str]] = {}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        verse_toks[ref] = list(r.toks)
        lexeme_of[ref] = {t.idx: t.lexeme for t in r.heb}
        struct_of[ref] = {t.idx: (t.state, t.case_) for t in r.heb}
        rela_of[ref] = {t.idx: t.rela for t in r.heb}
        definite_of[ref] = compute_definite(r.heb, lex_pos, heb.assimilated_after_idx(r.book, r.ch, r.v))
    has_struct = heb.has_state or heb.has_case
    has_definite_signal = heb.has_state or heb.has_assimilated_articles
    has_relation_signal = heb.has_phrase                    # rela lands with phrase_id (OT-only)

    stop = StopwordFilter(publish_iso, str(usj_dir))

    # Build ONE unified per-(ref, h_idx) view across methods first (first method in `methods` order
    # wins a contested h_idx — the same "first wins" convention merge_align/compact_align already use),
    # THEN decide extensions and "claimed" positions from that single view. Scanning each method's own
    # jsonl independently (an earlier version of this function did) computes "claimed" separately per
    # method and can process the same h_idx twice under two different claimed-sets when eflomal and
    # gloss both cover it — an inconsistent, method-order-dependent result, not a real per-language
    # finding. Caught this live comparing an isolated single-method test against the real multi-method
    # run: they disagreed for Arabic specifically because of this bug, not because nouns behave
    # differently from names.
    unioned: dict[int, dict[int, dict]] = collections.defaultdict(dict)   # ref -> h_idx -> pair
    meta: dict[int, dict] = {}                                            # ref -> {book, chapter, verse}
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    meta.setdefault(ref, {"book": rec["book"], "chapter": rec["chapter"], "verse": rec["verse"]})
                    verse = unioned[ref]
                    for p in rec["pairs"]:
                        if p.get("t_idx") and p["h_idx"] not in verse:
                            verse[p["h_idx"]] = p

    out: dict[str, list[dict]] = collections.defaultdict(list)
    for ref, verse in unioned.items():
        toks = verse_toks.get(ref)
        if not toks:
            continue
        claimed: set[int] = set()
        for p in verse.values():
            claimed |= set(p["t_idx"])
        widened = []
        for p in verse.values():
            if not p.get("content"):
                continue
            pos = lexeme_of.get(ref, {}).get(p["h_idx"]) and lex_pos.get(lexeme_of[ref][p["h_idx"]])
            t_idx = sorted(p["t_idx"])
            # Step 1: at most ONE extension per SIDE (before/after), but now BOTH sides can fire for the
            # same occurrence when two INDEPENDENT signals justify it (the "the covenant OF" fertility-2
            # case — a leading definite-article extension and a trailing genitive extension are two
            # different needs, not competing guesses about the same missing word). `extensions` collects
            # them in trial order; `sides_used` is the actual one-per-side cap.
            extensions: list[tuple[str, str, int]] = []          # (direction, stat_label, candidate)
            sides_used: set[str] = set()

            def _try_extend(direction: str, stat_label: str) -> bool:
                if direction in sides_used:
                    return False
                cur = sorted(t_idx + [c for _, _, c in extensions])
                c = cur[0] - 1 if direction == "before" else cur[-1] + 1
                if 0 <= c < len(toks) and c not in claimed and stop.is_function(toks[c]):
                    extensions.append((direction, stat_label, c))
                    sides_used.add(direction)
                    return True
                return False

            # Try each flagged (direction, risk) for this pos IN ORDER, falling through when one's own
            # candidate position isn't valid for THIS occurrence (already claimed, or not a function
            # word) — not just when the pos itself has no entry. Two+ risks sharing a pos (e.g. English
            # "noun" carrying both `articles` and `possession_affix`) are genuinely different needs for
            # different occurrences, not competing guesses about the same one; see `active`'s own comment.
            for d, _risk in active.get(pos, []):
                if _try_extend(d, pos):
                    break
            if case_marking_direction and has_struct:
                # ADDITIVE path: the lexeme's own POS tag didn't match (missing from the prior pack, or
                # not one of the categories the phase-1 audit flagged), but THIS occurrence's own
                # structured morphology confirms the case_marking relation independently. Never removes
                # anything the pos-tag gate above already catches — only adds cases it would otherwise
                # miss. See case_marking_direction's own comment for why this is additive, not a
                # replacement: a version that REQUIRED this confirmation (dropping the pos-tag gate)
                # measured worse on both hin and arb (see below).
                state, case_ = struct_of.get(ref, {}).get(p["h_idx"], (None, None))
                if state == "construct" or case_ in ("genitive", "dative"):
                    _try_extend(case_marking_direction, "struct")
            if relation_trigger and case_marking_direction and has_relation_signal:
                # Correction 1 (Step 1): the struct path above extends the construct HEAD (the
                # possessum — "servant" in "servant of the LORD"); a postpositional language needs the
                # function word attached to the POSSESSOR instead ("prabhu KA sevak"), a DIFFERENT
                # occurrence (`rela=="rec"`, the Hebrew rectum) the struct check structurally never
                # reaches — additive, not a replacement (never removes the struct path's own catches).
                # For prepositional languages both land on the same side by construction (`case_marking_
                # direction` is the same GB074/075-derived value for either token), so this mainly
                # matters for postpositional ones (hin OT — the untested half of the shipped hin win,
                # which was measured on NT/Greek, where `case_` already marks the possessor directly).
                if rela_of.get(ref, {}).get(p["h_idx"]) == "rec":
                    _try_extend(case_marking_direction, "possessor")
            if definite_trigger and article_order_direction and has_definite_signal:
                # Step 1 (C'): derived definiteness (compute_definite) as an ADDITIVE trigger, parallel
                # to case_marking's structured-signal path above — fires even when the lexeme's own POS
                # tag missed `active`, gated on `article_order` direction (not the coarse RISK_RULES
                # audit). Opt-in (`--definite-trigger`) until measured; see module docstring for results.
                if definite_of.get(ref, {}).get(p["h_idx"]):
                    _try_extend(article_order_direction, "definite")
            if not extensions:
                continue
            new_t_idx = sorted(t_idx + [c for _, _, c in extensions])
            new_target = " ".join(toks[j] for j in new_t_idx)
            ext = dict(p)
            ext.update(t_idx=new_t_idx, target=new_target, method="spanext",
                      prior="+".join(f"spanext_{lbl}_{d}" for d, lbl, _ in extensions))
            widened.append(ext)
            for _, _, c in extensions:
                claimed.add(c)
            for _, lbl, _ in extensions:
                stats[f"extended_{lbl}"] += 1
        if widened:
            m_ref = meta[ref]
            out[m_ref["book"]].append({"ref": ref, "book": m_ref["book"], "chapter": m_ref["chapter"],
                                       "verse": m_ref["verse"], "pairs": widened})
    stats["active_pos_direction"] = len(active)
    return dict(out), dict(stats)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="base-chain edition tag (align_<method>_<iso>_*.jsonl)")
    ap.add_argument("--publish-iso", required=True, help="bare published iso, for the Grambank lookup")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--book", action="append")
    ap.add_argument("--nt", action="store_true")
    ap.add_argument("--ot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--methods", default="eflomal,gloss",
                    help="base-chain methods to read pairs from (comma-sep)")
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--definite-trigger", action="store_true",
                    help="Step 1's derived-definiteness additive trigger (compute_definite) — "
                         "opt-in, off by default until measured")
    ap.add_argument("--relation-trigger", action="store_true",
                    help="Step 1's Correction 1 fix — extend the Hebrew possessor (rela==rec) "
                         "additively — opt-in, off by default until measured")
    ap.add_argument("--typology-fallback", action="store_true",
                    help="Step 2's WALS/lang2vec direction table for languages Grambank doesn't "
                         "cover — measured net positive for spa, net negative for ben/asm; opt-in "
                         "per language until a quality gate exists (see analyze_language.analyze)")
    a = ap.parse_args(argv)

    books = _books(a)
    methods = tuple(m.strip() for m in a.methods.split(","))
    by_book, stats = extend_spans(a.iso, a.publish_iso, a.usj_dir, books, a.out, methods, a.prior_pack,
                                  definite_trigger=a.definite_trigger,
                                  relation_trigger=a.relation_trigger,
                                  typology_fallback=a.typology_fallback)
    if "skipped" in stats:
        print(f"[span_extension] {a.iso}: {stats['skipped']}", file=sys.stderr)
        return 0
    n_pairs = sum(len(rec["pairs"]) for recs in by_book.values() for rec in recs)
    for book, recs in by_book.items():
        recs.sort(key=lambda r: (r["chapter"], r["verse"]))
        dest = a.out / f"align_spanext_{a.iso}_{book}.jsonl"
        with dest.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[span_extension] {a.iso}: {dict(stats)} · {n_pairs} pair(s) widened across {len(by_book)} "
         f"book(s) → align_spanext_{a.iso}_<BOOK>.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
