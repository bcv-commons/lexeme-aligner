"""A typology table beyond Grambank — Step 2 (B+D) of internal-docs/aim1-typology-source-structure-
plan.md. `span_extension.py`'s own `direction_for`/`possession_direction_for` answer "before"/"after"/
None from Grambank alone, which covers only ~49% of published languages (799/1,629). This module adds
three FALLBACK sources, each used for a slot only after its agreement with Grambank on the languages
both cover is measured and found trustworthy — never assumed.

SLOTS: `adposition`, `article`, `possessor`, `subject_verb`, `object_verb` — each resolves to "before" /
"after" / None (ambiguous, absent, or no source at all — NEVER guessed).

SOURCE PRIORITY per slot (first available AND validated source wins):
  1. **Grambank** (already vendored, `config/grambank/features.json`) — GB074/075 (adposition),
     GB022/023 (article), GB065 (possessor, ternary), GB131/133 (subject/verb — NOT a validated
     signal, see span_extension.py's own note that a verb-order proxy for SUBJECT POSITION measured a
     wash; used here only for its LITERAL meaning, subject-verb order, which is what it actually is).
  2. **WALS CLDF** (vendored `pipeline/vendor/wals/{values,codes,languages,parameters}.csv`, pinned
     tag v2020.5, CC-BY-4.0 — see config/PROVENANCE.txt). Parameters: 85A (adposition: 1=post/after,
     2=pre/before), 86A (genitive-noun order: 1=Gen-N/before, 2=N-Gen/after — this IS possessor
     order), 82A (subject-verb: 1=SV/before, 2=VS/after), 83A (object-verb: 1=OV/before, 2=VO/after).
     **No `article` mapping**: checked the full WALS parameter list — 37A/38A give article TYPE
     (word/affix/demonstrative-derived/absent), never its ORDER relative to the noun. WALS simply has
     no comparable feature; the `article` slot is never filled from WALS, not forced through a
     mismatched proxy (88A "Order of Demonstrative and Noun" was considered and rejected — conflates
     two different word classes).
  3. **lang2vec `syntax_knn`** (`pip install lang2vec`, CC-BY-SA-4.0, own license line in
     `config/PROVENANCE.txt`). Exact feature names verified against the installed package (2026-09-24,
     `get_features(['eng'], 'syntax_knn', header=True)`), NOT assumed from the paper: adposition =
     `S_ADPOSITION_BEFORE_NOUN`/`_AFTER_NOUN`; article = `S_ARTICLE_WORD_BEFORE_NOUN`/`_AFTER_NOUN`;
     possessor = `S_POSSESSOR_BEFORE_NOUN`/`_AFTER_NOUN`; subject_verb = `S_SUBJECT_BEFORE_VERB`/
     `_AFTER_VERB`; object_verb = `S_OBJECT_BEFORE_VERB`/`_AFTER_VERB`. Values are floats (0.0/1.0 in
     every case checked); both 1.0 (e.g. eng `S_POSSESSOR_BEFORE_NOUN`=`S_POSSESSOR_AFTER_NOUN`=1.0,
     correctly — English allows both "John's book" and "book of John") or both 0.0 means ambiguous/
     absent, same "never guess" convention as `direction_for`. **100% language coverage claimed by
     lang2vec** (KNN-imputed) — this is the source that reaches languages Grambank and WALS don't.
     `pip install lang2vec` pulled in `setuptools>=81`, which DROPPED the `pkg_resources` module
     lang2vec's own code still imports — pinned `setuptools<81` in `pyproject.toml`'s `typology` extra
     to keep it importable (a real, current packaging conflict, not a hypothetical one).
  4. **Empirical** (`constituent_order.py`, our own high-confidence alignments) — deferred, not built
     in this pass; see internal-docs/aim1-typology-source-structure-plan.md Step 2's own scope note.

VALIDATION (before ANY fallback source is trusted for ANY slot): `validate_agreement()` computes, per
(source, slot), the fraction of Grambank-covered languages where that source AGREES with Grambank
(both resolve to the same before/after, or Grambank is silent) — a source is used for a slot only at
**≥ 90% agreement** (Östling 2015's own bar for adposition order was 94.8-95.1%).

MEASURED 2026-09-24 (real run, `--validate`, against `config/grambank/features.json`'s ~2,300
languages with any Grambank data at all):
  slot            WALS agreement (n)       lang2vec agreement (n)
  adposition      98.62% (580)             97.01% (1,271)
  article         no WALS parameter        73.82% (638)  <- BELOW threshold, NOT used
  possessor       94.69% (621)             91.60% (1,322)
  subject_verb    95.71% (560)             93.25% (963)
  object_verb     96.64% (565)             94.47% (977)
Every slot clears 90% on both sources EXCEPT lang2vec's `article` direction, which is real and
excluded, not a bug — `build()` simply never uses lang2vec for that one slot, so the `article` slot
has NO fallback at all beyond Grambank itself (WALS has none either, see above) for a language
outside Grambank's coverage. `build()` (2026-09-24, real run): **3,304 languages** got at least one
slot filled, versus Grambank's ~2,300 alone — a real coverage gain, concentrated in adposition/
possessor/subject_verb/object_verb.

END-TO-END VALIDATION (the plan's own bar: F1 on spa_r09/ben_irv/asm_irv — Clear gold, zero Grambank
coverage — with the typology-driven `case_marking`/`articles`/`possession_affix` triggers enabled via
`analyze_language.analyze(use_typology=True)` / `span_extension`'s `--typology-fallback`). Aggregate
whole-Bible F1 alone was misleading here exactly the way it was for the fra articles investigation
(span_extension.py's own docstring) — split into gold-unclaimed (metric artifact) vs real conflict:
  spa: F1 .774->.727 (looks like a big drop) — but 4,440 improvements vs 3,705 REAL conflicts: net
       POSITIVE on the part of the metric that can actually judge it; the rest (most of the aggregate
       drop) is the SAME gold-unclaimed-article artifact already diagnosed for fra.
  ben: F1 .599->.597 (near flat) — but 427 improvements vs 1,355 REAL conflicts (93% of regressions
       are genuine conflicts, NOT a gold-convention artifact this time): net NEGATIVE.
  asm: F1 .557->.557 (flat) — 115 improvements vs 258 real conflicts (92% conflict): net NEGATIVE.
Hand-checked the ben/asm conflicts: the words wrongly grabbed are overwhelmingly PRONOUNS and
CONJUNCTIONS (Bengali তিনি/সে "he/she", তারা "they", এবং "and", তার "his/her", ...), not postpositions
or genitive markers — the SAME root cause already flagged as a risk in span_extension.py's Step 1
section (`stop.is_function`'s blanket stopword acceptance, not the typology direction itself, which
is correct). Spanish's own stopword set is dominated by genuine articles/prepositions, so the same
broad check does much less damage there. **CONCLUSION: the typology TABLE is sound (validated,
useful, real coverage gain); WIRING it into span_extension's existence gate is a genuine per-language
split, not a universal win — shipped as `use_typology=False` / `--typology-fallback` (opt-in), not
unconditional.** This is now the THIRD independent case (after fra's possessive-determiner steal and
hin's construct-chain misattribution) of the same underlying weakness — narrowing the candidate-word
check beyond "any stopword" is the highest-value remaining fix for this whole mechanism, not
anything specific to Grambank, WALS, or lang2vec.

    python3 -m lexeme_aligner.typology --validate           # print per-(source,slot) agreement, no write
    python3 -m lexeme_aligner.typology --build               # write config/typology/directions.json
    python3 -m lexeme_aligner.typology --direction <iso> <slot>   # one lookup, for debugging
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

_WALS_DIR = Path("pipeline/vendor/wals")
_OUT = Path("config/typology/directions.json")
_GRAMBANK_FEATURES_FILE = Path("config/grambank/features.json")

SLOTS = ("adposition", "article", "possessor", "subject_verb", "object_verb")

# --- Grambank -------------------------------------------------------------------------------------------
# (before_id, after_id) pairs — read directly from grambank_fetch.FEATURES (the single source of truth
# for what each pair of GB codes means) rather than a second hardcoded copy here, which is exactly how
# a first draft of this module got `subject_verb`'s pair BACKWARDS (GB131/GB133 swapped — verb-initial
# was read as "before" instead of "after") and never caught it until cross-checking against
# grambank_fetch.py's own comment. `typology.py` still has no import from `span_extension.py` (kept a
# one-way dependency, span_extension -> typology, not the reverse) but DOES depend on grambank_fetch,
# which neither module needs the other for.
from lexeme_aligner.grambank_fetch import FEATURES as _GRAMBANK_FEATURES

_GRAMBANK_PAIRS = {
    "adposition": tuple(_GRAMBANK_FEATURES["adposition_order"]),
    "article": tuple(_GRAMBANK_FEATURES["article_order"]),
    "subject_verb": tuple(_GRAMBANK_FEATURES["subject_verb_order"]),
    "object_verb": tuple(_GRAMBANK_FEATURES["object_verb_order"]),
}
_GRAMBANK_POSSESSOR_TERNARY = _GRAMBANK_FEATURES["possession_order"][0]   # 1=before, 2=after, 3=both/free


def _load_grambank_all() -> dict[str, dict[str, str]]:
    if not _GRAMBANK_FEATURES_FILE.exists():
        return {}
    return json.loads(_GRAMBANK_FEATURES_FILE.read_text(encoding="utf-8")).get("languages", {})


def grambank_direction(grambank: dict[str, str] | None, slot: str) -> str | None:
    if not grambank:
        return None
    if slot == "possessor":
        v = grambank.get(_GRAMBANK_POSSESSOR_TERNARY)
        return "before" if v == "1" else "after" if v == "2" else None
    pair = _GRAMBANK_PAIRS.get(slot)
    if not pair:
        return None
    before, after = grambank.get(pair[0]) == "1", grambank.get(pair[1]) == "1"
    if before and not after:
        return "before"
    if after and not before:
        return "after"
    return None


# --- WALS CLDF -------------------------------------------------------------------------------------------
# parameter_id -> (before_code_number, after_code_number) — see module docstring for the codes.csv
# Names these numbers correspond to. `article` deliberately absent (no WALS parameter for it).
_WALS_PARAMS = {
    "adposition": ("85A", "2", "1"),      # 85A-2 Prepositions=before, 85A-1 Postpositions=after
    "possessor": ("86A", "1", "2"),       # 86A-1 Genitive-Noun=before, 86A-2 Noun-Genitive=after
    "subject_verb": ("82A", "1", "2"),    # 82A-1 SV=before, 82A-2 VS=after
    "object_verb": ("83A", "1", "2"),     # 83A-1 OV=before, 83A-2 VO=after
}


def _load_wals_tables(wals_dir: Path = _WALS_DIR):
    """(iso -> [wals_lang_id, ...], (parameter_id, wals_lang_id) -> value_number) or (None, None) if
    the vendored CSVs aren't present (this source is skipped entirely, not an error)."""
    langs_fp, values_fp = wals_dir / "languages.csv", wals_dir / "values.csv"
    if not (langs_fp.exists() and values_fp.exists()):
        return None, None
    iso_to_wals: dict[str, list[str]] = collections.defaultdict(list)
    for row in csv.DictReader(langs_fp.open(encoding="utf-8")):
        if row["ISO639P3code"]:
            iso_to_wals[row["ISO639P3code"]].append(row["ID"])
    values: dict[tuple[str, str], str] = {}
    for row in csv.DictReader(values_fp.open(encoding="utf-8")):
        values[(row["Parameter_ID"], row["Language_ID"])] = row["Value"]
    return dict(iso_to_wals), values


def wals_direction(iso: str, slot: str, iso_to_wals: dict, values: dict) -> str | None:
    """Majority vote across every WALS entry for this ISO (a macrolanguage/dialect cluster can have
    several) when they disagree; None (not a guess) when there's a genuine tie or no data at all."""
    spec = _WALS_PARAMS.get(slot)
    if not spec or not iso_to_wals:
        return None
    param, before_num, after_num = spec
    votes = collections.Counter()
    for wals_id in iso_to_wals.get(iso, []):
        v = values.get((param, wals_id))
        if v == before_num:
            votes["before"] += 1
        elif v == after_num:
            votes["after"] += 1
    if not votes:
        return None
    top = votes.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None                                          # tied — genuinely ambiguous, don't guess
    return top[0][0]


# --- lang2vec syntax_knn ----------------------------------------------------------------------------------
_LANG2VEC_PAIRS = {
    "adposition": ("S_ADPOSITION_BEFORE_NOUN", "S_ADPOSITION_AFTER_NOUN"),
    "article": ("S_ARTICLE_WORD_BEFORE_NOUN", "S_ARTICLE_WORD_AFTER_NOUN"),
    "possessor": ("S_POSSESSOR_BEFORE_NOUN", "S_POSSESSOR_AFTER_NOUN"),
    "subject_verb": ("S_SUBJECT_BEFORE_VERB", "S_SUBJECT_AFTER_VERB"),
    "object_verb": ("S_OBJECT_BEFORE_VERB", "S_OBJECT_AFTER_VERB"),
}


_L2V_CACHE: dict | None = None


def _lang2vec_fetch_all(isos: tuple[str, ...]) -> dict:
    """ONE `get_features` call for every slot's pair at once (all 5 pairs live in the SAME `syntax_knn`
    feature vector `get_features` already returns per language) — measured 80s for a single slot over
    ~2,300 languages, so calling it once per slot (this function's first cut did) cost ~7 minutes for
    all 5 slots and blew through every reasonable script timeout. Cached per (sorted isos tuple) —
    `validate_agreement`/`build` both want the SAME full-catalogue fetch, not five of them.

    `get_features` RAISES (not skips) on the first code it doesn't recognize — one unrecognized ISO
    among thousands (caught live: "eud" isn't in URIEL) aborts the WHOLE batch. Pre-filter against
    `lang2vec.LANGUAGES` (the library's own recognized-code set) instead of a slower per-language
    fallback loop — the ~100% coverage claim is over URIEL's own catalogue, not over an arbitrary
    ISO 639-3 code, and there is no cheaper way to find out which codes qualify than checking this set."""
    global _L2V_CACHE
    key = isos
    if _L2V_CACHE is not None and _L2V_CACHE.get("_key") == key:
        return _L2V_CACHE
    import lang2vec.lang2vec as l2v
    known = [iso for iso in isos if iso in l2v.LANGUAGES]
    res = l2v.get_features(known, "syntax_knn", header=True) if known else {}
    res["_key"] = key
    _L2V_CACHE = res
    return res


def lang2vec_direction_batch(isos: list[str], slot: str) -> dict[str, str | None]:
    """{iso: "before"|"after"|None} for every iso, from ONE cached lang2vec fetch (see
    `_lang2vec_fetch_all`) — calling this for several slots over the SAME `isos` list only pays the
    real lang2vec cost once. Returns {} entirely if lang2vec isn't installed or the fetch itself fails
    (a single bad language code among thousands should not be possible here since lang2vec fills
    unknown codes with a default/imputed vector rather than raising, but the whole batch degrading to
    {} on any other error is the safe fallback — every caller already treats a missing entry as "no
    signal from this source", not a crash)."""
    try:
        res = _lang2vec_fetch_all(tuple(isos))
    except ImportError:
        return {}
    except Exception:
        return {}
    header = res.get("CODE")
    if not header:
        return {}
    before_feat, after_feat = _LANG2VEC_PAIRS[slot]
    idx = {n: i for i, n in enumerate(header)}
    if before_feat not in idx or after_feat not in idx:
        return {}
    bi, ai = idx[before_feat], idx[after_feat]
    out: dict[str, str | None] = {}
    for iso in isos:
        vec = res.get(iso)
        if vec is None:
            out[iso] = None
            continue
        b, a = vec[bi], vec[ai]
        out[iso] = "before" if (b == 1.0 and a != 1.0) else "after" if (a == 1.0 and b != 1.0) else None
    return out


# --- validation -----------------------------------------------------------------------------------------
def validate_agreement(isos: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """{slot: {source: {"agree": n, "compared": n, "rate": float}}} — agreement of WALS and lang2vec
    with Grambank, over every language BOTH the fallback source and Grambank resolve to a direction
    (Grambank silent or the fallback silent -> not counted; this measures DISAGREEMENT on genuine
    overlap, not coverage). `isos`: defaults to every Grambank-covered language."""
    grambank_all = _load_grambank_all()
    isos = isos or list(grambank_all)
    iso_to_wals, wals_values = _load_wals_tables()

    out: dict[str, dict[str, dict]] = {slot: {"wals": {"agree": 0, "compared": 0},
                                              "lang2vec": {"agree": 0, "compared": 0}}
                                       for slot in SLOTS}
    for slot in SLOTS:
        l2v_dirs = lang2vec_direction_batch(isos, slot)
        for iso in isos:
            gb_dir = grambank_direction(grambank_all.get(iso), slot)
            if gb_dir is None:
                continue
            if iso_to_wals is not None:
                w_dir = wals_direction(iso, slot, iso_to_wals, wals_values)
                if w_dir is not None:
                    out[slot]["wals"]["compared"] += 1
                    out[slot]["wals"]["agree"] += int(w_dir == gb_dir)
            l_dir = l2v_dirs.get(iso)
            if l_dir is not None:
                out[slot]["lang2vec"]["compared"] += 1
                out[slot]["lang2vec"]["agree"] += int(l_dir == gb_dir)
    for slot in SLOTS:
        for src in ("wals", "lang2vec"):
            c = out[slot][src]["compared"]
            out[slot][src]["rate"] = round(out[slot][src]["agree"] / c, 4) if c else None
    return out


# --- build + lookup --------------------------------------------------------------------------------------
def build(agreement_threshold: float = 0.90, isos: list[str] | None = None
         ) -> tuple[dict[str, dict], dict]:
    """{iso: {slot: {"direction", "source", "confidence"}}} plus the validate_agreement() report that
    decided which fallback sources were trusted. Grambank is always trusted (confidence 1.0 — it's the
    reference every other source is validated against, not itself validated). WALS/lang2vec are used
    for a slot ONLY if `validate_agreement` measured that source's agreement with Grambank at
    `agreement_threshold` or better; otherwise that source contributes nothing for that slot at all
    (silently, not as an error) — see module docstring for why (Östling 2015's own bar was 94.8-95.1%).
    `isos`: defaults to the union of every Grambank- and WALS-covered language; lang2vec's own ~100%
    coverage means it never LIMITS this set, only Grambank/WALS presence does."""
    agreement = validate_agreement()
    use_wals = {slot: (agreement[slot]["wals"]["rate"] or 0) >= agreement_threshold for slot in SLOTS}
    use_lang2vec = {slot: (agreement[slot]["lang2vec"]["rate"] or 0) >= agreement_threshold
                    for slot in SLOTS}

    grambank_all = _load_grambank_all()
    iso_to_wals, wals_values = _load_wals_tables()
    isos = isos or sorted(set(grambank_all) | set(iso_to_wals or {}))

    table: dict[str, dict] = {}
    for slot in SLOTS:
        l2v_dirs = lang2vec_direction_batch(isos, slot) if use_lang2vec[slot] else {}
        for iso in isos:
            entry = None
            gb = grambank_direction(grambank_all.get(iso), slot)
            if gb is not None:
                entry = (gb, "grambank", 1.0)
            elif use_wals[slot] and iso_to_wals is not None:
                w = wals_direction(iso, slot, iso_to_wals, wals_values)
                if w is not None:
                    entry = (w, "wals", agreement[slot]["wals"]["rate"])
            if entry is None and use_lang2vec[slot]:
                l = l2v_dirs.get(iso)
                if l is not None:
                    entry = (l, "lang2vec", agreement[slot]["lang2vec"]["rate"])
            if entry is not None:
                direction, source, confidence = entry
                table.setdefault(iso, {})[slot] = {
                    "direction": direction, "source": source, "confidence": confidence}
    return table, agreement


_TABLE_CACHE: dict | None = None


def direction(iso: str, slot: str, path: Path = _OUT) -> str | None:
    """The main lookup other modules call: "before"/"after"/None from the pre-built table
    (`config/typology/directions.json`). Loaded once per process and cached; pass `path` explicitly
    (bypassing the cache) in tests. None if the table doesn't exist, or has no entry for (iso, slot) —
    never guessed."""
    global _TABLE_CACHE
    if path == _OUT:
        if _TABLE_CACHE is None:
            _TABLE_CACHE = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        table = _TABLE_CACHE
    else:
        table = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    entry = table.get(iso, {}).get(slot)
    return entry["direction"] if entry else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", action="store_true", help="print per-(source,slot) agreement, no write")
    ap.add_argument("--build", action="store_true", help="write config/typology/directions.json")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--out", type=Path, default=_OUT)
    ap.add_argument("--direction", nargs=2, metavar=("ISO", "SLOT"), help="one lookup, for debugging")
    a = ap.parse_args(argv)

    if a.direction:
        iso, slot = a.direction
        print(direction(iso, slot, path=a.out))
        return 0
    if a.validate:
        print(json.dumps(validate_agreement(), indent=1))
        return 0
    if a.build:
        table, agreement = build(a.threshold)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[typology] {len(table)} languages -> {a.out}", file=sys.stderr)
        print(json.dumps(agreement, indent=1), file=sys.stderr)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
