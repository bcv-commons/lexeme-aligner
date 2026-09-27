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
import csv
import functools
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
# D2-fix (2026-09-26): found while root-causing the 1 Cor 1:26 mispairing (see _CLAUSE_ROLE_MAX_DISTANCE's
# comment) — the actual clause-boundary marker there is NOT a verb at all (no participle/infinitive
# appears in the window either), it's the subordinating complementizer ὅτι (G3754, verified against the
# real token directly: strong=G3754, lemma=ὅτι) introducing the embedded clause the three predicate
# nominatives actually belong to. Greek subordinators/complementizers are a small, closed class (unlike
# the open-ended parsing problem a full dependency grammar would solve) — hardcoded here the same way
# `_GREEK_PREPOSITIONS` already is. NOT exhaustive (a real `frames`/dependency signal remains the actual
# fix, see internal-docs/bcv-query-wishlist.md's 2026-09-26 ask); this covers the common complementizer/
# conditional/temporal subordinators most likely to introduce an embedded clause with its own s/o role.
_GREEK_SUBORDINATORS = {"G3754",   # ὅτι  — that/because (verified: 1 Cor 1:26)
                        "G2443",   # ἵνα  — in order that/so that
                        "G1437",   # ἐάν  — if
                        "G1487",   # εἰ   — if
                        "G3752",   # ὅταν — whenever
                        "G2531",   # καθώς — just as
                        "G5613",   # ὡς   — as/that/when
                        "G5620"}   # ὥστε — so that
# D0-fix (2026-09-26): the Hebrew equivalents, for `_hebrew_clause_role_stat` below — verified against
# real spine tokens by Strong's number (lemma-text matching missed these due to a niqqud/normalization
# mismatch caught in the same check: `t.lemma == "אֲשֶׁר"` silently failed on real data, `t.strong ==
# "H0834"` did not — matching by Strong's, not raw lemma text, is the reliable approach here).
_HEBREW_SUBORDINATORS = {"H0834",   # אֲשֶׁר — relative "who/which/that", introduces a relative clause
                         "H3588",   # כִּי   — that/because/for/when
                         "H0518"}   # אִם    — if

_D1_MIN_N = {"adposition": 200, "article_word": 300, "possessive_word": 300, "negation": 300}
_D2_MIN_N = {"possessor": 300, "subject_verb": 300, "object_verb": 300}

_GRC_ROLE_TSV = Path("config/grc_frames/role.tsv")
_GRC_FRAMES_TSV = Path("config/grc_frames/frames.tsv")


@functools.lru_cache(maxsize=1)
def load_greek_frames(role_tsv: Path = _GRC_ROLE_TSV,
                      frames_tsv: Path = _GRC_FRAMES_TSV) -> dict[int, dict[int, set[int]]]:
    """D2-frames (2026-09-26): real Greek NT argument structure from bcv-query, delivered same-day in
    reply to the wishlist ask this bug motivated (see internal-docs/bcv-query-wishlist.md). Returns
    `{ref: {verb_idx: {arg_idx, ...}}}` — `ref` matches `refs.encode()`, `verb_idx`/`arg_idx` match our
    own spine's 0-indexed `idx` (role.tsv's `word` column is 1-indexed; verified against real tokens:
    1 Cor 1:1 idx=0/Παῦλος/nominative == word=1/nominative, idx=3/Χριστοῦ/genitive == word=4/genitive).

    Deliberately just a set of valid arg positions per verb, NOT split by PropBank role (A0/A1/A2) — the
    fix this supports (`_clause_role_stat`'s `frame_index` parameter) uses frames.tsv to answer "is this
    specific role="s"/"o" token actually THIS verb's own argument", not to relabel grammatical role from
    the thematic one. PropBank A0/A1 (agent/patient) don't map 1:1 onto syntactic subject/object under
    passive voice, so `role`'s own v/s/o tagging stays the source of grammatical role; frames.tsv only
    ever narrows/confirms which pairing is real, replacing the walk-and-guess heuristic where it can.

    Missing files degrade to `{}` (no frame data at all) rather than raising — this fix is additive on
    top of the existing distance/subordinator heuristic (`_clause_role_stat` falls back to it per-verb
    when a verb has no frame entry), never a hard dependency."""
    if not Path(role_tsv).exists() or not Path(frames_tsv).exists():
        return {}
    key_to_ref_idx: dict[str, tuple[int, int]] = {}
    with open(role_tsv, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            ref = encode(row["book"], int(row["chapter"]), int(row["verse"]))
            key_to_ref_idx[row["key"]] = (ref, int(row["word"]) - 1)
    frame_index: dict[int, dict[int, set[int]]] = {}
    with open(frames_tsv, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            verb_loc = key_to_ref_idx.get(row["verb_key"])
            arg_loc = key_to_ref_idx.get(row["arg_key"])
            if verb_loc is None or arg_loc is None or verb_loc[0] != arg_loc[0]:
                continue
            ref, verb_idx = verb_loc
            frame_index.setdefault(ref, {}).setdefault(verb_idx, set()).add(arg_loc[1])
    return frame_index


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


def _possessor_stat_greek(recs, anchors: dict) -> dict:
    """Roadmap D2, design doc §2.3 (Greek half — the Hebrew half already exists as `rec_after_rate`,
    read by `derive_one` below): an adjacent (H,G) or (G,H) content-token pair in SOURCE order, order-
    AGNOSTIC (Greek genitive constructions attach either "τοῦ Δαυὶδ οἶκος" or "οἶκος Δαυίδ"), where
    exactly one member has `case_=="genitive"` (the possessor, G) and the other does not (the head, H),
    at most one intervening article. Excludes genitive PRONOUNS (`_is_gen_pronoun`) — those are §2.7's
    `possessive_word`, a different slot, deliberately still withheld (D1 failed its own known-answer
    check for the pronoun-adjacent slots; this is the noun/name case only, unaffected by that failure).
    Measures P(pos(G) < pos(H)) in the TARGET — i.e. does the possessor's own rendering precede the
    head's — directly from adjacency, not order-kept relative to source (Greek itself is mixed on this,
    per the doc)."""
    n_before = n_after = n_fused = n_opportunities = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch:
            continue
        members = r.heb
        for i, t in enumerate(members):
            if not t.is_content or _is_gen_pronoun(t):
                continue
            nxt = _next_content(members, i)
            if nxt is None or not nxt.is_content or _is_gen_pronoun(nxt):
                continue
            t_gen, nxt_gen = t.case_ == "genitive", nxt.case_ == "genitive"
            if t_gen == nxt_gen:
                continue                                   # need exactly one genitive, one not
            g, h = (t, nxt) if t_gen else (nxt, t)
            n_opportunities += 1
            pg, ph = anch.get(g.idx), anch.get(h.idx)
            if pg is None or ph is None:
                continue
            if pg == ph:
                n_fused += 1
            elif pg < ph:
                n_before += 1                              # possessor(G) precedes head(H) in target
            else:
                n_after += 1
    n_resolved = n_before + n_after
    return {"n_opportunities": n_opportunities, "n_fused": n_fused, "n_before": n_before,
           "n_after": n_after, "n_resolved": n_resolved,
           "rate_after": (n_after / n_resolved) if n_resolved else None}


_CLAUSE_ROLE_MAX_DISTANCE = 15
# D2-fix (2026-09-26): evidence-based cap, not a real parse. Found via independent verification of
# D2's first sweep: 1 Corinthians 1:26 ("not many wise, not many powerful, not many well-born [were
# called]") has three predicate nominatives (role="s") that get wrongly attributed to "consider"
# (Βλέπετε), an unrelated OUTER clause verb — Greek elides the copula "were", so there is no finite
# verb token to stop the window-walk at the real clause boundary. A crude distance proxy (role token
# more than 15 source-tokens from its paired verb) isolated 85 of ~5,800 eng subject-role pairs in
# exactly this shape — a real, non-zero minority, not the whole story (most mispairings are surely
# closer than 15 tokens too), but a cheap, evidence-based guardrail while a real Greek dependency/
# clause-boundary signal remains unavailable (see internal-docs/bcv-query-wishlist.md's 2026-09-26
# ask). NOT a substitute for real parsing — see D2-frames in the roadmap plan.


def _clause_role_stat(recs, anchors: dict, target_role: str,
                      max_distance: int = _CLAUSE_ROLE_MAX_DISTANCE,
                      frame_index: dict[int, dict[int, set[int]]] | None = None) -> dict:
    """Roadmap D2, design doc §2.4 (Greek half — the Hebrew half already exists as `func_order`, read
    by `derive_one` below): for every finite verb (`role=="v"`, `_is_finite_verb`) in a verse, walk
    OUTWARD in both directions in spine order until hitting ANOTHER finite verb (or a verse boundary) —
    every `target_role` token ("s" or "o") found in that window pairs with this verb ("no other verb
    between them in source order", the doc's own phrasing). Measures P(pos(role token) < pos(verb)) in
    the TARGET directly, not order-kept (Greek's own order is free enough that direct measurement is
    the right question, per the doc).

    `max_distance` (D2-fix, see `_CLAUSE_ROLE_MAX_DISTANCE`'s own comment): the walk additionally stops
    after `max_distance` source-token steps even without hitting another finite verb — a real elided-
    copula clause boundary the "another finite verb" rule alone cannot see. A pair beyond the cap is
    dropped entirely (not counted as an opportunity at all), not merely excluded from `n_resolved`,
    since an over-distant pairing is wrong at the SELECTION stage, not just unresolved.

    The walk ALSO stops at a Greek subordinator/complementizer (`_GREEK_SUBORDINATORS`, D2-fix) — the
    ACTUAL boundary in the 1 Cor 1:26 case that motivated this fix (ὅτι at a distance of only 3-8 tokens
    from the outer verb, well inside any reasonable distance cap; the mispairing survives the distance
    cap alone and needs this check too, confirmed by direct testing).

    `frame_index` (D2-frames, 2026-09-26, `load_greek_frames()`'s output): when a verb HAS a real frame
    entry, this is AUTHORITATIVE and REPLACES the walk-and-guess heuristic for that verb entirely — only
    `target_role` tokens that are also one of the verb's real frame arguments count, no distance/
    subordinator guessing needed (verified directly against 1 Cor 1:26: the real frame for "consider"
    links only to "brothers"/"your calling", correctly excluding the three elided-clause predicate
    nominatives the OLD heuristic mispaired). A verb with NO frame entry (frames.tsv covers 25,493 of
    the NT's verb forms, not all) falls back to the heuristic below, unchanged — this is additive
    precision, not a replacement, since frame coverage is partial."""
    n_before = n_after = n_fused = n_opportunities = 0
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        anch = anchors.get(ref)
        if not anch:
            continue
        members = r.heb
        verse_frames = (frame_index or {}).get(ref, {})
        for vi, v in enumerate(members):
            if not (v.role == "v" and _is_finite_verb(v)):
                continue
            pv = anch.get(v.idx)
            if pv is None:
                continue
            verb_args = verse_frames.get(v.idx)
            if verb_args is not None:
                for m in members:
                    if m.role == target_role and m.idx in verb_args:
                        n_opportunities += 1
                        ps = anch.get(m.idx)
                        if ps is not None:
                            if ps == pv:
                                n_fused += 1
                            elif ps < pv:
                                n_before += 1
                            else:
                                n_after += 1
                continue
            for step in (-1, 1):
                j = vi + step
                while 0 <= j < len(members) and abs(j - vi) <= max_distance:
                    m = members[j]
                    if m.role == "v" and _is_finite_verb(m):
                        break
                    if m.strong in _GREEK_SUBORDINATORS:
                        break
                    if m.role == target_role:
                        n_opportunities += 1
                        ps = anch.get(m.idx)
                        if ps is not None:
                            if ps == pv:
                                n_fused += 1
                            elif ps < pv:
                                n_before += 1
                            else:
                                n_after += 1
                    j += step
    n_resolved = n_before + n_after
    return {"n_opportunities": n_opportunities, "n_fused": n_fused, "n_before": n_before,
           "n_after": n_after, "n_resolved": n_resolved,
           "rate_after": (n_after / n_resolved) if n_resolved else None}


def _hebrew_clause_role_stat(recs, anchors: dict, target_function: str,
                             max_distance: int = _CLAUSE_ROLE_MAX_DISTANCE) -> dict:
    """D0-fix (2026-09-26) — Hebrew-side analog of D2's `_clause_role_stat`, built to replace
    `gapfill.compute_order_stats`'s `func_order` as the source for `subject_verb`/`object_verb`
    specifically (NOT `possessor`, which keeps using `rec_after_rate` — that one is sound, see below).

    Root cause this fixes (found via the automated known-answer gate: Russian's subject_verb resolved
    confidently to "after", contradicting Russian's well-established dominant SVO order): `func_order`'s
    (Pred,Subj) statistic measures whether the target PRESERVES Hebrew's own source order, conditioned
    on Hebrew already having placed Pred before Subj in that instance. But Hebrew clause order is
    genuinely MIXED — narrative vav-consecutive clauses are frequently verb-initial (Pred-first), other
    clauses are subject-fronted — unlike Hebrew CONSTRUCT CHAINS, which really are consistently
    head-first (so `rec_after_rate`'s order-kept approach IS sound for `possessor`). A translation with
    a literal/source-hewing tradition can show a high "order preserved" rate on Hebrew's own verb-initial
    clauses while its OWN default word order is something else entirely — measuring literalism, not
    typology. This function instead pools BOTH Hebrew source orders and measures the target's ABSOLUTE
    position directly (mirroring D2's own Greek `_clause_role_stat` exactly, and the design doc's own
    stated preference for direct measurement over order-kept wherever the source order isn't itself
    reliably diagnostic).

    Deliberately a NEW, additive function, not a rewrite of `compute_order_stats` — that function is
    also read by `gapfill.main()`'s own LIVE production gap-filling, which this fix must not touch.

    Groups tokens by `phrase_id` (same BHSA grouping `compute_order_stats` uses), one averaged target
    position per phrase, sorted by source position. For every `Pred`-function phrase, walks outward in
    that SORTED PHRASE LIST until hitting another `Pred` phrase, a Hebrew subordinator/relative-marker
    token between the two phrases' own source positions (`_HEBREW_SUBORDINATORS` — the H0834/H3588/H0518
    class of embedded-clause marker, verified against real data the same way as the Greek fix), or
    `max_distance` phrases away. Every `target_function`-tagged phrase found in that window pairs with
    the Pred, scored directly (target position before/after), not order-relative-to-source."""
    n_before = n_after = n_fused = n_opportunities = 0
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
        subordinator_positions = sorted(t.idx for t in r.heb if t.strong in _HEBREW_SUBORDINATORS)
        phrases = sorted((src, fn, sum(tgts) / len(tgts)) for fn, src, tgts in by_phrase_fn.values())
        for pi, (src_p, fn_p, tp) in enumerate(phrases):
            if fn_p != "Pred":
                continue
            for step in (-1, 1):
                j = pi + step
                while 0 <= j < len(phrases) and abs(j - pi) <= max_distance:
                    src_m, fn_m, tm = phrases[j]
                    if fn_m == "Pred":
                        break
                    lo, hi = (src_p, src_m) if src_p < src_m else (src_m, src_p)
                    if any(lo < sp < hi for sp in subordinator_positions):
                        break
                    if fn_m == target_function:
                        n_opportunities += 1
                        if tm == tp:
                            n_fused += 1
                        elif tm < tp:
                            n_before += 1
                        else:
                            n_after += 1
                    j += step
    n_resolved = n_before + n_after
    return {"n_opportunities": n_opportunities, "n_fused": n_fused, "n_before": n_before,
           "n_after": n_after, "n_resolved": n_resolved,
           "rate_after": (n_after / n_resolved) if n_resolved else None}


def derive_d2_slots(recs_all, anchors: dict,
                    frame_index: dict[int, dict[int, set[int]]] | None = None) -> dict:
    """{"possessor": ..., "subject_verb": ..., "object_verb": ...} from Greek alone (NT), using
    `HebToken.role` (Greek-only, see that field's own docstring) — only present where `has_role`. Uses
    the SAME `_direction_slot` bands D1 uses (0.70/0.30), per the design doc's own §2.3/§2.4 threshold
    convention (a DIFFERENT convention from D0's Hebrew `_order_slot`, |rate-0.5|>=0.20 — the two are
    combined by `_combine_testaments` in `derive_one`, not unified into one threshold scheme).

    `frame_index` (D2-frames, `load_greek_frames()`'s output) is threaded through to `_clause_role_stat`
    for subject_verb/object_verb — see that function's own docstring for why real frame data, where
    available, replaces the walk-and-guess heuristic entirely rather than merely supplementing it."""
    out: dict = {}
    poss = _possessor_stat_greek(recs_all, anchors)
    d = _direction_slot(poss, _D2_MIN_N["possessor"])
    if d is not None:
        out["possessor"] = d
    for role, slot_name in (("s", "subject_verb"), ("o", "object_verb")):
        stat = _clause_role_stat(recs_all, anchors, role, frame_index=frame_index)
        d = _direction_slot(stat, _D2_MIN_N[slot_name])
        if d is not None:
            out[slot_name] = d
    return out


def _combine_testaments(heb_slot: dict | None, grc_slot: dict | None) -> dict | None:
    """Roadmap D2: combine D0's Hebrew-derived (OT) and D2's Greek-derived (NT) versions of the SAME
    slot (`possessor`/`subject_verb`/`object_verb`) for a dual-testament language. A slot dict whose
    own `direction` is `None` (already-recorded "mixed") is treated as unresolved for this comparison —
    only a real direction counts as a testament actually taking a position. If only one testament
    resolves, use it (tagged with which). If both resolve and AGREE, keep the higher-`n` one as the
    published fact but record both rates for audit. If both resolve and DISAGREE, per the design doc's
    own explicit instruction: emit `null` with `reason: "testament_conflict"` and both rates — never
    silently pick one."""
    heb_dir = heb_slot.get("direction") if heb_slot else None
    grc_dir = grc_slot.get("direction") if grc_slot else None
    if heb_dir is None and grc_dir is None:
        return heb_slot or grc_slot                        # neither resolved a real direction; pass through
    if heb_dir is None:
        out = dict(grc_slot); out["testament"] = "NT"; return out
    if grc_dir is None:
        out = dict(heb_slot); out["testament"] = "OT"; return out
    if heb_dir == grc_dir:
        primary = heb_slot if heb_slot.get("n", 0) >= grc_slot.get("n", 0) else grc_slot
        out = dict(primary)
        out["testament"] = "OT+NT"
        out["ot_rate"] = heb_slot.get("rate_after")
        out["nt_rate"] = grc_slot.get("rate_after")
        return out
    return {"direction": None, "source": "derived", "reason": "testament_conflict",
           "ot": {"direction": heb_dir, "rate_after": heb_slot.get("rate_after"), "n": heb_slot.get("n")},
           "nt": {"direction": grc_dir, "rate_after": grc_slot.get("rate_after"), "n": grc_slot.get("n")}}


def validate_derived(slot: str, derived_docs: dict[str, dict], seed: int = 0) -> dict:
    """Generalizes `typology.validate_agreement` (§4.2): for `slot`, compare every language's freshly
    DERIVED direction (from `derived_docs`, already computed — this never re-derives) against Grambank's
    OWN direction for the SAME slot (`typology.grambank_direction`), on languages where both resolve.
    Splits that reference set in half (seeded, deterministic) — calibrate on one half, report the
    held-out agreement on the other — per the doc's "leave-one-out is not needed ... what must be held
    out is the threshold calibration" rule. Roadmap D2 (2026-09-25) generalized this from D1's own
    adposition-only restriction: `typology.grambank_direction` already resolves `possessor`/
    `subject_verb`/`object_verb`/`article` generically (it always did — D1's restriction was a
    conservative choice for its own single shipped slot, not a real limitation of the reference
    function), so any of `typology.SLOTS` is now accepted. A slot outside that set still raises rather
    than silently returning a meaningless comparison."""
    from lexeme_aligner.typology import SLOTS, grambank_direction
    if slot not in SLOTS:
        raise ValueError(f"validate_derived({slot!r}): not a recognized gram-struct direction slot "
                         f"(expected one of {SLOTS})")
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


# D2-fix (2026-09-26): the automated replacement for what used to be a discretionary manual read.
# Independent verification found a real bug (see `_CLAUSE_ROLE_MAX_DISTANCE`'s comment) that a prior
# delegated agent's own known-answer check graded "PASSED" despite Russian resolving to a confidently
# WRONG direction (82% "subject after verb", contradicting Russian's well-established dominant SVO
# order) and English abstaining on a case that should be nearly unambiguous. This table is deliberately
# SMALL — only slot/language pairs where the descriptive-grammar literature agrees near-universally, so
# a genuinely flexible language (Spanish word order, Finnish case-driven scrambling) is never forced
# into a wrong hard assertion. `direction` uses the same "after" = role-token-after-verb /
# possessor-before-head convention `_direction_slot`/`_order_slot` already use throughout this module.
KNOWN_ANSWERS = {
    "subject_verb": {"eng": "before", "fra": "before", "rus": "before", "cmn": "before",
                     "hin": "before", "arb": "after"},
    "object_verb": {"eng": "after", "fra": "after", "rus": "after", "cmn": "after",
                    "hin": "before", "arb": "after"},
    "possessor": {"cmn": "before", "hin": "before", "arb": "after", "fra": "after"},
}


def check_known_answers(derived_docs: dict[str, dict]) -> dict:
    """Hard, automated gate — NOT a discretionary read. For every (slot, iso) in `KNOWN_ANSWERS`,
    compares the language's ACTUAL derived direction (whichever testament resolved it) against the
    textbook-expected one.

    Two distinct outcomes, deliberately not conflated:
      - `violations` — the slot resolved to a CONFIDENT direction that CONTRADICTS the known answer.
        This is the hard failure: being confidently wrong (the Russian case) is a selector bug, not a
        limit of the method, and `passed` is False whenever this list is non-empty.
      - `abstentions` — the slot came back `null`/`mixed` where the answer is textbook-unambiguous
        (the English case). Not a hard failure on its own (abstaining is honest, if suspicious), but
        real signal that the measurement is noisier than it should be — reported, not swallowed.

    Call this on the SAME `derived_docs` mapping `validate_derived` reads, before trusting a sweep's
    output enough to merge it into `gram_struct.py --build`."""
    violations = []
    abstentions = []
    matches = []
    for slot, table in KNOWN_ANSWERS.items():
        for iso, expected in table.items():
            doc = derived_docs.get(iso)
            if not doc or slot not in doc:
                continue
            actual = doc[slot].get("direction")
            if actual is None:
                abstentions.append({"slot": slot, "iso": iso, "expected": expected})
            elif actual != expected:
                violations.append({"slot": slot, "iso": iso, "expected": expected, "actual": actual,
                                   "detail": doc[slot]})
            else:
                matches.append({"slot": slot, "iso": iso})
    return {"passed": not violations, "violations": violations, "abstentions": abstentions,
           "matches": len(matches), "checked": len(matches) + len(violations) + len(abstentions)}


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

    # Roadmap D2 (2026-09-25): Greek half of possessor/subject_verb/object_verb, from `HebToken.role`
    # (NT-only). Computed over the SAME content-only `anchors` D0's Hebrew stats use below (unlike D1's
    # markers, a possessor/subject/object/verb token IS a content word) — reaches every language with
    # an NT, combined with the OT-derived version (if any) via `_combine_testaments` rather than one
    # silently overwriting the other. `load_greek_frames()` is `lru_cache`d — parsed once per process,
    # not once per language.
    d2_greek = derive_d2_slots(recs_all, anchors, frame_index=load_greek_frames())

    if ot_books:
        heb = HebrewSource()
        recs_ot = build_corpus(ot_books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
        stats = compute_order_stats(recs_ot, anchors)

        possessor = _order_slot(stats["rec_after_rate"], stats["rec_after_n"], min_n=50)
        combined = _combine_testaments(possessor, d2_greek.get("possessor"))
        if combined is not None:
            doc["possessor"] = combined

        # D0-fix (2026-09-26): subject_verb/object_verb no longer read `stats["func_order"]`
        # (order-kept relative to Hebrew's own source order — see `_hebrew_clause_role_stat`'s own
        # docstring for why that's unsound here). `possessor` above is UNCHANGED (rec_after_rate's
        # order-kept approach is sound for construct chains, which really are consistently head-first).
        for func, slot_name in (("Subj", "subject_verb"), ("Objc", "object_verb")):
            heb_stat = _hebrew_clause_role_stat(recs_ot, anchors, func)
            heb_slot = _direction_slot(heb_stat, _D2_MIN_N[slot_name])
            combined = _combine_testaments(heb_slot, d2_greek.get(slot_name))
            if combined is not None:
                doc[slot_name] = combined

        prof = constituent_profile(tag, usj_dir, out_dir, methods=("eflomal", "gloss"))
        _CONSTITUENT_DIR.mkdir(parents=True, exist_ok=True)
        (_CONSTITUENT_DIR / f"{iso}.json").write_text(
            json.dumps(prof, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        doc.setdefault("audit", {})["constituent_order"] = {
            "source": "derived",
            **{k: prof[k] for k in ("verses_measured", "pair_order_kept", "function_drift") if k in prof}}
    else:
        # NT-only language (D2's entire reason to exist — the 1,211 languages D0's OT-only pass never
        # reached): no Hebrew counterpart to combine against, so `d2_greek`'s own facts (already tagged
        # testament="NT" by `_combine_testaments(None, ...)`) are the whole answer.
        for slot_name in ("possessor", "subject_verb", "object_verb"):
            combined = _combine_testaments(None, d2_greek.get(slot_name))
            if combined is not None:
                doc[slot_name] = combined

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
                                "negation", "negation_word")},
            "d2_slots_written": 0,
            "d2_slot_counts": {k: 0 for k in ("possessor", "subject_verb", "object_verb")}}
    known_answer_isos = {iso for table in KNOWN_ANSWERS.values() for iso in table}
    known_answer_docs: dict[str, dict] = {}
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
            if any(k in doc for k in stats["d2_slot_counts"]):
                stats["d2_slots_written"] += 1
            for k in stats["d2_slot_counts"]:
                if k in doc:
                    stats["d2_slot_counts"][k] += 1
        if iso in known_answer_isos:
            known_answer_docs[iso] = doc
        doc["content_sha256"] = _content_sha256(doc)
        (out_dir / f"{iso}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
    # D2-fix (2026-09-26): a hard, automated gate, not a discretionary read — see
    # `check_known_answers`'s own docstring for why this exists and what it does/doesn't catch.
    stats["known_answers"] = check_known_answers(known_answer_docs)
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
         f"{stats['d1_slot_counts']}, {stats['d2_slots_written']} got >=1 D2 slot: "
         f"{stats['d2_slot_counts']}), {stats['gate_failed']} gated on alignment_quality, "
         f"{stats['no_edition']} had no edition at all → {args.out}", file=sys.stderr)
    ka = stats["known_answers"]
    print(f"[derive_typology] known-answer gate: {ka['matches']}/{ka['checked']} matched, "
         f"{len(ka['abstentions'])} abstained, {len(ka['violations'])} VIOLATED", file=sys.stderr)
    if not ka["passed"]:
        print(f"[derive_typology] KNOWN-ANSWER GATE FAILED — a language resolved to a confident, "
             f"textbook-wrong direction. Not safe to merge into gram_struct.py --build. Violations: "
             f"{json.dumps(ka['violations'], indent=1)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
