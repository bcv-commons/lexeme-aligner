"""Derived fact: where does a language put its standard negator relative to the verb? (plan internal-docs/grammar-word-level-plan-2026-10-08.md, D-3)

The 2026-09-25 D1 negation selector looked at target words ADJACENT to a negator-shaped word and failed its known-answer check. This one
reads the ALIGNMENT instead: for every source negator (Hebrew לֹא/אַל/בַּל, Greek οὐ/μή) whose clause verb is also aligned, compare the
negator's target positions with the verb's:

    fused       the negator and the verb share a target word (a negative affix, or a verb form that carries the negation)
    before      the negator's target is a separate word before the verb's target
    after       ... after it
    unaligned   the negator has no target although its verb has one (bound negation that the aligner gave to the verb alone, or a
                construction the translation does not render with a word)

The clause verb is the negator's `head_idx` (MACULA Hebrew: the token's clause verb) or, for Greek and where head_idx is missing, the next
verb token within four tokens (`mood` set). Per edition the counts are kept; a language's verdict pools its editions' counts.

    direction  "before" | "after" when one side holds >= 80% of the separate-word cases (n >= 50), else "mixed"; null below n = 50
    fused_share, unaligned_share  shares of all negators whose verb is aligned

Validated against WALS 143A (Order of Negative Morpheme and Verb: NegV / VNeg / [Neg-V] / [V-Neg]) with `--validate`, and checked against
known answers (eng spa hin arb cmn rus fin: before). Output: config/gram_struct/negation_order.json (gitignored, CC0, derived).

MEASURED 2026-10-08 (297 languages with a WALS 143A value, one edition each; pipeline/work/logs/negation_order_all.json): NOT ADMITTED.
  * direction: 84.0% agreement with WALS NegV/VNeg (n=75 resolved) — below the 85% floor of the admission rule; only 4 of 297 languages
    come out "after" although the sample holds 63 VNeg languages, and 181 are "mixed": the aligner places a source negator on the target
    word in SOURCE order far too often (function-word alignment bias; Sango's clause-final "ape" reads "before" 86%). Known answers: 6 match,
    hin abstains (mixed), 0 violations.
  * bound vs free: fused+unaligned share averages 0.34 in WALS affix languages vs 0.11 in word languages; a threshold fitted on a random
    half (0.24) classifies the other half at 80.4% (base rate 52%) — informative, still below 85%. Not written to gram_struct.

    .venv/bin/python -m lexeme_aligner.negation_order --tag bsb --iso eng --usj-dir pipeline/work/ingest-cache/usj-engbsb
    .venv/bin/python -m lexeme_aligner.negation_order --validate
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

NEGATORS = {"H3808", "H0408", "H1077", "G3756", "G3361"}            # לֹא אַל בַּל · οὐ μή (Strong's rollup)
OUT_FILE = Path("config/gram_struct/negation_order.json")
KNOWN_ANSWERS = {"eng": "before", "spa": "before", "hin": "before", "arb": "before", "cmn": "before", "rus": "before", "fin": "before"}
MIN_N = 50
SIDE_SHARE = 0.80
_WALS = Path("pipeline/vendor/wals")


def _norm_strong(t) -> str | None:
    s = getattr(t, "strong", None)
    if s is None:
        return None
    s = str(s)
    if s[:1] in ("H", "G"):
        return s
    return None


def _strong_key(t, testament: str) -> str | None:
    s = _norm_strong(t)
    if s:
        return s
    raw = getattr(t, "strong", None)
    if raw is None:
        return None
    try:
        return f"{'H' if testament == 'OT' else 'G'}{int(raw):04d}"
    except (TypeError, ValueError):
        return None


def verse_counts(heb: list, aligned: dict[int, list[int]], testament: str, counts: collections.Counter) -> None:
    """Add one verse's negator observations to `counts` (keys: fused / before / after / unaligned / no_verb)."""
    toks = sorted(heb, key=lambda t: t.idx)
    by_idx = {t.idx: t for t in toks}
    for k, t in enumerate(toks):
        if _strong_key(t, testament) not in NEGATORS:
            continue
        verb = by_idx.get(getattr(t, "head_idx", None)) if getattr(t, "head_idx", None) is not None else None
        if verb is None:
            verb = next((u for u in toks[k + 1:k + 5] if getattr(u, "mood", None) and u.is_content), None)
        if verb is None or verb.idx not in aligned:
            counts["no_verb"] += 1
            continue
        v = aligned[verb.idx]
        n = aligned.get(t.idx)
        if not n:
            counts["unaligned"] += 1
        elif set(n) & set(v):
            counts["fused"] += 1
        elif max(n) < min(v):
            counts["before"] += 1
        elif min(n) > max(v):
            counts["after"] += 1
        else:
            counts["interleaved"] += 1


def verdict(counts: collections.Counter) -> dict:
    sep = counts["before"] + counts["after"]
    judged = sep + counts["fused"] + counts["unaligned"] + counts["interleaved"]
    out = {"n": judged, "n_separate": sep, "counts": dict(counts),
           "fused_share": round(counts["fused"] / judged, 4) if judged else None,
           "unaligned_share": round(counts["unaligned"] / judged, 4) if judged else None,
           "rate_after": round(counts["after"] / sep, 4) if sep else None}
    if sep < MIN_N:
        out["direction"], out["reason"] = None, "insufficient_n"
    elif counts["before"] / sep >= SIDE_SHARE:
        out["direction"] = "before"
    elif counts["after"] / sep >= SIDE_SHARE:
        out["direction"] = "after"
    else:
        out["direction"] = "mixed"
    return out


def edition_counts(tag: str, usj_dir: Path, books: list[str], methods=("eflomal", "gloss"), heb=None) -> collections.Counter:
    """Counts for one edition, from its own eflomal(+gloss) files only (the circularity rule: never spanext/gapfill output)."""
    from lexeme_aligner.config import OUT
    from lexeme_aligner.eval.word_checks import load_alignment
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import BOOK_NUMBERS, encode
    from lexeme_aligner.run_pilot import OT_BOOKS, build_corpus
    from lexeme_aligner.versification import remapper
    heb = heb or HebrewSource()
    recs = build_corpus(books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    ali = load_alignment(tag, tuple(methods), OUT, {BOOK_NUMBERS[b] for b in books})
    counts: collections.Counter = collections.Counter()
    ot = set(OT_BOOKS)
    for r in recs:
        aligned = ali.get(encode(r.book, r.ch, r.v))
        if aligned:
            verse_counts(r.heb, aligned, "OT" if r.book in ot else "NT", counts)
    return counts


def wals_143a(wals_dir: Path = _WALS) -> dict[str, str]:
    """{iso: before|after|prefix|suffix|other} from WALS 143A."""
    iso_of = {r["ID"]: r.get("ISO639P3code") for r in csv.DictReader(open(wals_dir / "languages.csv", encoding="utf-8"))}
    m = {"1": "before", "2": "after", "3": "prefix", "4": "suffix"}
    out = {}
    for r in csv.DictReader(open(wals_dir / "values.csv", encoding="utf-8")):
        if r["Parameter_ID"] == "143A" and iso_of.get(r["Language_ID"]):
            out[iso_of[r["Language_ID"]]] = m.get(r["Value"], "other")
    return out


def validate(doc: dict, wals: dict[str, str]) -> dict:
    """Agreement of our separate-word direction with WALS NegV/VNeg, and how our fused/unaligned shares look for WALS affix languages."""
    agree = n = 0
    fused_affix, fused_word = [], []
    mism = []
    for iso, e in doc.items():
        w = wals.get(iso)
        if not w or not isinstance(e, dict):
            continue
        if w in ("prefix", "suffix") and e.get("fused_share") is not None:
            fused_affix.append(e["fused_share"] + (e.get("unaligned_share") or 0))
        if w in ("before", "after"):
            if e.get("fused_share") is not None:
                fused_word.append(e["fused_share"] + (e.get("unaligned_share") or 0))
            if e.get("direction") in ("before", "after"):
                n += 1
                agree += e["direction"] == w
                if e["direction"] != w:
                    mism.append((iso, e["direction"], w))
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None        # noqa: E731
    return {"direction_agreement": round(agree / n, 4) if n else None, "n": n, "mismatches": mism[:20],
            "fused_or_unaligned_mean_wals_affix": mean(fused_affix), "n_affix": len(fused_affix),
            "fused_or_unaligned_mean_wals_word": mean(fused_word), "n_word": len(fused_word)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso"); ap.add_argument("--tag", action="append", help="edition tag(s) of --iso (repeatable; counts are pooled)")
    ap.add_argument("--usj-dir", action="append", type=Path, help="one per --tag, same order")
    ap.add_argument("--list", type=Path, help="JSON [[iso, [[tag, usj_dir], ...]], ...] to process in one run")
    ap.add_argument("--validate", action="store_true", help=f"compare {OUT_FILE} with WALS 143A and the known answers")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    a = ap.parse_args(argv)
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    doc = json.loads(a.out.read_text(encoding="utf-8")) if a.out.exists() else {}
    jobs = []
    if a.iso and a.tag:
        jobs.append((a.iso, list(zip(a.tag, a.usj_dir or []))))
    if a.list:
        jobs += [(iso, [(t, Path(u)) for t, u in eds]) for iso, eds in json.loads(a.list.read_text(encoding="utf-8"))]
    if jobs:
        from lexeme_aligner.hebrew_source import HebrewSource
        heb = HebrewSource()
        for iso, eds in jobs:
            pooled: collections.Counter = collections.Counter()
            per_ed = {}
            for tag, usj in eds:
                c = edition_counts(tag, Path(usj), OT_BOOKS + NT_BOOKS, heb=heb)
                per_ed[tag] = verdict(c)
                pooled.update(c)
            doc[iso] = {**verdict(pooled), "editions": per_ed, "source": "derived"}
            print(f"[negation_order] {iso}: {doc[iso]['direction']} (n_sep {doc[iso]['n_separate']}, fused {doc[iso]['fused_share']}, "
                  f"unaligned {doc[iso]['unaligned_share']})", file=sys.stderr, flush=True)
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(dict(sorted(doc.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.validate:
        ka = {iso: (doc.get(iso, {}).get("direction"), want) for iso, want in KNOWN_ANSWERS.items() if iso in doc}
        res = {"known_answers": ka, "violations": [i for i, (got, want) in ka.items() if got in ("before", "after") and got != want],
               "wals_143a": validate(doc, wals_143a())}
        print(json.dumps(res, indent=1))
        return 1 if res["violations"] else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
