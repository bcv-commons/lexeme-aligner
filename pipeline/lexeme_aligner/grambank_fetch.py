"""Pin Grambank's span-relevant typological features per ISO 639-3 → config/grambank/features.json.

WHAT THIS IS FOR: whether a single source word needs SEVERAL target words is the product of two things —
the source token's own morphology (spine-side, language-independent) and the TARGET language's typology
(does it express this relation as a free word or as an affix?). We can measure the first and induce a
lot else from a Bible alone, but the second cannot be induced from a Bible: it is exactly the parameter
Grambank encodes.

That the target-side half is real was verified against Clear gold spans BEFORE pinning anything
(2026-08-31) — mean target span at FINITE verbs (spine `person` populated) minus other content tokens:

    eng  +1.41   free subject pronouns REQUIRED
    hin  +0.77   pronouns common
    ben  +0.56 / asm +0.55   pro-drop-ish (Indic)
    arb  +0.21   PRO-DROP, subject already bound into the verb

A 6.7x spread, rank-ordered by whether the language needs a free subject pronoun — so a UNIFORM span
policy is provably wrong (no one cap serves both eng and arb), and this is the datum that decides per
language. It also retro-explains the blind-extension measurement, where arb gained least (+8%) and fra
most (+44%).

TWO PINNED DEPENDENCIES, not one: Grambank's own `ISO639P3code` column is EMPTY, so the join to our
bare-ISO catalogue must go through Glottolog's languoid table (8,184 glottocode→ISO pairs). Both are
CC-BY 4.0 and both are recorded in config/PROVENANCE.txt.

COVERAGE, measured: 2,467 Grambank varieties, 2,345 of which map to an ISO; that reaches 746 of our
1,513 published languages (49.3%). It is therefore an ENHANCEMENT WHERE AVAILABLE and never a
requirement — the mission constraint in internal-docs/gap-fill-scaling-strategy.md §0 stands. Absent
coverage the consumer must fall back to NOT extending (blind extension measured harmful: marginal token
precision decays 22.5% -> 12.8% -> 10.5% as the cap rises, against a 24.4% no-extension base), never to
a uniform cap.

    python3 -m lexeme_aligner.grambank_fetch --build
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
import urllib.request
from pathlib import Path

_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
GRAMBANK_COMMIT = "9e0f34194224204fa6a2058a2c12d43923e8715f"
GLOTTOLOG_COMMIT = "072ca0d0410039fb8b779be8fc165bac575d2cda"
_GB = f"https://raw.githubusercontent.com/grambank/grambank/{GRAMBANK_COMMIT}/cldf"
_GL = f"https://raw.githubusercontent.com/glottolog/glottolog-cldf/{GLOTTOLOG_COMMIT}/cldf"

_CACHE = Path("pipeline/vendor/grambank")
_OUT = Path("config/grambank/features.json")

# The span-relevant subset. NOT all 195 — only features that say whether a relation is expressed as a
# FREE WORD (target needs an extra token) or as an AFFIX (it does not). Grouped by the source-side
# trigger they answer for, so the consuming rule reads straight off the spine.
FEATURES = {
    # finite verb (spine `person` populated) -> does the target need a free subject pronoun?
    # 0 on BOTH means the verb cannot index its subject, so the pronoun must be a separate word.
    "subject_indexing": ["GB089", "GB090"],          # S by suffix/enclitic, S by prefix/proclitic
    "agent_indexing": ["GB091", "GB092"],            # A argument, same question
    # construct/genitive -> is there morphological case, or must an adposition carry it ("son OF x")?
    # CORRECTION 2 (Step 1, internal-docs/aim1-typology-source-structure-plan.md §2.3/§4, 2026-09-24):
    # GB070/072 alone (morphological case) wrongly gated this OFF for eng/fra/por — all GB072=0 (no
    # case) yet all render a genitive with a directional adposition ("of"/"de", GB074=1) — the largest
    # Clear-gold languages never got the genitive-adposition trigger at all. Direction (GB074/075), not
    # morphological case, is the real precondition — added here under the SAME "any_one" polarity (fire
    # if EITHER a real case system OR a directional adposition exists); hin/arb (already GB070/072=1)
    # are unaffected, eng/fra/por (GB074=1, GB070/072=0) now correctly flag.
    "case_marking": ["GB070", "GB072", "GB074", "GB075"],  # core args, oblique NPs, OR a directional adposition
    # definite noun -> does the language spell out an article ("THE sons")?
    "articles": ["GB020", "GB021", "GB022", "GB023"],
    # possessed noun -> affixed possession, or a free possessive word ("HIS house")?
    "possession_affix": ["GB430", "GB431", "GB432", "GB433"],
    # tense/aspect/mood as an inflecting AUXILIARY WORD (extra token) vs marked on the verb (no token)
    "tam_auxiliary": ["GB119", "GB120", "GB121"],
    "tam_affix": ["GB082", "GB083", "GB084", "GB086", "GB312"],
    # DIRECTION pairs — not "does a free word exist" but "which side of the noun does it fall on", for
    # consuming code that needs to know which adjacent position to check when widening a name/noun span
    # (analyze_language.py's RISK_RULES flags THAT a category is worth checking; these say WHERE to look).
    # Confirmed predictive 2026-09-23: arb GB074=1/GB075=0 (preposition, extend backward), hin GB074=0/
    # GB075=1 (postposition, extend forward), eng GB022=1/GB023=0 (prenominal article, extend backward) —
    # all three matched the real gold-confirmed direction found by hand for each language.
    "adposition_order": ["GB074", "GB075"],          # [0]=preposition (before), [1]=postposition (after)
    "article_order": ["GB022", "GB023"],              # [0]=prenominal (before), [1]=postnominal (after)
    # Direct possessor/possessum order — unlike the pairs above, this is ONE ternary feature, not two
    # binary flags: GB065 codes 1 (possessor precedes possessum, e.g. English "his house"), 2 (possessor
    # follows, e.g. "house his"), or 3 (both orders occur/free) — needs its own direction helper, not
    # direction_for()'s before_id/after_id pair convention.
    "possession_order": ["GB065"],
    # Clause-level verb POSITION (GB131 verb-initial, GB133 verb-final; GB132 verb-medial is
    # deliberately excluded — SVO and OVS are both "medial" and put the subject on OPPOSITE sides of
    # the verb, so GB132 alone can never resolve subject_verb/object_verb order; a medial language
    # correctly gets neither GB131 nor GB133 = "1" and this pair resolves to None, not a guess).
    # These SAME two codes serve BOTH `subject_verb` and `object_verb` (typology.py, Step 2): a
    # verb-final clause puts subject AND object before the verb; verb-initial puts both after — one
    # pair, two slots. Registered for typology.py's LITERAL verb-position table entry only — kept
    # deliberately separate from the REJECTED "subject_order" proxy below (a DIFFERENT use: guessing
    # subject-PRONOUN position from verb order for span_extension's subject_indexing trigger, measured
    # a wash and never shipped). Using GB131/133 for what they actually encode is not the same claim.
    "subject_verb_order": ["GB133", "GB131"],   # [0]=verb-final (S before V), [1]=verb-initial (S after V)
    "object_verb_order": ["GB133", "GB131"],    # same codes — verb-final: O before V; verb-initial: O after V

    # Roadmap X1 (internal-docs/aim1-typology-source-structure-plan.md §4R, 2026-09-25): the remaining
    # free-word-existence facts the fertility prior (Step 3) and future span rules need — same "does
    # this relation cost the target an extra token" question as the groups above, just for relations
    # Step 3 doesn't yet gate on. Registered here (existence data, `any_one`-style polarity where
    # relevant) but NOT yet wired into fertility_priors.py/analyze_language.RISK_RULES — that wiring is
    # a separate, gold-measured decision per the Step 1.6/3 precedent, not automatic just because the
    # fact exists. All codes confirmed present in the vendored `grambank_parameters.csv` (2026-09-25).
    "plural_word": ["GB318"],                # free plural-marking element in the NP
    "negation_particle": ["GB298", "GB299"], # free negation: inflecting "auxiliary verb" / non-inflecting particle
    "voice_particle": ["GB302", "GB303"],    # free passive / antipassive marker
    "tam_particle": ["GB519", "GB520", "GB521"],  # free (non-inflecting) mood/aspect/tense particle —
                                                   # distinct from tam_auxiliary (GB119-121, an inflecting
                                                   # auxiliary VERB) and tam_affix (bound marking) above
    "copula": ["GB117"],                     # copula for predicate nominals
    "existential": ["GB126"],                # existential verb ("there is")
    "reflexive_reciprocal": ["GB305", "GB306"],  # phonologically independent reflexive / reciprocal pronoun
    "possessive_pronoun": ["GB313"],         # special adnominal possessive pronouns (not regular genitive)
    "comparative": ["GB276"],                # non-bound comparative degree marker
    "question_particle": ["GB262", "GB263", "GB264"],  # polar-interrogative particle: initial/final/other position
    "complementizer": ["GB421", "GB422"],    # preposed / postposed complementizer (verbs of thinking/knowing)
    # NP-order features: each code is its own multi-valued ORDER parameter (not a binary existence pair
    # like the direction groups above), so direction_for()'s before/after convention does not apply
    # as-is — registered for a future typology.py NP-order slot, not consumed anywhere yet.
    "np_order": ["GB024", "GB025", "GB193", "GB203"],  # numeral+N, demonstrative+N, property-word+N, quantifier+N
    "relative_clause_order": ["GB327", "GB328"],  # relative clause may follow / precede the noun
    "negation_clause_edge": ["GB137", "GB138"],   # standard negation marked clause-finally / clause-initially
    "verb_complexity": ["GB118", "GB122", "GB123", "GB158"],  # serial verbs, verb compounding, light-verb
                                                               # constructions, verb reduplication
    "agreement": ["GB170", "GB171", "GB172", "GB185", "GB186", "GB198"],  # gender/number agreement on
                                                                          # property word, demonstrative,
                                                                          # article, numeral
}
# TRIED 2026-09-24, not kept AS A SUBJECT-PRONOUN-POSITION PROXY: a "subject_order" direction (GB133
# verb-final=before, GB131 verb-initial=after — Grambank has no dedicated subject-pronoun-position or
# auxiliary-order feature; checked the full parameter list) derived a plausible before/after direction
# and matched textbook typology (hin SOV->before, arb VSO->after), but adding it to span_extension's
# subject_indexing category measured a WASH on real Clear gold (hin whole NT: F1 unchanged .627->.627,
# exact_span/precision slightly worse) — see span_extension.py's module docstring for the full
# comparison. NOT re-added there. `subject_verb_order`/`object_verb_order` above are a DIFFERENT,
# later (Step 2) use of the same two codes for their literal, undisputed meaning (clause-level
# subject/object-verb word order, not a subject-pronoun-drop proxy) — see typology.py.
_ALL = {f for fs in FEATURES.values() for f in fs}


def _fetch(url: str, dest: Path) -> Path:
    """Download once into the gitignored vendor cache; reuse if already there (pinned by commit, so a
    cached copy cannot drift)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
        fh.write(r.read())
    return dest


def build(cache: Path = _CACHE) -> tuple[dict, dict]:
    gl = _fetch(f"{_GL}/languages.csv", cache / "glottolog_languages.csv")
    gb_l = _fetch(f"{_GB}/languages.csv", cache / "grambank_languages.csv")
    gb_v = _fetch(f"{_GB}/values.csv", cache / "grambank_values.csv")

    # glottocode -> ISO 639-3 (Grambank's own ISO column is empty; this bridge is why Glottolog is pinned)
    g2iso = {r["ID"]: (r.get("ISO639P3code") or "").strip()
             for r in csv.DictReader(open(gl, encoding="utf-8"))}
    g2iso = {k: v for k, v in g2iso.items() if v}
    lang2iso = {}
    for r in csv.DictReader(open(gb_l, encoding="utf-8")):
        iso = g2iso.get((r.get("Glottocode") or "").strip())
        if iso:
            lang2iso[r["ID"]] = iso

    # ISO -> {feature: value}. A language may map from several varieties; keep the first non-'?' value
    # and count disagreements rather than silently picking one.
    out: dict[str, dict] = collections.defaultdict(dict)
    conflicts: collections.Counter = collections.Counter()
    for r in csv.DictReader(open(gb_v, encoding="utf-8")):
        fid = r.get("Parameter_ID")
        if fid not in _ALL:
            continue
        iso = lang2iso.get(r.get("Language_ID"))
        if not iso:
            continue
        val = (r.get("Value") or "").strip()
        if val in ("", "?"):
            continue
        prev = out[iso].get(fid)
        if prev is None:
            out[iso][fid] = val
        elif prev != val:
            conflicts[iso] += 1
    stats = {"languages": len(out), "features": len(_ALL),
             "varieties_mapped": len(lang2iso), "iso_with_conflicts": len(conflicts)}
    return dict(out), stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--cache", type=Path, default=_CACHE)
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()
    if not args.build:
        ap.error("pass --build")

    feats, st = build(args.cache)
    doc = {"_doc": __doc__.strip().split("\n\n")[0],
           "_pins": {"grambank": GRAMBANK_COMMIT, "glottolog": GLOTTOLOG_COMMIT,
                     "license": "CC-BY-4.0 (both)"},
           "_features": FEATURES,
           "_coverage": st,
           "languages": feats}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[grambank_fetch] {st['languages']} ISO codes x {st['features']} features "
          f"({st['varieties_mapped']} Grambank varieties mapped; "
          f"{st['iso_with_conflicts']} ISOs had multi-variety disagreements) → {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
