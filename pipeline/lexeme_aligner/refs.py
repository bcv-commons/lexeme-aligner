"""Verse references — vendored (self-contained) BBCCCVVV encoder.

Copied from bcv-RAG indexer.references so the aligner has NO cross-package import and can be
lifted into a standalone repo unchanged. USFM book code → canonical number (GEN=1 … REV=66).
"""
from __future__ import annotations

BOOK_NUMBERS = {
    "GEN": 1, "EXO": 2, "LEV": 3, "NUM": 4, "DEU": 5, "JOS": 6, "JDG": 7, "RUT": 8, "1SA": 9,
    "2SA": 10, "1KI": 11, "2KI": 12, "1CH": 13, "2CH": 14, "EZR": 15, "NEH": 16, "EST": 17,
    "JOB": 18, "PSA": 19, "PRO": 20, "ECC": 21, "SNG": 22, "ISA": 23, "JER": 24, "LAM": 25,
    "EZK": 26, "DAN": 27, "HOS": 28, "JOL": 29, "AMO": 30, "OBA": 31, "JON": 32, "MIC": 33,
    "NAM": 34, "HAB": 35, "ZEP": 36, "HAG": 37, "ZEC": 38, "MAL": 39, "MAT": 40, "MRK": 41,
    "LUK": 42, "JHN": 43, "ACT": 44, "ROM": 45, "1CO": 46, "2CO": 47, "GAL": 48, "EPH": 49,
    "PHP": 50, "COL": 51, "1TH": 52, "2TH": 53, "1TI": 54, "2TI": 55, "TIT": 56, "PHM": 57,
    "HEB": 58, "JAS": 59, "1PE": 60, "2PE": 61, "1JN": 62, "2JN": 63, "3JN": 64, "JUD": 65,
    "REV": 66,
}


def encode(book_code: str, chapter: int, verse: int) -> int:
    """(book_code, chapter, verse) → BBCCCVVV integer."""
    book = BOOK_NUMBERS.get(book_code.upper())
    if book is None:
        raise ValueError(f"unknown book code: {book_code}")
    if not (1 <= chapter <= 999) or not (1 <= verse <= 999):
        raise ValueError(f"chapter/verse out of range: {chapter}:{verse}")
    return book * 1_000_000 + chapter * 1_000 + verse
