"""Derived fact `possessive_bound`: does the target language mark a pronominal possessor ON the possessed noun (an affix: Arabic بيتُهُ,
Finnish talonsa, Turkish evi, Hungarian háza), rather than with a separate word (his house, sa maison, उसका घर)?
Plan internal-docs/grammar-word-level-plan-2026-10-08.md, D-1.

Same method as `article_bound` (and the same functions: `article_bound.affix_lifts`, `paradigm_stat`), with a different SOURCE condition:
a content noun "has a pronominal possessor" when

  * Hebrew: its own word carries a pronominal suffix (a later non-content morpheme of the same word, by MACULA key, with `person` set:
    אִשְׁתּ + וֹ "his wife"), or
  * Greek: the next one or two tokens are a genitive personal pronoun (αὐτοῦ, μου, σου, ἡμῶν, ὑμῶν: grc:0846 / grc:1473 / grc:4771 with
    case genitive).

Rows are SINGULAR nouns (number confound, see source_possessor_index) whose base alignment (eflomal, then gloss) is ONE target word. An affix on the possessed noun shows up as an edge string that is
far more frequent with a possessor than without; the paradigm statistic compares each lexeme's most frequent form with and without.
Reading the alignment of the pronoun itself is avoided on purpose: function-word alignments are unreliable (word_checks C2: a suffix pronoun
linked more than one word away from its noun is wrong ~98% of the time, and negator alignments failed validation, see negation_order).

`bound` True / False / None (too few nouns). Like article_bound, False means "no bound possessor detected", not "free word"; validated
against Grambank GB431/GB433 (possession marked by a prefix/suffix on the possessed noun) with `--validate`.

MEASURED 2026-10-08 (one edition per language; results in pipeline/work/logs/{possessive_bound,plural_affix}_[012].json): NOT ADMITTED.
  * possessor, 604 languages with Grambank GB431/GB433: within-lexeme lift, threshold fitted on a random half (0.05), held-out 69.5%
    (base rate 52.8%); paradigm share 55.4%. Disagreements are largely ORTHOGRAPHIC: Coptic ⲡⲉϥ- possessive articles, Sorani clitics,
    Breton mutation read "on the noun"; Chuj/Aymara possessive affixes are missed. The detector says "written together", Grambank says
    "morphological affix" — useful for an alignment veto, not as a typological fact.
  * plural (--condition plural), 407 languages with WALS 33A affix/stem-change vs word/clitic/none: held-out 74.5% (base 64.9%).
  Arabic and Finnish come out clearly bound (lift .27/.23); eng/fra/spa/hin clearly not (.013-.030) once the lift is computed within lexemes
  (the cross-lexeme lift called eng/spa "bound" through kinship-term endings and plural confounds). Not written to gram_struct.

    .venv/bin/python -m lexeme_aligner.possessive_bound --tag arb_vdv --iso arb
    .venv/bin/python -m lexeme_aligner.possessive_bound --list jobs.json --out FILE ; --validate --out FILE
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lexeme_aligner import article_bound as ab
from lexeme_aligner.config import OUT, PRIOR_PACK

GREEK_POSSESSIVE = {"grc:0846", "grc:1473", "grc:4771"}
OUT_FILE = Path("config/gram_struct/possessive_bound.json")


def source_possessor_index(books: list[str]) -> dict[tuple[int, int], bool]:
    """{(ref, idx): the content token has a pronominal possessor} from the spine (edition independent)."""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    heb = HebrewSource()
    out: dict[tuple[int, int], bool] = {}
    for book in books:
        for ch in heb.chapters(book):
            for v in heb.verses(book, ch):
                toks = sorted(heb.verse_tokens(book, ch, v), key=lambda t: t.idx)
                ref = encode(book, ch, v)
                for k, t in enumerate(toks):
                    # singular nouns only: possessed nouns are more often plural than bare ones in the source ("his sons"), and a
                    # plural ending would otherwise pass for a possessive affix (first run: eng "-s", spa "-s" came out "bound")
                    if not (t.is_content and t.strong) or getattr(t, "number", None) != "singular":
                        continue
                    out[(ref, t.idx)] = has_possessor(toks, k)
    return out


def source_plural_index(books: list[str]) -> dict[tuple[int, int], bool]:
    """{(ref, idx): the content noun is plural} for singular and plural nouns (dual and unmarked tokens left out) — the condition for the
    `plural_affix` variant (`--condition plural`): does the target mark plural on the noun itself?"""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    heb = HebrewSource()
    out: dict[tuple[int, int], bool] = {}
    for book in books:
        for ch in heb.chapters(book):
            for v in heb.verses(book, ch):
                ref = encode(book, ch, v)
                for t in heb.verse_tokens(book, ch, v):
                    num = getattr(t, "number", None)
                    if t.is_content and t.strong and num in ("singular", "plural"):
                        out[(ref, t.idx)] = num == "plural"
    return out


def has_possessor(toks: list, k: int) -> bool:
    t = toks[k]
    key = (t.keys or [""])[0]
    if len(key) == 12:                                                   # Hebrew: a suffix morpheme of the same word
        word = key[:-1]
        for u in toks[k + 1:k + 4]:
            ukey = (u.keys or [""])[0]
            if ukey[:-1] != word:
                break
            if not u.is_content and getattr(u, "person", None):
                return True
        return False
    for u in toks[k + 1:k + 3]:                                          # Greek: a following genitive personal pronoun
        if u.lexeme in GREEK_POSSESSIVE and getattr(u, "case_", None) == "genitive":
            return True
    return False


def stratified_lift(rows: list[tuple[str, bool, str]], lengths=(1, 2, 3), min_each: int = 3, top: int = 40) -> dict | None:
    """The best edge affix by WITHIN-LEXEME lift: for every lexeme seen >= `min_each` times with and without a possessor, the difference
    P(affix | possessed) - P(affix | plain); averaged over lexemes, weighted by the smaller count. Cross-lexeme lift is confounded by WHICH
    nouns get possessors (kinship terms: eng/spa "-r"/"-re" came out "bound"), the within-lexeme contrast is not."""
    import collections
    by_lex: dict[str, list[list[str]]] = collections.defaultdict(lambda: [[], []])
    for w, poss, lx in rows:
        by_lex[lx][0 if poss else 1].append(w)
    lexes = [(lx, a, b) for lx, (a, b) in by_lex.items() if len(a) >= min_each and len(b) >= min_each]
    if not lexes:
        return None
    cand: collections.Counter = collections.Counter()
    for _lx, a, _b in lexes:
        for w in a:
            for L in lengths:
                if len(w) >= L + 2:
                    cand[("suffix", w[-L:])] += 1
                    cand[("prefix", w[:L])] += 1
    best = None
    for (edge, aff), _n in cand.most_common(top):
        num = den = 0.0
        for _lx, a, b in lexes:
            has = (lambda w: w.endswith(aff)) if edge == "suffix" else (lambda w: w.startswith(aff))      # noqa: E731
            wgt = min(len(a), len(b))
            num += wgt * (sum(map(has, a)) / len(a) - sum(map(has, b)) / len(b))
            den += wgt
        lift = num / den if den else 0.0
        if best is None or lift > best["lift"]:
            best = {"edge": edge, "affix": aff, "lift": round(lift, 4), "lexemes": len(lexes)}
    return best


def slot(rows: list[tuple[str, bool, str]]) -> dict | None:
    """article_bound's decision rule on possessor-conditioned rows (same thresholds: they were not re-fitted here)."""
    s = ab.article_bound_slot(rows)
    if s is None:
        return None
    st = stratified_lift(rows)
    return {"bound": s["bound"], "source": "derived", "n_possessed": s["n_article"], "n_plain": s["n_anarthrous"],
            "affix_lift": s["affix_lift"], "paradigm": s["paradigm"], "via": s["via"], "stratified": st,
            **({"edge": s["edge"], "marker": s["marker"]} if s["bound"] else {})}


def grambank_reference(path: Path = Path("config/grambank/features.json")) -> dict[str, bool]:
    """{iso: possession marked by an affix on the possessed noun (GB431 or GB433 == 1)} where both are known."""
    doc = json.loads(path.read_text(encoding="utf-8")).get("languages", {})
    out = {}
    for iso, f in doc.items():
        a, b = f.get("GB431"), f.get("GB433")
        if a in ("0", "1") and b in ("0", "1"):
            out[iso] = a == "1" or b == "1"
    return out


def wals_plural_reference(wals_dir: Path = Path("pipeline/vendor/wals")) -> dict[str, bool]:
    """{iso: WALS 33A says plural is a prefix, suffix or stem change on the noun} (True) vs a plural word/clitic or no plural (False)."""
    import csv
    iso_of = {r["ID"]: r.get("ISO639P3code") for r in csv.DictReader(open(wals_dir / "languages.csv", encoding="utf-8"))}
    bound = {"1", "2", "3"}                    # 33A-1 prefix, -2 suffix, -3 stem change
    free = {"7", "8", "9"}                     # plural word, plural clitic, no plural
    out = {}
    for r in csv.DictReader(open(wals_dir / "values.csv", encoding="utf-8")):
        if r["Parameter_ID"] == "33A" and iso_of.get(r["Language_ID"]) and (r["Value"] in bound or r["Value"] in free):
            out[iso_of[r["Language_ID"]]] = r["Value"] in bound
    return out


def validate(doc: dict, ref: dict[str, bool]) -> dict:
    tp = fp = tn = fn = 0
    mism = []
    for iso, e in doc.items():
        if iso not in ref or not isinstance(e, dict) or e.get("bound") is None:
            continue
        ours, theirs = bool(e["bound"]), ref[iso]
        tp += ours and theirs; fp += ours and not theirs; tn += (not ours) and (not theirs); fn += (not ours) and theirs
        if ours != theirs and len(mism) < 25:
            mism.append((iso, ours, theirs, (e.get("affix_lift") or {}).get("affix")))
    n = tp + fp + tn + fn
    return {"n": n, "agreement": round((tp + tn) / n, 4) if n else None, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None, "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "mismatches": mism}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso"); ap.add_argument("--tag", action="append")
    ap.add_argument("--list", type=Path, help="JSON [[iso, [tag, ...]], ...]")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    ap.add_argument("--aligner-out", type=Path, default=OUT)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--redo", action="store_true", help="recompute languages already in --out")
    ap.add_argument("--condition", choices=("possessor", "plural"), default="possessor",
                    help="possessor (default): affix on the possessed noun; plural: plural marked on the noun itself")
    a = ap.parse_args(argv)
    doc = json.loads(a.out.read_text(encoding="utf-8")) if a.out.exists() else {}
    jobs = [(a.iso, a.tag)] if a.iso and a.tag else []
    if a.list:
        jobs += [(iso, tags) for iso, tags in json.loads(a.list.read_text(encoding="utf-8"))]
    if jobs:
        from lexeme_aligner.compact_align import ALL_BOOKS
        from lexeme_aligner.span_extension import load_priors
        lex_pos, _ = load_priors(PRIOR_PACK)
        idx = (source_plural_index if a.condition == "plural" else source_possessor_index)(list(ALL_BOOKS))
        for iso, tags in jobs:
            if iso in doc and not a.redo:
                continue
            slots = {}
            for tag in tags:
                s = slot(ab.collect_nouns(tag, lex_pos, idx, a.aligner_out))
                if s is not None:
                    slots[tag] = dict(s, n_article=s["n_possessed"])        # combine_editions weighs by n_article
            from lexeme_aligner.edition_vote import combine_editions
            comb = combine_editions(slots, field="bound", weight_key="n_article") if slots else None
            best = max(slots.values(), key=lambda s: s["n_possessed"]) if slots else None
            doc[iso] = {**(best or {}), **({"bound": comb.get("bound"), "agreement": comb.get("agreement")} if comb else {"bound": None}),
                        "editions": {t: s["bound"] for t, s in slots.items()}, "source": "derived"}
            print(f"[possessive_bound] {iso}: {doc[iso].get('bound')} {(doc[iso].get('stratified') or {}).get('affix')}", file=sys.stderr, flush=True)
            a.out.parent.mkdir(parents=True, exist_ok=True)              # after every language: a long sweep can be resumed / read early
            a.out.write_text(json.dumps(dict(sorted(doc.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.validate:
        print(json.dumps(validate(doc, grambank_reference() if a.condition == "possessor" else wals_plural_reference()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
