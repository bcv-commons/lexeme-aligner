"""Roadmap I1 (internal-docs/aim1-typology-source-structure-plan.md §4R): URIEL+ as a second `imputed`
gram-struct source, alongside lang2vec (`typology.py`). Same owner decision as lang2vec (CC-BY-SA-4.0,
kept in its own pinned file with its own license line, may be vendored and published) and the SAME
validation protocol Step 2 used for lang2vec (`typology.validate_agreement`): ship a slot only at >=90%
agreement with Grambank on the languages Grambank resolves.

REAL MEASUREMENT (2026-09-25, `urielplus` 1.3.1, `mean_imputation()` — the fastest of URIEL+'s four
imputation strategies; KNN/SoftImpute/MIDASpy were NOT attempted due to time budget, and might do
better — a documented follow-up, not assumed). Packaging: `urielplus` alone lacks its imputation
dependencies; `pip install "urielplus[imputation]"` pulls a real, heavy extra (scikit-learn, cvxpy,
fancyimpute, ~20 packages) — same class of finding as lang2vec's own `setuptools<81` conflict,
documented here and in `config/PROVENANCE.txt` instead of assumed away.

Per-slot agreement vs `config/grambank/features.json` (the same reference set Step 2 used):
    adposition      98.2%  (646/658)   -- SHIPPED (beats lang2vec's own 97.01%)
    object_verb     97.4%  (680/698)   -- SHIPPED (beats lang2vec's own 94.47%)
    article         90.5%  (19/21)     -- NOT SHIPPED: n=21 is too small to trust a bare pass over the
                                          90% line; lang2vec itself failed this slot at 73.8% (Step 2),
                                          and URIEL+'s own self-reported quality metric for the WHOLE
                                          S_-prefixed syntactic feature block was only 74% accuracy on
                                          held-out mean-imputation, so a 19/21 sample is not strong
                                          enough evidence to overturn that — needs a real KNN/SoftImpute
                                          run with a larger resolved set before it can be trusted.
    possessor       66.0%  (1174/1779) -- NOT SHIPPED, fails badly. lang2vec (91.60%) remains the only
                                          imputed source for this slot.
    subject_verb    82.5%  (998/1210)  -- NOT SHIPPED, fails the 90% bar. lang2vec (93.25%) remains the
                                          only imputed source for this slot.
Only `adposition` and `object_verb` are ever written by this module — a slot that fails validation is
never persisted, matching D1's own "withhold, don't ship experimental" precedent for a failed
known-answer/agreement check.

COVERAGE (real, checked against the current gram-struct build, not assumed): of the 10 published
languages with zero gram-struct fact at the time this was built (`bux, flh, jen, khj, kql, ktm, kze,
lng, njd, zbu` — NONE of which lang2vec resolves at all, despite lang2vec's own "100% coverage" framing
in `typology.py`'s docstring referring to ITS OWN database scope, not actual ISO-code overlap with
every published language), only **2** (`ktm`, `kze`) get a validated fact from URIEL+ (`object_verb`);
the other 8 either fail URIEL+'s own validated-slot check too, or URIEL+ has no resolvable value for
them at all even after imputation. Beyond those 10, URIEL+ adds a genuinely NEW (not already covered by
lang2vec or Grambank) adposition/object_verb fact for **150** published languages total — a real,
if modest, coverage gain, concentrated among languages lang2vec's own ISO-code list simply does not
reach rather than languages where lang2vec resolved but was wrong.

    python -m lexeme_aligner.uriel_plus --build   # runs mean_imputation (~30s) + writes the pin
    python -m lexeme_aligner.uriel_plus --validate
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

SHIPPED_SLOTS = ("adposition", "object_verb")
_ALL_SLOT_PAIRS = {
    "adposition": ("S_ADPOSITION_BEFORE_NOUN", "S_ADPOSITION_AFTER_NOUN"),
    "article": ("S_ARTICLE_WORD_BEFORE_NOUN", "S_ARTICLE_WORD_AFTER_NOUN"),
    "possessor": ("S_POSSESSOR_BEFORE_NOUN", "S_POSSESSOR_AFTER_NOUN"),
    "subject_verb": ("S_SUBJECT_BEFORE_VERB", "S_SUBJECT_AFTER_VERB"),
    "object_verb": ("S_OBJECT_BEFORE_VERB", "S_OBJECT_AFTER_VERB"),
}
# measured this session (module docstring) — used as the shipped fact's own recorded confidence
_MEASURED_AGREEMENT = {"adposition": 0.982, "object_verb": 0.974}
_OUT = Path("config/typology/uriel_plus.json")


def _slot_direction(row_by_feat: dict[str, np.ndarray], slot: str) -> str | None:
    """`row_by_feat`: {feature_name: per-source array (post-imputation, no -1 left ideally)} for ONE
    language. Mean-of-known-sources per side, same "before>=.5 xor after>=.5" rule used to derive the
    real numbers in this module's own docstring."""
    bf, af = _ALL_SLOT_PAIRS[slot]
    b, a = row_by_feat.get(bf), row_by_feat.get(af)
    if b is None or a is None:
        return None
    bk, ak = b[b != -1], a[a != -1]
    if bk.size == 0 or ak.size == 0:
        return None
    bv, av = float(np.mean(bk)), float(np.mean(ak))
    if bv >= 0.5 and av < 0.5:
        return "before"
    if av >= 0.5 and bv < 0.5:
        return "after"
    return None


def build_all_slot_directions() -> dict[str, dict[str, str]]:
    """{iso: {slot: direction}} for ALL FIVE slots (unfiltered) — the raw material `validate_agreement`
    and `build_directions` both filter down from. Requires `pip install "urielplus[imputation]"`."""
    from urielplus.urielplus import URIELPlus  # optional heavy dep, imported lazily

    u = URIELPlus()
    u.mean_imputation()
    feats = list(u.feats[1])
    langs = [str(l) for l in u.langs[1]]
    data = u.data[1]
    fi = {f: i for i, f in enumerate(feats)}
    out: dict[str, dict[str, str]] = {}
    for i, iso in enumerate(langs):
        row = data[i]
        d = {}
        for slot, (bf, af) in _ALL_SLOT_PAIRS.items():
            v = _slot_direction({bf: row[fi[bf]], af: row[fi[af]]}, slot)
            if v:
                d[slot] = v
        if d:
            out[iso] = d
    return out


def validate_agreement(all_directions: dict[str, dict[str, str]] | None = None,
                       grambank_path: Path = Path("config/grambank/features.json")
                       ) -> dict[str, tuple[int, int]]:
    """{slot: (agree, total)} vs Grambank, the same reference set + protocol as
    `typology.validate_agreement`. Real numbers for this run are in the module docstring above."""
    if all_directions is None:
        all_directions = build_all_slot_directions()
    doc = json.loads(Path(grambank_path).read_text(encoding="utf-8"))
    gb = doc["languages"]
    feat_groups = doc["_features"]

    def gb_pair_direction(entry: dict, codes: list[str]) -> str | None:
        before, after = entry.get(codes[0]), entry.get(codes[1])
        if before == "1" and after != "1":
            return "before"
        if after == "1" and before != "1":
            return "after"
        return None

    results: dict[str, tuple[int, int]] = {}
    for slot in _ALL_SLOT_PAIRS:
        agree = total = 0
        for iso, entry in gb.items():
            if slot == "possessor":
                v = entry.get(feat_groups["possession_order"][0])
                gbv = "before" if v == "1" else "after" if v == "2" else None
            else:
                code_key = {"adposition": "adposition_order", "article": "article_order",
                           "subject_verb": "subject_verb_order",
                           "object_verb": "object_verb_order"}[slot]
                gbv = gb_pair_direction(entry, feat_groups[code_key])
            if not gbv:
                continue
            uv = all_directions.get(iso, {}).get(slot)
            if not uv:
                continue
            total += 1
            agree += gbv == uv
        results[slot] = (agree, total)
    return results


def build_directions(out: Path = _OUT) -> dict[str, dict[str, str]]:
    """The PERSISTED, PINNED file — only `SHIPPED_SLOTS`, only languages URIEL+ actually resolves them
    for. Never overwrites `config/typology/directions.json` (lang2vec's own pin) — a separate file,
    same as the owner decision requires for a CC-BY-SA-4.0 source."""
    all_directions = build_all_slot_directions()
    shipped = {}
    for iso, d in all_directions.items():
        kept = {slot: {"direction": d[slot], "source": "uriel_plus",
                       "confidence": _MEASURED_AGREEMENT[slot]}
               for slot in SHIPPED_SLOTS if slot in d}
        if kept:
            shipped[iso] = kept
    Path(out).write_text(json.dumps(shipped, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return shipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--out", type=Path, default=_OUT)
    a = ap.parse_args(argv)
    if a.validate:
        for slot, (agree, total) in validate_agreement().items():
            pct = f"{100*agree/total:.1f}%" if total else "n/a"
            print(f"{slot:14} {agree}/{total}  {pct}", file=sys.stderr)
        return 0
    if a.build:
        shipped = build_directions(a.out)
        print(f"[uriel_plus] {len(shipped)} language(s) with >=1 shipped slot -> {a.out}", file=sys.stderr)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
