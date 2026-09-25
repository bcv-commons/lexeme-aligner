"""Roadmap item D0 (internal-docs/aim1-typology-source-structure-plan.md §4R, 2026-09-25) — persist
the per-language statistics the base chain already computes from its OWN `align_eflomal_*`(+`gloss`)
rows into `config/gram_struct/derived_input/<iso>.json`, which `gram_struct.py --build`'s `derived/`
partition folds in alongside `config/constituent_order/<iso>.json` (which this module ALSO refreshes,
via `constituent_order.profile()`, so the 57 languages that partition already covers only grow, never
shrink). One streaming pass per language: build the corpus once, everything below reads from that.

CIRCULARITY RULE (plan principle 8): every statistic here comes ONLY from `eflomal`(+`gloss`) rows —
never `spanext`/`gapfill`/`residual`/`llm` output, so a mechanism that later CONSUMES a derived fact
(fertility_priors, span_extension) is never validated against data it helped produce.

QUALITY GATE (plan's own wording, applied before deriving anything for a language): eflomal+gloss
content coverage >= 0.80 (computed here, not stored anywhere else — see below for why "eflomal+gloss"
rather than eflomal alone), `hi_conf_ge_0.9 / rows` >= 0.40 (computed here from eflomal's own per-pair
`score >= 0.9` share — see the CORRECTED note below, not the published `lexeme-alignments` manifest's
own `hi_conf_ge_0.9`/`rows` fields, which measure a DIFFERENT thing), >= 3,000 aligned verses, gold health
>= 0.6 where a gold exists (read from `gold_to_fullalign.py`'s own already-computed, already-persisted
`positional` health in `publish/full-align/manifest.json` / `pipeline/work/full-align/manifest.json` —
NOT recomputed here, per the "reuse, don't reimplement" instruction). A language failing any of these
gets `derived_input/<iso>.json` = `{"_derived_meta": {"reason": "alignment_quality", "gate": {...}}}`
only — no slots, no audit facts; a language that passes still gets the full gate dict recorded (with
`passed: true`) so the numbers behind every derived fact are auditable, not just the verdict.

DEVIATION from the plan's literal wording, noted rather than silently resolved: "eflomal content
coverage" is computed here from the SAME `eflomal+gloss` union `load_covered()` scan used for every
other statistic in this pass (one `anchors` dict, reused everywhere) rather than an eflomal-ONLY
re-scan — cheaper (no second pass) and consistent with the circularity rule's own "eflomal(+gloss)"
framing, which treats the two as one tier. Recorded explicitly as `coverage_methods: "eflomal+gloss"`
in the gate dict so this choice is auditable, not hidden in a number.

CORRECTED (found while smoke-testing on hin): the plan's own design report pointed at
`publish/lexeme-alignments/manifest.json`'s per-language `hi_conf_ge_0.9`/`rows` fields for the second
gate check. Those fields measure a TYPE-level lexicon dominance share (the `lexeme-alignments`
schema's own `hi_conf` column, aggregated across eflomal+gloss+gapfill rows) — NOT a per-occurrence
alignment-quality signal, and it reads far too low for even our best-measured gold languages to be the
intended bar (hin 0.244, eng 0.208, fra 0.252, spa 0.262, cmn 0.248 — all would FAIL a 0.40 threshold
under this reading, which cannot be the plan's intent given its own stated gain of "181 OT languages").
The right signal, matching `run_pilot._hi()`'s own definition of "hi-conf" for eflomal specifically
(`method == "eflomal" and score >= 0.9`, the intersection-backed core), is computed directly here from
the language's own `align_eflomal_<tag>_*.jsonl` — hin reads 0.793 under this definition, consistent
with what a well-measured gold language should score. This is a genuine spec-vs-real-data mismatch, not
a design choice overridden quietly: the manifest field the design report named exists and is real, it
simply measures something else than the plan's prose implied.

SLOTS WRITTEN (OT-only, per D0's own scope — Greek-side derivation is D1, a separate later item):
  possessor     — from `gapfill.compute_order_stats()`'s `rec_after_rate` (GB065's own question: does
                  the construct-governed dependent/possessor come AFTER its head in the target?).
  subject_verb  — from the `("Pred", "Subj")` cross-phrase pair in the SAME function's `func_order`.
  object_verb   — from the `("Pred", "Objc")` pair, same function.
  Direction convention matches `typology.py`'s SLOTS exactly (see that module's own docstring): a HIGH
  rate on `("Pred", "Subj")`/`("Pred", "Objc")` means the target PRESERVES Hebrew's own (usually
  verb-first) source order, i.e. Pred (verb) precedes Subj/Objc in the rendering too — that is
  "after" (subject/object AFTER the verb) in typology.py's before/after-the-verb convention; a LOW
  rate means the target flips to Subj/Objc-before-Pred, i.e. "before".
  Every slot is WRITTEN once its underlying n clears the SAME gold-anchored confidence gate
  `compute_order_stats`'s own caller (`gapfill.main()`) uses (n>=50 for possessor, n>=100 per func
  pair, |rate-0.5|>=0.20) — confident results get a real direction; results that ran but stayed inside
  that margin get `direction: null, reason: "mixed"`; a pair with too little data (or a testament with
  no OT content at all) gets NO KEY at all (absence = unknown, not attempted) — per docs/architecture.md
  §2's null-vs-absent contract.

AUDIT FACTS (never slots — `gram_struct.py`'s merge would reject a partition writing a slot key another
partition also writes, and these are diagnostics for a human, not typology facts a mechanism reads):
  derived.audit.multiword_rates       — analyze_language.multiword_rates() (any testament).
  derived.audit.diagnose_block_rates  — span_extension.diagnose()'s block_rates, ONLY attempted when a
                                        Grambank or typology-table entry exists for the language (that
                                        module's own early-return gate makes this cheap to attempt
                                        otherwise, but the full corpus rebuild it performs when
                                        coverage DOES exist is real cost — see the module docstring);
                                        any exception is caught and the key omitted, never fails the build.
  derived.constituent_order           — the FULL `constituent_order.profile()` output for the SAME
                                        edition (pair_order_kept + function_drift), also written
                                        verbatim to `config/constituent_order/<iso>.json` (refreshing/
                                        creating that file — the canonical location `gram_struct.py`'s
                                        `build_derived()` already reads, so no reader needs to change).

    python3 -m lexeme_aligner.derive_typology --build                 # published languages (compact-alignments manifest)
    python3 -m lexeme_aligner.derive_typology --build --iso hin       # one language
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.analyze_language import multiword_rates
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.constituent_order import profile as constituent_profile
from lexeme_aligner.gapfill import compute_order_stats, load_covered, load_priors
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, build_corpus
from lexeme_aligner.versification import remapper

_COMPACT_MANIFEST = Path("publish/compact-alignments/manifest.json")
_LEXEME_MANIFEST = Path("publish/lexeme-alignments/manifest.json")
_FULLALIGN_MANIFEST = Path("publish/full-align/manifest.json")
_FULLALIGN_INTERNAL_MANIFEST = Path("pipeline/work/full-align/manifest.json")
_CONSTITUENT_DIR = Path("config/constituent_order")
OUT_DIR = Path("config/gram_struct/derived_input")

MIN_COVERAGE = 0.80
MIN_HI_CONF_SHARE = 0.40
MIN_ALIGNED_VERSES = 3000
MIN_GOLD_HEALTH = 0.6
_PHRASE_CONFIDENCE_MARGIN = 0.20   # same gold-anchored gate as gapfill.py's own (see that module)


def _load_json(fp: Path) -> dict:
    return json.loads(Path(fp).read_text(encoding="utf-8")) if Path(fp).exists() else {}


def _content_sha256(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def primary_edition(iso: str, compact_manifest: dict) -> tuple[str, str, list[str]] | None:
    """(tag, edition_code, books) for the edition with the most OT-book coverage (ties broken by total
    book count), or the edition with the most books at all if none has any OT book (pure NT-only
    language) — always returns SOME edition tag when the language has one, since the quality gate
    needs a `usj_dir` even for NT-only languages."""
    editions = compact_manifest.get(iso, {}).get("editions", {})
    if not editions:
        return None
    def key(item):
        _ecode, e = item
        books = e.get("books") or []
        return (len([b for b in books if b in OT_BOOKS]), len(books))
    ecode, e = max(editions.items(), key=key)
    return e["tag"], ecode, (e.get("books") or [])


def gold_health_for(iso: str, fullalign_manifests: list[dict]) -> float | None:
    """Max `positional` gold health across every manual partition for this iso, across the published
    and vendor-only full-align manifests — `gold_to_fullalign.py`'s own already-computed, already-
    persisted number (this module never recomputes gold health)."""
    best = None
    for m in fullalign_manifests:
        e = m.get("languages", {}).get(iso, {})
        for ed in e.get("editions", {}).values():
            for part in ed.get("layers", {}).get("manual", {}).values():
                h = part.get("health")
                if isinstance(h, dict) and isinstance(h.get("positional"), (int, float)):
                    best = h["positional"] if best is None else max(best, h["positional"])
    return best


def _eflomal_hi_conf_share(out_dir: Path, tag: str) -> float:
    """Share of eflomal's own content pairs with `score >= 0.9` — the intersection-backed core, the
    same definition `run_pilot._hi()` uses for eflomal specifically. See the module docstring's
    CORRECTED note for why this replaces the published manifest's `hi_conf_ge_0.9`/`rows` fields."""
    total = hi = 0
    for fp in tag_files(out_dir, "eflomal", tag):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("content"):
                        total += 1
                        if (p.get("score") or 0) >= 0.9:
                            hi += 1
    return (hi / total) if total else 0.0


def quality_gate(iso: str, tag: str, all_books: list[str], usj_dir: Path, out_dir: Path,
                 lex_pos: dict, fullalign_manifests: list[dict]) -> tuple[dict, dict, list]:
    """(gate_dict, anchors, recs_all) — `anchors` is the eflomal+gloss `load_covered()` output (already
    spans BOTH testaments, since `load_covered` scans the align jsonl directly rather than being
    book-scoped); `recs_all` is the built corpus over `all_books`, returned so D1's Greek-first slots
    (which need both testaments) don't force a second `build_corpus` pass when the gate passes."""
    heb = HebrewSource()
    recs_all = build_corpus(all_books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    heb_content = sum(1 for r in recs_all for t in r.heb if t.is_content and t.strong)
    _covered_h, _taken_t, anchors, _strong_surf, _target_pos = load_covered(
        tag, out_dir, ("eflomal", "gloss"), 0.0, lex_pos)
    aligned_content = sum(len(v) for v in anchors.values())
    aligned_verses = sum(1 for v in anchors.values() if v)
    coverage = (aligned_content / heb_content) if heb_content else 0.0

    hi_conf_share = _eflomal_hi_conf_share(out_dir, tag)

    health = gold_health_for(iso, fullalign_manifests)

    gate = {"coverage": round(coverage, 4), "coverage_methods": "eflomal+gloss",
           "heb_content_tokens": heb_content, "aligned_verses": aligned_verses,
           "hi_conf_share": round(hi_conf_share, 4), "gold_health": health}
    failed = (coverage < MIN_COVERAGE or aligned_verses < MIN_ALIGNED_VERSES
             or hi_conf_share < MIN_HI_CONF_SHARE
             or (health is not None and health < MIN_GOLD_HEALTH))
    gate["passed"] = not failed
    return gate, anchors, recs_all


def _order_slot(rate: float | None, n: int, min_n: int) -> dict | None:
    """None (omit the key) if n is below the gate's own minimum; else a written fact — a real
    direction if confident, else `direction: null, reason: "mixed"`. Matches docs/architecture.md
    §2's null-vs-absent contract: absence means "not attempted", null-with-reason means "attempted,
    inconclusive"."""
    if n < min_n or rate is None:
        return None
    if abs(rate - 0.5) < _PHRASE_CONFIDENCE_MARGIN:
        return {"direction": None, "source": "derived", "n": n, "rate": round(rate, 4), "reason": "mixed"}
    return {"direction": "after" if rate >= 0.5 else "before", "source": "derived", "n": n,
           "rate": round(rate, 4)}


# --- D1 (2026-09-25): Greek-first cheap slots (plan §4R "D1"; design internal-docs/review-2026-09-25/
# derived_typology.md §2.1/2.2/2.7/2.8). Unlike D0's Hebrew-only possessor/subject_verb/object_verb pass
# (OT only, 62->301 languages), these four run over BOTH testaments so they reach any language with a
# passing NT alignment — the ~1,590-language, 416-of-501 gain the plan calls out, since 1,211 of 1,629
# published languages are NT-only and every Hebrew-only signal in this module was previously unreachable
# for them. Selectors verified against real corpus data (not assumed from memory) before being hardcoded
# below: Greek Strong's/lemma values were spot-checked on engbsb/MAT (see this session's own log), Hebrew
# preposition/negation lemma values on hinirv/GEN+the first 5 OT books.
#
# SIMPLIFICATIONS, stated rather than silently made (time-boxed scope for this pass):
#   - article_word's own spec (§2.2) additionally requires the aligned target token be among the
#     language's top-20 target-stopwords (to exclude demonstratives inflating the existence rate).
#     NOT implemented here (would need a StopwordFilter load per language, a second real cost) — the
#     structural "own separate token, disjoint from the noun" signal is kept; the stopword refinement is
#     a documented follow-up, not silently dropped.
#   - `possessive_word`'s target-noun check and `negation`'s target-finite-verb check use the SAME
#     `_next_content` adjacency helper as adposition/article_word (a general "next content token", not a
#     POS-specific one) plus a light-weight kind check (`_is_finite_verb` for negation only) — not a full
#     prior-pack POS lookup. This matches the doc's own selectors closely (they define the target the
#     same adjacency way) but is worth knowing if a language's negation target check ever needs
#     tightening.
#   - `validate_derived` is wired to a genuine external reference ONLY for `adposition` (direct reuse of
#     `typology.grambank_direction`, GB074/075 — the exact same question). `article_word`, `possessive_word`
#     and `negation` have NO reliably-wired external validation source in this codebase yet (negation
#     direction's own doc-cited reference is WALS 143A/143E/144A, none of which are parsed by
#     `typology.py` today; article_word/possessive_word would need lang2vec's word/affix pair, which
#     `typology.py` currently only exposes for the 5 SLOTS it already tracks, not these 3 new ones). Per
#     the plan's own de-risking rule ("a selector bug, not a limit of the method" only applies to a
#     MEASURED disagreement) these three ship as `experimental: true` UNCONDITIONALLY, with
#     `validation: "not yet wired"` recorded on every value, rather than fabricating a ≥90%/85-90% verdict
#     with no real comparison behind it. Wiring WALS 143A + the lang2vec word/affix pairs for these three
#     is the natural next-session follow-up, not attempted here.

_GREEK_PREPOSITIONS = {"G1722", "G1519", "G1537", "G4314", "G1909", "G1223", "G0575", "G3326", "G4012",
                       "G5259", "G5228", "G2596", "G3844", "G4862", "G1799", "G0891", "G2193", "G4253"}
# Hebrew: MACULA gives the inseparable prefix prepositions (ב/ל) their own AUGMENTED lexeme ids (not a
# plain Strong's match) — verified live: hbo:0871a=בְּ (H0871), hbo:3807a=לְ (H3807). Free-standing
# prepositions use their ordinary Strong's/lemma — verified: מִן=H4480, עַל=H5921, אֶל=H0413, עִם=H5973.
# כְּ/תַּחַת were not found in the verified sample (rare or a further augmented form not checked) —
# their omission narrows recall slightly, never introduces a wrong signal (n_min gates the rest).
_HEBREW_PREP_LEXEMES = {"hbo:0871a", "hbo:3807a"}
_HEBREW_PREP_LEMMAS = {"מִן", "עַל", "אֶל", "עִם"}
_GREEK_ARTICLE_STRONG = "G3588"                        # ὁ — verified engbsb/MAT
_HEBREW_ARTICLE_LEXEME = "hbo:1886a"                   # span_extension._DEFINITE_ARTICLE_LEXEME, reused
_GREEK_NEGATION = {"G3756", "G3361"}                   # οὐ (covers οὐκ/οὐχ, one lexeme) + μή — verified
_HEBREW_NEGATION_LEMMAS = {"לֹא", "אַל"}                # verified hinirv OT sample
_GREEK_GEN_PRONOUN_LEMMAS = {"ἐγώ", "σύ", "αὐτός", "ἡμεῖς", "ὑμεῖς"}   # verified engbsb/MAT genitive rollups

_D1_MIN_N = {"adposition": 200, "article_word": 300, "possessive_word": 300, "negation": 300}


def _is_prep(t) -> bool:
    return t.strong in _GREEK_PREPOSITIONS or t.lexeme in _HEBREW_PREP_LEXEMES or t.lemma in _HEBREW_PREP_LEMMAS


def _is_article(t) -> bool:
    return t.strong == _GREEK_ARTICLE_STRONG or t.lexeme == _HEBREW_ARTICLE_LEXEME


def _is_negator(t) -> bool:
    return t.strong in _GREEK_NEGATION or t.lemma in _HEBREW_NEGATION_LEMMAS


def _is_gen_pronoun(t) -> bool:
    return t.case_ == "genitive" and t.lemma in _GREEK_GEN_PRONOUN_LEMMAS


def _is_finite_verb(t) -> bool:
    return bool(t.person) or (bool(t.mood) and t.mood not in ("participle", "infinitive"))


def _next_content(members: list, i: int):
    """First content token after `members[i]` in spine order, skipping at most one intervening article
    (so an article sitting between a preposition/pronoun and its noun doesn't block adjacency); any OTHER
    intervening function word breaks true adjacency and returns None."""
    skipped_article = False
    for j in range(i + 1, len(members)):
        t = members[j]
        if t.is_content:
            return t
        if _is_article(t) and not skipped_article:
            skipped_article = True
            continue
        return None
    return None


def full_anchors(out_dir: Path, tag: str, methods=("eflomal", "gloss")) -> dict[int, dict[int, int]]:
    """{ref: {h_idx: first target position}} for EVERY aligned pair, content AND non-content — a real
    bug found and fixed while smoke-testing D1 on real data: `gapfill.load_covered`'s own `anchors`
    (what D0's possessor/subject_verb/object_verb slots read) deliberately DROPS every non-content pair
    (`if not p.get("content"): continue`, gapfill.py's own scan) because it exists to support GAP-FILLING
    content tokens, not to answer "where did the preposition/article/negator itself land". D1's four
    slots are ALL about a non-content marker's own target position, so they need a from-scratch scan of
    the same jsonl that keeps those pairs. First-method-wins on a repeated h_idx (same convention as
    every other multi-method scan in this codebase — `tag_files`'s own method-order)."""
    out: dict[int, dict[int, int]] = {}
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    verse = out.setdefault(ref, {})
                    for p in rec["pairs"]:
                        ti = p.get("t_idx") or []
                        if ti and p["h_idx"] not in verse:
                            verse[p["h_idx"]] = ti[0]
    return out


def _adjacency_stat(recs, anchors: dict, is_marker, target_ok=None, require_stopword=None) -> dict:
    """Generic 2.1/2.2/2.7/2.8 statistic: for every source token matching `is_marker`, find the next
    content token via `_next_content` (optionally gated by `target_ok`), then compare their base-chain
    anchor positions. `anchors[ref]` is `{h_idx: single target position}` (`derive_typology.full_anchors`'s
    shape — a plain first-target-position map covering non-content pairs too, unlike `gapfill.
    load_covered`'s own content-only `anchors`).

    `require_stopword` (a `target_stopwords.StopwordFilter`, optional): the marker's OWN rendered word
    must be a genuine function word in the target language — `article_word`'s literal §2.2 selector
    ("aligned to its own target token, disjoint from the noun's span, and that token is in the
    language's target-stopwords list"), applied generically to any existence check that needs it. Found
    NECESSARY, not optional, while smoke-testing on real data: without it, hin's `article_word` read
    present=true at 76% even though Hindi has no articles at all (GB020=0, independently established
    all session) — Greek ὁ used demonstrative-style before a proper name renders as Hindi's own
    demonstrative/deictic particle often enough to look like a free article by pure adjacency. Requiring
    stopword membership is `article_word`'s BASE selector text, not the doc's further top-20-frequency
    refinement (not implemented — a residual gap, noted where this is called)."""
    n_before = n_after = n_fused = n_opportunities = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch:
            continue
        members = r.heb
        for i, t in enumerate(members):
            if not is_marker(t):
                continue
            nxt = _next_content(members, i)
            if nxt is None or (target_ok and not target_ok(nxt)):
                continue
            n_opportunities += 1
            pa, pb = anch.get(t.idx), anch.get(nxt.idx)
            if pa is None or pb is None:
                continue
            if pa == pb:
                n_fused += 1
                continue
            if require_stopword is not None:
                word = r.toks[pa] if 0 <= pa < len(r.toks) else None
                if not require_stopword.is_function(word):
                    continue          # marker DID get its own token, but it isn't a genuine function word
            if pa < pb:
                n_before += 1
            else:
                n_after += 1
    n_resolved = n_before + n_after
    return {"n_opportunities": n_opportunities, "n_fused": n_fused, "n_before": n_before,
           "n_after": n_after, "n_resolved": n_resolved,
           "rate_after": (n_after / n_resolved) if n_resolved else None,
           "word_rate": ((n_before + n_after) / n_opportunities) if n_opportunities else None}


def _direction_slot(stat: dict, min_n: int, experimental: bool = False) -> dict | None:
    """`after >= 0.70 / before <= 0.30 / else null:mixed` (derived_typology.md §2.1's own bands — a
    DIFFERENT threshold convention than D0's `_order_slot`, which uses a |rate-0.5|>=0.20 margin;
    kept distinct per-slot as the design doc specifies rather than unified for tidiness)."""
    if stat["n_resolved"] < min_n or stat["rate_after"] is None:
        return None
    ra = stat["rate_after"]
    direction = "after" if ra >= 0.70 else "before" if ra <= 0.30 else None
    out = {"direction": direction, "source": "derived", "n": stat["n_resolved"],
          "n_fused": stat["n_fused"], "rate_after": round(ra, 4)}
    if direction is None:
        out["reason"] = "mixed"
    if experimental:
        out["experimental"] = True
        out["validation"] = "not yet wired"
    return out


def _word_slot(stat: dict, min_n: int, present_ge: float = 0.35, absent_le: float = 0.10,
              experimental: bool = True) -> dict | None:
    """Existence-only fact (`article_word`/`possessive_word`): `present=true` if the word-rate clears
    `present_ge`, `false` if at/below `absent_le`, else omitted (mid-band = genuinely ambiguous, not a
    fact worth publishing at this pass's confidence)."""
    if stat["n_opportunities"] < min_n or stat["word_rate"] is None:
        return None
    wr = stat["word_rate"]
    if wr >= present_ge:
        present = True
    elif wr <= absent_le:
        present = False
    else:
        return None
    out = {"present": present, "source": "derived", "rate": round(wr, 4), "n": stat["n_opportunities"]}
    if experimental:
        out["experimental"] = True
        out["validation"] = "not yet wired"
    return out


def derive_d1_slots(recs, anchors: dict, stopwords=None, include_unvalidated: bool = False) -> dict:
    """{"adposition": ..., "adposition_word": ..., ...} — only the keys whose n cleared its own min_n
    are present (absence = not attempted, matching D0's own null-vs-absent contract).

    KNOWN-ANSWER CHECK RESULT (2026-09-25, real data, eng/hin/arb NT sample): `adposition` +
    `adposition_word` are correct on all three (eng "before"/.0236, hin "after"/.893 — Hindi IS
    postpositional, arb "before"/.0068) and `adposition` is the one slot with a real external
    validation wired (`validate_derived`, direct Grambank GB074/075 reuse) — SHIPPED.

    `article_word`/`possessive_word`/`negation`/`negation_word` FAILED their own known-answer check:
    hin (GB020=0, independently established this whole session — Hindi has no articles at all) read
    `article_word: present=true` at 76%, and adding the doc's own stopword-membership requirement
    (`require_stopword`) only reduced it to 67% — nowhere near flipping the verdict. Root cause (not
    fixed in this pass): Greek ὁ precedes a proper NAME extremely often in the NT (a distinct,
    extremely common construction from "the noun"), and is itself extremely frequent (2,754 occurrences
    in 4 books alone) — plausibly enough raw co-occurrence volume that eflomal/gloss's own alignment
    noise around proper nouns (a known, independently-documented weak point — "a specific proper NAME
    may occur only a handful of times, so the model has far less evidence") produces a spurious
    "own separate token" signal against SOME frequent target stopword often enough to look like a real
    article, in a language that has none. A genuine fix needs either the doc's own further top-20-
    frequency refinement (not just membership) or excluding ὁ+NAME occurrences from the selector
    entirely (2.2's selector doesn't currently distinguish common-noun heads from proper-name heads).
    Per this plan's own de-risking rule (a known-answer failure means the selector is wrong, not the
    method's limit — fix before shipping, don't paper over it with an `experimental` flag), these three
    are WITHHELD from the real build by default (`include_unvalidated=False`) rather than published
    known-wrong under a caveat. `include_unvalidated=True` exists for follow-up debugging only."""
    out: dict = {}

    prep = _adjacency_stat(recs, anchors, _is_prep, target_ok=lambda n: n.is_content)
    d = _direction_slot(prep, _D1_MIN_N["adposition"])
    if d is not None:
        out["adposition"] = d
    if prep["n_opportunities"] >= _D1_MIN_N["adposition"] and prep["word_rate"] is not None:
        out["adposition_word"] = {"present": prep["word_rate"] >= 0.35, "source": "derived",
                                  "rate": round(prep["word_rate"], 4), "n": prep["n_opportunities"]}

    if not include_unvalidated:
        return out

    art = _adjacency_stat(recs, anchors, _is_article, target_ok=lambda n: n.is_content,
                          require_stopword=stopwords)
    aw = _word_slot(art, _D1_MIN_N["article_word"])
    if aw is not None:
        if aw["present"]:
            ad = _direction_slot(art, _D1_MIN_N["article_word"], experimental=True)
            if ad is not None and ad.get("direction"):
                aw["direction"] = ad["direction"]
        out["article_word"] = aw

    poss = _adjacency_stat(recs, anchors, _is_gen_pronoun, target_ok=lambda n: n.is_content)
    pw = _word_slot(poss, _D1_MIN_N["possessive_word"])
    if pw is not None:
        out["possessive_word"] = pw

    neg = _adjacency_stat(recs, anchors, _is_negator, target_ok=_is_finite_verb)
    nd = _direction_slot(neg, _D1_MIN_N["negation"], experimental=True)
    if nd is not None:
        out["negation"] = nd
    if neg["n_opportunities"] >= _D1_MIN_N["negation"] and neg["word_rate"] is not None:
        out["negation_word"] = {"present": neg["word_rate"] >= 0.35, "source": "derived",
                                "rate": round(neg["word_rate"], 4), "n": neg["n_opportunities"],
                                "experimental": True, "validation": "not yet wired"}
    return out


def validate_derived(slot: str, derived_docs: dict[str, dict], seed: int = 0) -> dict:
    """Generalizes `typology.validate_agreement` (§4.2): for `slot`, compare every language's freshly
    DERIVED direction (from `derived_docs`, already computed — this never re-derives) against Grambank's
    OWN direction for the SAME slot (`typology.grambank_direction`), on languages where both resolve.
    Splits that reference set in half (seeded, deterministic) — calibrate on one half, report the
    held-out agreement on the other — per the doc's "leave-one-out is not needed ... what must be held
    out is the threshold calibration" rule. ONLY `adposition` has a direct Grambank counterpart wired
    (GB074/075 is exactly the same question this slot derives); calling this for any other slot raises,
    rather than silently returning a meaningless comparison."""
    if slot != "adposition":
        raise ValueError(f"validate_derived({slot!r}): no external reference wired for this slot yet "
                         "(see this module's own D1 docstring) — only 'adposition' is supported")
    from lexeme_aligner.typology import grambank_direction
    grambank_all = _load_json(Path("config/grambank/features.json")).get("languages", {})

    pairs = []
    for iso, doc in derived_docs.items():
        dslot = doc.get(slot)
        if not dslot or not dslot.get("direction"):
            continue
        gdir = grambank_direction(grambank_all.get(iso), slot)
        if gdir is None:
            continue
        pairs.append((iso, dslot["direction"], gdir))

    rng = random.Random(seed)
    shuffled = pairs[:]
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    calib, held_out = shuffled[:half], shuffled[half:]

    def agreement(rows):
        agree = sum(1 for _, d, g in rows if d == g)
        return {"agree": agree, "compared": len(rows),
               "rate": round(agree / len(rows), 4) if rows else None}

    return {"slot": slot, "reference_total": len(pairs),
           "calibration_half": agreement(calib), "held_out_half": agreement(held_out),
           "overall": agreement(pairs)}


def resolve_tag(tag: str) -> tuple[str, Path] | None:
    """(canonical_tag, usj_dir) — tolerant of a real, found-live data-quality wrinkle:
    `publish/compact-alignments/manifest.json`'s per-edition `tag` field is lowercase for most
    editions but UPPERCASE for a few (e.g. spa's `SPAERV`, por's `PORB09` — matching that edition's
    own code exactly rather than the lowercased directory-safe form `_tag()` normally produces), while
    both the ingest-cache directory AND the `align_eflomal_<tag>_*.jsonl` files on disk are lowercase.
    Silently building an empty corpus (usj_dir missing) or scanning zero jsonl (case-sensitive glob
    miss) would each have looked like a real `alignment_quality` gate failure (coverage/aligned_verses
    both 0) instead of a path-casing bug — caught by hand-checking spa/por in the first real sample
    run, both of which failed the gate this way before this fix. The CANONICAL tag (used for every
    downstream `tag_files`/`load_covered`/`constituent_profile`/`multiword_rates` call, not just the
    usj_dir lookup) is whichever casing actually has a real `usj-<tag>` directory on disk — trying the
    tag as given, then lowercased, then uppercased. Returns None if none of the three exists."""
    for candidate in (tag, tag.lower(), tag.upper()):
        p = Path(f"pipeline/work/ingest-cache/usj-{candidate}")
        if p.exists():
            return candidate, p
    return None


def derive_one(iso: str, tag: str, ot_books: list[str], all_books: list[str], out_dir: Path = OUT,
               with_diagnose: bool = True) -> dict:
    resolved = resolve_tag(tag)
    if resolved is None:
        return {"_derived_meta": {"reason": "no_ingest_cache", "gate": {"tag": tag}}}
    tag, usj_dir = resolved   # rebind to the canonical, on-disk-verified casing for every call below
    lex_pos, _ = load_priors(PRIOR_PACK)
    fullalign_manifests = [_load_json(_FULLALIGN_MANIFEST), _load_json(_FULLALIGN_INTERNAL_MANIFEST)]

    gate, anchors, recs_all = quality_gate(iso, tag, all_books, usj_dir, out_dir, lex_pos,
                                           fullalign_manifests)
    if not gate["passed"]:
        return {"_derived_meta": {"reason": "alignment_quality", "gate": gate}}

    doc: dict = {"_derived_meta": {"reason": None, "gate": gate}}

    # D1: Greek-first slots — both testaments, so this reaches NT-only languages D0's OT-only pass
    # never could. Uses `full_anchors` (see its own docstring), NOT the `anchors` from `quality_gate`
    # above — that one is content-only and would silently zero out every D1 slot (all four markers are
    # non-content by definition).
    d1_anchors = full_anchors(out_dir, tag)
    from lexeme_aligner.target_stopwords import StopwordFilter
    stopwords = StopwordFilter(iso, str(usj_dir))
    doc.update(derive_d1_slots(recs_all, d1_anchors, stopwords=stopwords))

    if ot_books:
        heb = HebrewSource()
        recs_ot = build_corpus(ot_books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
        stats = compute_order_stats(recs_ot, anchors)

        possessor = _order_slot(stats["rec_after_rate"], stats["rec_after_n"], min_n=50)
        if possessor is not None:
            doc["possessor"] = possessor

        for pair, slot_name in ((("Pred", "Subj"), "subject_verb"), (("Pred", "Objc"), "object_verb")):
            n = stats["func_order_n"].get(pair, 0)
            rate = stats["func_order"].get(pair)
            slot = _order_slot(rate, n, min_n=100)
            if slot is not None:
                doc[slot_name] = slot

        prof = constituent_profile(tag, usj_dir, out_dir, methods=("eflomal", "gloss"))
        _CONSTITUENT_DIR.mkdir(parents=True, exist_ok=True)
        (_CONSTITUENT_DIR / f"{iso}.json").write_text(
            json.dumps(prof, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        doc.setdefault("audit", {})["constituent_order"] = {
            "source": "derived",
            **{k: prof[k] for k in ("verses_measured", "pair_order_kept", "function_drift") if k in prof}}

    mw = multiword_rates(tag, out_dir, lex_pos, method="eflomal")
    if mw:
        doc.setdefault("audit", {})["multiword_rates"] = {
            pos: {"multi_word": mwn, "total": tot} for pos, (mwn, tot) in mw.items()}

    if with_diagnose:
        try:
            from lexeme_aligner.span_extension import diagnose
            books = all_books
            report = diagnose(tag, iso, usj_dir, books, out_dir)
            if report.get("block_rates"):
                doc.setdefault("audit", {})["diagnose_block_rates"] = report["block_rates"]
        except Exception as e:                        # never fail the whole build over a diagnostic
            print(f"[derive_typology] {iso}: diagnose() skipped ({e})", file=sys.stderr)

    return doc


def build(isos: list[str] | None = None, out_dir: Path = OUT_DIR, aligner_out: Path = OUT,
         with_diagnose: bool = True) -> dict:
    compact_manifest = _load_json(_COMPACT_MANIFEST).get("languages", {})
    if isos is None:
        isos = sorted(_load_json(_LEXEME_MANIFEST).get("languages", {}))
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {"total": 0, "no_edition": 0, "passed": 0, "gate_failed": 0,
            "failed_reasons": {}, "ot_slots_written": 0, "d1_slots_written": 0,
            "d1_slot_counts": {k: 0 for k in
                               ("adposition", "adposition_word", "article_word", "possessive_word",
                                "negation", "negation_word")}}
    for iso in isos:
        stats["total"] += 1
        ed = primary_edition(iso, compact_manifest)
        if ed is None:
            stats["no_edition"] += 1
            continue
        tag, _ecode, books = ed
        ot_books = [b for b in books if b in OT_BOOKS]
        try:
            doc = derive_one(iso, tag, ot_books, books, aligner_out, with_diagnose=with_diagnose)
        except Exception as e:
            doc = {"_derived_meta": {"reason": "alignment_quality", "gate": {"error": str(e)}}}
        if doc.get("_derived_meta", {}).get("reason") == "alignment_quality" and "possessor" not in doc:
            stats["gate_failed"] += 1
        else:
            stats["passed"] += 1
            if any(k in doc for k in ("possessor", "subject_verb", "object_verb")):
                stats["ot_slots_written"] += 1
            if any(k in doc for k in stats["d1_slot_counts"]):
                stats["d1_slots_written"] += 1
            for k in stats["d1_slot_counts"]:
                if k in doc:
                    stats["d1_slot_counts"][k] += 1
        doc["content_sha256"] = _content_sha256(doc)
        (out_dir / f"{iso}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--validate", metavar="SLOT", help="D1: report validate_derived(SLOT) against "
                    "config/gram_struct/derived_input/*.json already on disk (run --build first)")
    ap.add_argument("--iso", action="append", help="one or more isos; default: every published language")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--aligner-out", type=Path, default=OUT)
    ap.add_argument("--no-diagnose", action="store_true",
                    help="skip span_extension.diagnose() block-rate audit (cheap for Grambank-absent "
                         "languages, a real second corpus build for covered ones)")
    args = ap.parse_args()
    if args.validate:
        docs = {fp.stem: _load_json(fp) for fp in args.out.glob("*.json")}
        print(json.dumps(validate_derived(args.validate, docs), indent=1))
        return 0
    if not args.build:
        ap.error("pass --build or --validate SLOT")
    stats = build(args.iso, args.out, args.aligner_out, with_diagnose=not args.no_diagnose)
    print(f"[derive_typology] {stats['total']} language(s): {stats['passed']} passed the quality gate "
         f"({stats['ot_slots_written']} got >=1 OT slot, {stats['d1_slots_written']} got >=1 D1 slot: "
         f"{stats['d1_slot_counts']}), {stats['gate_failed']} gated on alignment_quality, "
         f"{stats['no_edition']} had no edition at all → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
