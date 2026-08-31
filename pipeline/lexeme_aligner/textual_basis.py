"""Which Greek textual tradition does a target edition follow? — the gate for second-spine alignment.

Why: we now hold three NT spines (MACULA/Nestle1904, Robinson-Pierpont 2018, Textus Receptus) and they
are NOT near-identical — only 58% of shared verses carry the same Strong's multiset, and word ORDER
differs in a further 4,329. Aligning an edition against a source text its translators did not follow is
wrong exactly at the variant points (and degrades gap-fill, whose positional prior interpolates target
position from SOURCE order), so a second alignment should be produced only where it is warranted.

Method — presence of the "missing verses": whole verses that the Byzantine/TR tradition carries and the
critical text omits entirely. Counting them is far more robust than measuring verse LENGTH (an earlier
attempt): it is binary per verse, immune to a translation's verbosity, and needs no alignment — just the
ingested target text. Measured against the spines themselves: NA carries 1/16, RP2018 12/16, TR 16/16,
and four of them (LUK 17:36, ACT 8:37, ACT 15:34, ACT 24:7) are TR-only, which is what separates TR from
Byzantine.

CAVEAT — this detects which READINGS an edition contains, not what its translators worked from. Most of
the catalogue is minority-language translation made from a pivot/gateway language rather than from Greek,
so a Byzantine verdict often means "inherited Byzantine readings via its source translation". That is
still exactly the right signal for choosing an alignment spine. Editions that mark such verses as
bracketed variants rather than adopting them are reported as `mixed` and left for a human.

    python3 -m lexeme_aligner.textual_basis --scan --out config/textual_basis.json
    python3 -m lexeme_aligner.textual_basis --iso ind          # one language, verbose
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
from lexeme_aligner.usj_source import read_verses

_INGEST = Path("pipeline/work/ingest-cache")

# (book, chapter, verse, tr_only) — tr_only marks the four that separate TR from Byzantine.
DIAGNOSTIC_VERSES = [
    ("MAT", 17, 21, False), ("MAT", 18, 11, False), ("MAT", 23, 14, False),
    ("MRK", 7, 16, False), ("MRK", 9, 44, False), ("MRK", 9, 46, False),
    ("MRK", 11, 26, False), ("MRK", 15, 28, False),
    ("LUK", 17, 36, True), ("LUK", 23, 17, False),
    ("ACT", 8, 37, True), ("ACT", 15, 34, True), ("ACT", 24, 7, True),
    ("ACT", 28, 29, False), ("ROM", 16, 24, False),
]
# JHN 5:4 is deliberately excluded — MACULA's Nestle1904 spine carries it too, so it cannot discriminate.

_BRACKETED = re.compile(r"^\s*[\[\(【]")     # an edition marking the reading as a variant, not adopting it


def _verse_text(usj_dir: Path, book: str, chapter: int, verse: int) -> str | None:
    """The edition's text for one verse, or None if the edition simply has no such verse."""
    fp = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    if not fp.exists():
        return None
    verses = read_verses(fp)
    for (ch, v), text in verses.items():
        if ch == chapter and v == verse:
            return text
    return None


def classify(usj_dir: Path) -> dict:
    """Probe the diagnostic verses and return counts + a verdict.

    `present` counts verses carried as real text; `bracketed` counts those the edition includes but marks
    as a variant. A verse whose BOOK was never ingested is not counted either way — `checked` records how
    many were actually testable, so a partial NT is not mistaken for a critical text."""
    present = bracketed = checked = tr_present = tr_checked = 0
    hits: list[str] = []
    for book, ch, v, tr_only in DIAGNOSTIC_VERSES:
        if not (usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json").exists():
            continue
        checked += 1
        if tr_only:
            tr_checked += 1
        text = _verse_text(usj_dir, book, ch, v)
        if not text or not text.strip():
            continue
        if _BRACKETED.match(text):
            bracketed += 1
            continue
        present += 1
        hits.append(f"{book} {ch}:{v}")
        if tr_only:
            tr_present += 1

    verdict = "indeterminate"
    if checked >= 8:
        rate = present / checked
        if bracketed >= 3 and rate < 0.5:
            verdict = "mixed"                       # includes them, but marked as variants
        elif rate < 0.2:
            verdict = "critical"                    # NA/UBS-style — our default spine already fits
        elif tr_checked and tr_present / tr_checked >= 0.5:
            verdict = "tr"                          # carries the TR-only verses
        elif rate >= 0.6:
            verdict = "byzantine"                   # Byzantine readings, but not the TR-only ones
        else:
            verdict = "mixed"
    return {"verdict": verdict, "present": present, "bracketed": bracketed, "checked": checked,
            "tr_only_present": tr_present, "tr_only_checked": tr_checked, "verses": hits}


def spine_for(verdict: str) -> str | None:
    """The SECOND spine warranted by a verdict, or None when our default spine already fits."""
    return {"byzantine": "rp2018", "tr": "tr"}.get(verdict)


def _edition_dirs(ingest: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = collections.defaultdict(list)
    for p in ingest.glob("usj-*"):
        if p.is_dir():
            out[p.name[4:7]].append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="classify every ingested edition")
    ap.add_argument("--iso", help="classify just this language's editions, verbosely")
    ap.add_argument("--ingest", type=Path, default=_INGEST)
    ap.add_argument("--out", type=Path, default=Path("config/textual_basis.json"))
    args = ap.parse_args()

    by_iso = _edition_dirs(args.ingest)
    if args.iso:
        for d in sorted(by_iso.get(args.iso, [])):
            r = classify(d)
            print(f"{d.name}: {r['verdict']}  present={r['present']}/{r['checked']} "
                  f"tr_only={r['tr_only_present']}/{r['tr_only_checked']} bracketed={r['bracketed']}")
            if r["verses"]:
                print(f"    carries: {', '.join(r['verses'])}")
        return 0
    if not args.scan:
        ap.error("pass --scan or --iso")

    editions: dict[str, dict] = {}
    counts: collections.Counter = collections.Counter()
    for iso in sorted(by_iso):
        for d in sorted(by_iso[iso]):
            r = classify(d)
            counts[r["verdict"]] += 1
            editions[d.name[4:]] = {"iso": iso, **{k: r[k] for k in
                                                   ("verdict", "present", "checked", "bracketed",
                                                    "tr_only_present")},
                                    "second_spine": spine_for(r["verdict"])}
    doc = {"_doc": __doc__.strip().split("\n\n")[0],
           "diagnostic_verses": [f"{b} {c}:{v}" + (" (TR-only)" if t else "")
                                 for b, c, v, t in DIAGNOSTIC_VERSES],
           "verdict_counts": dict(counts.most_common()), "editions": editions}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"[textual_basis] {len(editions)} editions → {args.out}", file=sys.stderr)
    for k, n in counts.most_common():
        print(f"    {k:15s} {n:5d}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
