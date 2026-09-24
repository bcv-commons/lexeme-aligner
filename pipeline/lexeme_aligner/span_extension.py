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
happens to trip GB432=1). That polarity may be miscalibrated for what this category is meant to detect
(mirroring the "not_all_one" fix subject_indexing needed earlier); worth a real Clear-gold check on
hin/fra with a corrected polarity before trusting it, not assumed here. Kept wired (correctly implemented,
tested, harmless where inert) rather than reverted, since — unlike subject_indexing — it hasn't measured
WORSE anywhere; it just hasn't yet had a fair test on a language where it would show a difference.

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


def direction_for(grambank: dict[str, str], feature_group: str) -> str | None:
    """"before" / "after" / None (mixed, ambiguous, or absent — never guess)."""
    before_id, after_id = GRAMBANK_FEATURES[feature_group]
    before = grambank.get(before_id) == "1"
    after = grambank.get(after_id) == "1"
    if before and not after:
        return "before"
    if after and not before:
        return "after"
    return None


def possession_direction_for(grambank: dict[str, str]) -> str | None:
    """"before" / "after" / None, from GB065 directly — a single ternary value (1=possessor precedes
    possessum, 2=possessor follows, 3=both/free), not a before_id/after_id binary pair like
    direction_for()'s other callers, so it gets its own small helper instead of forcing GB065 through
    that shape."""
    v = grambank.get(GRAMBANK_FEATURES["possession_order"][0])
    return "before" if v == "1" else "after" if v == "2" else None


def _books(a) -> list[str]:
    return (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in (a.book or ["MAT"])])


def extend_spans(iso: str, publish_iso: str, usj_dir: Path, books: list[str], out_dir: Path = OUT,
                 methods: tuple[str, ...] = ("eflomal", "gloss"), prior_pack: Path = PRIOR_PACK
                 ) -> tuple[dict[str, list[dict]], dict]:
    """{BOOK: [verse record, ...]} of ONLY the pairs that got widened, plus stats. Never mutates the base
    chain's own jsonl — this is a separate, additive layer (see module docstring)."""
    lex_pos, _ = load_priors(prior_pack)
    grambank = load_grambank_raw(publish_iso)
    stats: collections.Counter = collections.Counter()
    if grambank is None:
        return {}, {"skipped": "no Grambank coverage for this language"}

    # Which (risk, pos) combinations are ACTUALLY flagged for this base-chain run, and in which direction —
    # reuses analyze_language's own anomaly detection so this mechanism never fires on a category the
    # phase-1 audit itself wouldn't flag as worth checking.
    report = analyze(iso, publish_iso, out_dir, prior_pack, method=methods[0])
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
            d = possession_direction_for(grambank)        # GB065 is ternary, not a before/after pair
        elif risk in DIRECTION_FEATURES:
            d = direction_for(grambank, DIRECTION_FEATURES[risk])
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
            d = direction_for(grambank, DIRECTION_FEATURES["case_marking"])
            if d:
                case_marking_direction = d
            break

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
    verse_toks: dict[int, list[str]] = {}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        verse_toks[ref] = list(r.toks)
        lexeme_of[ref] = {t.idx: t.lexeme for t in r.heb}
        struct_of[ref] = {t.idx: (t.state, t.case_) for t in r.heb}
    has_struct = heb.has_state or heb.has_case

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
            # Try each flagged (direction, risk) for this pos IN ORDER, falling through when one's own
            # candidate position isn't valid for THIS occurrence (already claimed, or not a function
            # word) — not just when the pos itself has no entry. Two+ risks sharing a pos (e.g. English
            # "noun" carrying both `articles` and `possession_affix`) are genuinely different needs for
            # different occurrences, not competing guesses about the same one; see `active`'s own comment.
            direction = stat_label = cand = None
            for d, _risk in active.get(pos, []):
                c = t_idx[0] - 1 if d == "before" else t_idx[-1] + 1
                if 0 <= c < len(toks) and c not in claimed and stop.is_function(toks[c]):
                    direction, stat_label, cand = d, pos, c
                    break
            if direction is None and case_marking_direction and has_struct:
                # ADDITIVE path: the lexeme's own POS tag didn't match (missing from the prior pack, or
                # not one of the categories the phase-1 audit flagged), but THIS occurrence's own
                # structured morphology confirms the case_marking relation independently. Never removes
                # anything the pos-tag gate above already catches — only adds cases it would otherwise
                # miss. See case_marking_direction's own comment for why this is additive, not a
                # replacement: a version that REQUIRED this confirmation (dropping the pos-tag gate)
                # measured worse on both hin and arb (see below).
                state, case_ = struct_of.get(ref, {}).get(p["h_idx"], (None, None))
                if state == "construct" or case_ in ("genitive", "dative"):
                    c = t_idx[0] - 1 if case_marking_direction == "before" else t_idx[-1] + 1
                    if 0 <= c < len(toks) and c not in claimed and stop.is_function(toks[c]):
                        direction, stat_label, cand = case_marking_direction, "struct", c
            if direction is None:
                continue
            new_t_idx = sorted(t_idx + [cand])
            new_target = " ".join(toks[j] for j in new_t_idx)
            ext = dict(p)
            ext.update(t_idx=new_t_idx, target=new_target, method="spanext",
                      prior=f"spanext_{stat_label}_{direction}")
            widened.append(ext)
            claimed.add(cand)
            stats[f"extended_{stat_label}"] += 1
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
    a = ap.parse_args(argv)

    books = _books(a)
    methods = tuple(m.strip() for m in a.methods.split(","))
    by_book, stats = extend_spans(a.iso, a.publish_iso, a.usj_dir, books, a.out, methods, a.prior_pack)
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
