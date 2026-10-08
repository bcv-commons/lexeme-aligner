"""How does an edition treat the psalm titles (superscriptions)? Decided from the edition's own structure, not from alignments.

Hebrew verse 1 of 116 psalms is (or starts with) a title. Editions differ:
  numbered  the scheme counts the title as its own verse (PSA 3 has 9 verses in org, orgw, catm, rso, lxx, vul; 8 in eng). The
            `.vrs` fingerprint of the edition says so; its title words sit in verse 1 of the text.
  heading   an `eng`-scheme edition that prints the title as an unnumbered heading, or leaves it out (BSB, NET, hinirv ...):
            the title words are NOT in the verse text.
  inline    an `eng`-scheme edition that prints the title at the start of verse 1 without numbering it (eng_ylt, spa_r09).
  unknown   too close to call, or no usable text.

The `.vrs` counts can only separate `numbered` from the `eng` scheme; inside `eng` the verse text decides: for the psalms whose
spine verse 1 holds a title AND body, compare words-per-source-token in verse 1 of the edition with the same ratio over the
other verses of those psalms. A heading edition gives about 1.0; an inline title adds its words to verse 1 and pushes the ratio
up (YLT 1.22, Reina-Valera 1.17). Measured over 403 `eng`-scheme editions: 308 at or below 1.04, 58 above 1.12, 37 in between
(2026-10-07). Checked against helloAO's own `hebrew_subtitle` block on 27 editions: no contradiction.

    .venv/bin/python -m lexeme_aligner.psalm_titles --tag engbsb
    .venv/bin/python -m lexeme_aligner.psalm_titles --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HEADING_MAX = 1.04
INLINE_MIN = 1.12
MIN_BODY_TOKENS = 3                     # a psalm counts as "title + body in verse 1" with at least this many non-title tokens
NUMBERED_SCHEMES = frozenset({"org", "orgw", "catm", "rso", "lxx", "vul"})
_INGEST = Path("pipeline/work/ingest-cache")


def classify_ratio(ratio: float | None) -> str:
    """heading / inline / unknown from the verse-1 length ratio."""
    if ratio is None:
        return "unknown"
    if ratio <= HEADING_MAX:
        return "heading"
    if ratio > INLINE_MIN:
        return "inline"
    return "unknown"


def embedded_title_psalms(heb) -> list[tuple[int, int]]:
    """[(chapter, body token count)] for the psalms whose spine verse 1 holds title tokens AND a body."""
    out = []
    for ch in heb.chapters("PSA"):
        toks = heb.verse_tokens("PSA", ch, 1)
        sup = [t for t in toks if t.is_superscription]
        body = [t for t in toks if not t.is_superscription]
        if sup and len(body) >= MIN_BODY_TOKENS:
            out.append((ch, len(body)))
    return out


def length_ratio(heb, verses: dict, remap) -> float | None:
    """(words per body token in verse 1) / (words per source token in the other verses), over the embedded-title psalms."""
    from lexeme_aligner.usj_source import tokenize
    t1 = b1 = to = bo = 0
    for ch, nbody in embedded_title_psalms(heb):
        _, tc, tv = remap("PSA", ch, 1) if remap else ("PSA", ch, 1)
        if tv == 0 or (tc, tv) not in verses:
            continue
        t1 += len(tokenize(verses[(tc, tv)]["text"]))
        b1 += nbody
        for v in heb.verses("PSA", ch):
            if v == 1:
                continue
            _, c2, v2 = remap("PSA", ch, v) if remap else ("PSA", ch, v)
            if v2 and (c2, v2) in verses:
                to += len(tokenize(verses[(c2, v2)]["text"]))
                bo += len(heb.verse_tokens("PSA", ch, v))
    if not b1 or not bo or not to:
        return None
    return (t1 / b1) / (to / bo)


def title_mode(tag: str, usj_dir: Path | None = None, heb=None) -> dict:
    """{"mode": numbered|heading|inline|unknown, "ratio": float|None, "scheme": label} for one edition."""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.usj_source import read_verse_ranges
    from lexeme_aligner.versification import edition_scheme, remapper
    usj_dir = Path(usj_dir or _INGEST / f"usj-{tag}")
    scheme = edition_scheme(tag, str(usj_dir))
    if scheme in NUMBERED_SCHEMES:
        return {"mode": "numbered", "ratio": None, "scheme": scheme}
    fp = usj_dir / "19-PSA.json"
    if scheme != "eng" or not fp.exists():
        return {"mode": "unknown", "ratio": None, "scheme": scheme}
    heb = heb or HebrewSource()
    ratio = length_ratio(heb, read_verse_ranges(fp, rules={}), remapper(tag, str(usj_dir)))
    return {"mode": classify_ratio(ratio), "ratio": None if ratio is None else round(ratio, 3), "scheme": scheme}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag")
    ap.add_argument("--all", action="store_true", help="every edition in the ingest cache that has Psalms")
    ap.add_argument("--out", type=Path, help="with --all: write {tag: result} here")
    a = ap.parse_args(argv)
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = HebrewSource()
    if a.tag:
        print(json.dumps(title_mode(a.tag, heb=heb)))
        return 0
    if not a.all:
        ap.error("pass --tag or --all")
    res = {}
    for d in sorted(_INGEST.glob("usj-*")):
        if (d / "19-PSA.json").exists() and ".baseline" not in d.name:
            res[d.name[4:]] = title_mode(d.name[4:], d, heb)
    counts: dict[str, int] = {}
    for r in res.values():
        counts[r["mode"]] = counts.get(r["mode"], 0) + 1
    print(json.dumps(counts), file=sys.stderr)
    if a.out:
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
