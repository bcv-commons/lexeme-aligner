"""Text fingerprints for compact-alignments: a hash of a book's TOKENS and verse numbers, which any client can
compute from the text it fetched (plan: internal-docs/compact-text-fingerprint-plan-2026-10-12.md, section 2).

Compact positions index the tokens of a verse, so the fingerprint covers exactly what positions depend on and
nothing else (whitespace, punctuation, line breaks and digits do not change it):

    verse line  = "{verse}:{tokens joined by one space}"          (verses with no tokens are left out)
    chapter fp  = sha256( verse lines of the chapter joined by "\\n" )
    book fp     = sha256( "{chapter}:{chapter fp}" lines joined by "\\n" )
    file name   = <BOOK>_<first 8 hex of book fp>.json

Verses come from the edition's own text in the edition's own numbering (raw text: strip rules do not apply), by the
verse-text rule bcv-commons/bibles owns (tools/verse-text/js/rule.js; Python twin vendored as pipeline/vendor/bibles/
verse_text.py). usj_source's walker mirrors it and is tested against it (tests/test_verse_text_parity.py). Tokens =
usj_source.tokenize (TOKENIZER_VERSION).

Ownership of the tokenizer and the fingerprint is moving to bibles too (plan section 11): this module is the starting point
we hand over; afterwards we vendor their pinned release.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from lexeme_aligner.usj_source import TOKENIZER_VERSION, read_verse_ranges, tokenize

TEXT_FP_VERSION = 1
NAME_HEX = 8          # book fingerprint characters in a file name
MAP_HEX = 5           # chapter fingerprint characters in the optional _fingerprints.txt map

__all__ = ["TEXT_FP_VERSION", "TOKENIZER_VERSION", "NAME_HEX", "MAP_HEX", "verse_line", "chapter_lines",
           "fingerprints", "usj_verses", "usj_fingerprints", "file_hash", "map_line"]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def verse_line(verse: int, text: str) -> str | None:
    """The hashed line for one verse, or None when the verse has no tokens (left out of the fingerprint)."""
    toks = tokenize(text)
    return f"{verse}:{' '.join(toks)}" if toks else None


def chapter_lines(verses: dict[tuple[int, int], str]) -> dict[int, list[str]]:
    """{(chapter, verse): text} -> {chapter: [verse lines in verse order]}; chapters with no tokens are left out."""
    out: dict[int, list[str]] = {}
    for (c, v) in sorted(verses):
        line = verse_line(v, verses[(c, v)])
        if line is not None:
            out.setdefault(c, []).append(line)
    return out


def fingerprints(verses: dict[tuple[int, int], str]) -> tuple[str, dict[int, str]]:
    """(book fingerprint, {chapter: chapter fingerprint}), full 64-hex digests."""
    chapters = {c: _sha("\n".join(lines)) for c, lines in chapter_lines(verses).items()}
    book = _sha("\n".join(f"{c}:{chapters[c]}" for c in sorted(chapters)))
    return book, chapters


def usj_verses(usj_path: Path) -> dict[tuple[int, int], str]:
    """The edition's own verses from one USJ book file (raw text, no strip rules)."""
    return {k: v["text"] for k, v in read_verse_ranges(Path(usj_path), rules={}).items()}


def usj_fingerprints(usj_path: Path) -> tuple[str, dict[int, str]]:
    return fingerprints(usj_verses(usj_path))


def file_hash(book_fp: str) -> str:
    """The hash in a compact file name: <BOOK>_<file_hash>.json."""
    return book_fp[:NAME_HEX]


def map_line(book: str, book_fp: str, built: str, chapters: dict[int, str]) -> str:
    """One line of the optional per-edition _fingerprints.txt: book, file hash, build date, chapter fingerprints
    (MAP_HEX each) in chapter order; "n:xxxxx" only where the chapter sequence breaks."""
    parts, expect = [], 1
    for c in sorted(chapters):
        h = chapters[c][:MAP_HEX]
        parts.append(h if c == expect else f"{c}:{h}")
        expect = c + 1
    return " ".join([book, file_hash(book_fp), built, *parts])
