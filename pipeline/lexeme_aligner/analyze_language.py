"""Phase 1 of the Aim-2 "data-anchored conventions" plan (internal-docs/llm-align-experiment-plan.md):
cross-reference a language's own base-chain (eflomal) output against its Grambank typological profile,
to find PLAUSIBLE spots where the statistical aligner's own multi-word behaviour might not track the
language's real grammar — candidates for a `config/llm_conventions/<iso>.md` note, WITHOUT needing gold.

Deterministic, local, free: no LLM call, no network beyond what `grambank_fetch.py` already vendored.
This is exactly the audit that, done by hand this session, found Hindi's postposition bug (eflomal:
168 occurrences of 3 names, 0 multi-word, despite Hindi marking oblique case — GB072=1 — while French,
GB072=0, correctly has no such expectation).

DESIGN: Grambank features are RISK FLAGS, not verdicts. A feature being "on" says a category is WORTH
auditing, not what the answer should be — the actual signal is the empirical multi-word-span RATE from
the language's own eflomal output. A flagged risk with a near-zero observed rate is a candidate for a
convention note; the note itself should still be written from a REAL example (as today's hin.md/fra.md
fixes were), not asserted from the statistic alone. Findings here are "audit-derived, unverified" —
weaker than a note checked against real gold, and should be labelled as such if turned into a note.

    python3 -m lexeme_aligner.analyze_language --iso hinirv --publish-iso hin --book MAT
    python3 -m lexeme_aligner.analyze_language --iso fra-lsg --publish-iso fra --book MAT --json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.gapfill import load_priors
from lexeme_aligner.grambank_fetch import FEATURES as GRAMBANK_FEATURES, _OUT as GRAMBANK_FEATURES_FILE

# (risk key, POS tags the check applies to, minimum observed multi-word rate below which it's an
# anomaly, human description of what the flagged Grambank feature means for alignment spans, polarity,
# prompt_hint). `prompt_hint` is a short, LLM-prompt-facing phrasing of the SAME risk — distinct from
# `description` (developer/report-facing, verbose, cites Grambank IDs) because the two audiences want
# different things: a report reader wants the Grambank ID and the full "what to check" sentence; a model
# reading a SEEDS line wants one short actionable clause. Consumed by llm_align.py's `_risk_notes` to
# annotate a lexeme's SEEDS line (llm_prompt._seed_line) when its POS is flagged for the current
# language — see internal-docs/llm-align-experiment-plan.md §14.
# polarity "any_one" (default): flagged when ANY listed Grambank feature is "1" — the language HAS this
# category, so a free word for it is plausible. polarity "not_all_one": flagged unless EVERY listed
# feature is "1" — the language's marking is INCOMPLETE (partial or absent), so a free word may still be
# needed to fill the gap. NOTE the first cut of this rule tried "all_zero" (flag only when every feature
# is exactly "0") and it was WRONG: Grambank's GB089/090 record whether a suffix/prefix indexing the
# subject exists AT ALL, not whether it's rich enough to license dropping the pronoun — English is coded
# GB089=1 (its bare 3rd-singular "-s" counts) even though that one suffix can't replace "he/I/you/they" in
# the other five person/number slots, so "all_zero" never fired for English, the exact case that motivated
# this rule. Checked real values 2026-09-23: arb is the ONLY gold language with GB089=1 AND GB090=1 (full
# suffix+prefix paradigm) — and arb's own gap evidence (this session) genuinely has no subject-pronoun
# undershoot at all. eng/hin/por/fra all have at most one of the two "1" and all show a real, measured
# need for a free subject-marking word (eng confirmed directly: ~8% of gold-undershot verbs were a bare
# subject pronoun). "not_all_one" is the polarity that matches this pattern.
RISK_RULES = [
    ("case_marking", ("name", "noun"), 0.05,
     "oblique non-pronominal case marking (GB072) — check whether a name/noun ever needs a following "
     "adposition/postposition attached for a genitive/dative/ablative-type source relation.", "any_one",
     "this language often marks a genitive/dative/oblique relation with a following adposition/"
     "postposition — check whether the source's own case/relation needs one attached here"),
    ("tam_auxiliary", ("verb",), 0.05,
     "tense/aspect/mood carried by a separate auxiliary word (GB119-121) — check whether verb spans "
     "ever need more than one target word for tense/aspect/mood.", "any_one",
     "this language often marks tense/aspect/mood with a separate auxiliary word — check whether the "
     "source verb's own tense/aspect needs one added"),
    ("articles", ("noun", "name"), 0.05,
     "definite/specific articles (GB020-023) — check whether noun/name spans ever need a leading "
     "article word.", "any_one",
     "this language often marks definiteness with a leading article — check whether one belongs to "
     "this span"),
    ("possession_affix", ("noun",), 0.05,
     "possessive marking (GB430-433) — check whether a possessed noun ever needs a following "
     "possessive word.", "any_one",
     "this language often marks possession with a following possessive word — check whether one "
     "belongs to this span"),
    ("subject_indexing", ("verb",), 0.05,
     "the verb's own subject marking is incomplete (not both GB089 suffix and GB090 prefix) — check "
     "whether verb spans ever need an added free subject pronoun (a Greek/Hebrew pro-drop verb has no "
     "separate source word for it).", "not_all_one",
     "this language's verbs often need an explicit free subject pronoun (unlike a pro-drop source "
     "verb) — check whether one belongs to this span"),
]


def load_grambank(publish_iso: str, path: Path | None = None) -> dict[str, str] | None:
    """{Grambank feature ID: '0'|'1'} for this ISO, or None if not covered (~49% of published
    languages overall; ~75% of the languages already resourced enough to have Clear gold — checked
    2026-09-23 against config/gold_langs.json). `path` is looked up against the module-level
    `GRAMBANK_FEATURES_FILE` at CALL time (not as a default argument) so tests can monkeypatch it."""
    path = path or GRAMBANK_FEATURES_FILE
    if not Path(path).exists():
        return None
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc.get("languages", {}).get(publish_iso)


def multiword_rates(iso: str, out_dir: Path, lex_pos: dict[str, str], method: str = "eflomal"
                    ) -> dict[str, tuple[int, int]]:
    """pos -> (multi_word_pairs, total_pairs), from the base chain's own content pairs — the same
    "how often does this method ever produce more than one target word for this category" question
    asked by hand on Hindi's names this session, generalised to any POS Grambank flags as worth
    checking."""
    counts: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])   # pos -> [multiword, total]
    for fp in tag_files(out_dir, method, iso):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                for p in rec.get("pairs", []):
                    if not (p.get("content") and p.get("t_idx")):
                        continue
                    pos = lex_pos.get(p.get("lexeme"))
                    if not pos:
                        continue
                    c = counts[pos]
                    c[1] += 1
                    if len(p["t_idx"]) > 1:
                        c[0] += 1
    return {pos: tuple(v) for pos, v in counts.items()}


def analyze(iso: str, publish_iso: str, out_dir: Path = OUT, prior_pack: Path = PRIOR_PACK,
           method: str = "eflomal") -> dict:
    """The phase-1 report: Grambank coverage, per-POS multi-word rates, and any risk/anomaly matches."""
    lex_pos, _ = load_priors(prior_pack)
    grambank = load_grambank(publish_iso)
    rates = multiword_rates(iso, out_dir, lex_pos, method)

    findings = []
    if grambank is not None:
        for risk_key, pos_tags, threshold, description, polarity, prompt_hint in RISK_RULES:
            feature_ids = GRAMBANK_FEATURES.get(risk_key, [])
            if polarity == "not_all_one":
                flagged = bool(feature_ids) and not all(grambank.get(f) == "1" for f in feature_ids)
                matched_ids = [f for f in feature_ids if grambank.get(f) != "1"]
            else:
                flagged = any(grambank.get(f) == "1" for f in feature_ids)
                matched_ids = [f for f in feature_ids if grambank.get(f) == "1"]
            if not flagged:
                continue
            for pos in pos_tags:
                mw, total = rates.get(pos, (0, 0))
                if total == 0:
                    continue
                rate = mw / total
                if rate < threshold:
                    findings.append({
                        "risk": risk_key, "pos": pos, "multiword": mw, "total": total,
                        "rate": round(rate, 4), "grambank_ids": matched_ids,
                        "description": description, "prompt_hint": prompt_hint,
                    })
    return {
        "iso": iso, "publish_iso": publish_iso, "method": method,
        "grambank_covered": grambank is not None,
        "multiword_rates": {pos: {"multiword": mw, "total": t, "rate": round(mw / t, 4) if t else None}
                            for pos, (mw, t) in sorted(rates.items())},
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="edition tag whose align_<method>_<iso>_*.jsonl to audit")
    ap.add_argument("--publish-iso", required=True, help="bare published iso, for the Grambank lookup")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--prior-pack", type=Path, default=PRIOR_PACK)
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    report = analyze(a.iso, a.publish_iso, a.out, a.prior_pack, a.method)
    if a.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0

    print(f"=== phase-1 analysis: {a.iso} (publish_iso={a.publish_iso}) ===")
    print(f"Grambank coverage: {'yes' if report['grambank_covered'] else 'NOT COVERED — audit is eflomal-only, no independent cross-check'}")
    print("multi-word rate by POS (base chain's own content pairs):")
    for pos, r in report["multiword_rates"].items():
        print(f"  {pos:8} {r['multiword']:5}/{r['total']:<5} = {r['rate']*100:.1f}%" if r["rate"] is not None else f"  {pos}: no data")
    if report["findings"]:
        print("\nFLAGGED (Grambank marks this category as needing a free word here; observed rate is near zero):")
        for f in report["findings"]:
            print(f"  [{f['risk']}] pos={f['pos']} rate={f['rate']*100:.1f}% ({f['multiword']}/{f['total']}) "
                  f"grambank={f['grambank_ids']}")
            print(f"    -> {f['description']}")
    else:
        print("\nno anomalies flagged" + ("" if report["grambank_covered"] else " (no Grambank data to cross-check against)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
