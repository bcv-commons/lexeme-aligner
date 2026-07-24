"""USJ source adapter — read a book's USJ, emit per-verse target-language tokens.

The only format-specific code in the pipeline (docs/aligner-plan.md §Generic input):
walk the USJ `content` tree in document order, track chapter/verse markers, collect
translatable text, and EXCLUDE apparatus by element type/marker (notes, headings,
titles, intro material) so footnote/heading words can never leak into the alignment.

Tokenization is generic unicode word-splitting; language-specific normalization
(e.g. Indonesian clitic stripping) lives in gloss_align, not here.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

# Non-scripture paragraph markers (digits stripped before lookup): headers, titles,
# section headings, psalm titles (d), speaker lines, intro material. `b` is a blank line.
_SKIP_PARA = {
    "h", "toc", "mt", "imt", "ms", "mr", "s", "sr", "r", "d", "sp", "sd",
    "cl", "cp", "ca", "ide", "rem", "b", "ib", "ip", "is", "io", "iot",
    "ili", "im", "imi", "ipi", "iq", "ie", "periph", "restore", "lit",
}
# Character markers whose text is NOT translation text (alternate chapter/verse numbers).
_SKIP_CHAR = {"va", "vp", "ca", "cp", "fv", "fm"}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def strip_marks(text: str) -> str:
    """NFC, then drop combining marks (Unicode Mn) — Arabic harakat, Hebrew points, etc. Otherwise the
    word regex shatters a diacritized word at every mark (فِي → ف, ي). Precomposed Latin accents (é, å)
    survive NFC as single code points, so French/Swedish are unaffected; only decomposed marks go."""
    return "".join(c for c in unicodedata.normalize("NFC", text) if not unicodedata.combining(c))


def _base_marker(marker: str) -> str:
    return (marker or "").rstrip("0123456789")


def _walk_verses(usj_path: Path, warn=sys.stderr):
    """Shared walker for read_verses/read_verse_ranges — yields (chapter, verse_start, verse_end,
    text_piece) per text-bearing item, verse_end == verse_start for a bare (non-range) marker."""
    usj = json.loads(Path(usj_path).read_text(encoding="utf-8"))
    state = {"ch": 0, "vs": 0, "ve": 0}
    seen_unknown: set[str] = set()

    def emit(text: str):
        if state["ch"] and state["vs"] and text:
            yield (state["ch"], state["vs"], state["ve"], text)

    def walk(items):
        for it in items:
            if isinstance(it, str):
                yield from emit(it)
                continue
            t = it.get("type")
            if t == "chapter":
                state["ch"] = int(re.match(r"\d+", str(it.get("number", "0"))).group())
                state["vs"] = state["ve"] = 0
            elif t == "verse":
                # a range marker ("3-4") keeps BOTH ends — read_verses() historically only kept the
                # first number, silently discarding which OTHER source verses share this target text.
                m = re.match(r"(\d+)(?:-(\d+))?", str(it.get("number", "0")))
                if m:
                    state["vs"] = int(m.group(1))
                    state["ve"] = int(m.group(2)) if m.group(2) else state["vs"]
                else:
                    state["vs"] = state["ve"] = 0
            elif t == "note":
                continue                                    # footnotes / cross-refs: never text
            elif t == "para":
                base = _base_marker(it.get("marker", ""))
                if base in _SKIP_PARA:
                    continue
                if base not in {"p", "m", "po", "pr", "cls", "pmo", "pm", "pmc", "pmr",
                                "pi", "mi", "nb", "pc", "ph", "q", "qr", "qc", "qa",
                                "qac", "qm", "qd", "lh", "li", "lf", "lim", "tr", "tc",
                                "tcr", "th", "thr"} and base not in seen_unknown:
                    seen_unknown.add(base)
                    print(f"[usj] note: unknown para marker '{it.get('marker')}' — included",
                          file=warn)
                if it.get("content"):
                    yield from walk(it["content"])
            elif t == "char":
                if _base_marker(it.get("marker", "")) in _SKIP_CHAR:
                    continue
                if it.get("content"):
                    yield from walk(it["content"])
            elif it.get("content"):
                yield from walk(it["content"])

    yield from walk(usj.get("content", []))


def read_verses(usj_path: Path, warn=sys.stderr) -> dict[tuple[int, int], str]:
    """{(chapter, verse): text} for one book's USJ file. Verse ranges ("1-2") are
    keyed by their first number. Unknown para markers are included but logged once."""
    verses: dict[tuple[int, int], list[str]] = {}
    for ch, vs, _ve, text in _walk_verses(usj_path, warn):
        verses.setdefault((ch, vs), []).append(text)
    return {k: " ".join(parts) for k, parts in verses.items()}


def read_verse_ranges(usj_path: Path, warn=sys.stderr) -> dict[tuple[int, int], dict]:
    """{(chapter, verse_start): {"text": str, "verse_end": int}} — like read_verses but preserves
    the END of a verse RANGE marker ("3-4" -> verse_start=3, verse_end=4, text=both verses' combined
    translation), instead of silently discarding it. verse_end == verse_start for a normal single-verse
    marker. Use this (not read_verses) wherever a caller needs to know if a target block actually
    covers MULTIPLE source verses — e.g. reverse_align_check.py / a future build_corpus range-pooling."""
    verses: dict[tuple[int, int], dict] = {}
    for ch, vs, ve, text in _walk_verses(usj_path, warn):
        key = (ch, vs)
        if key not in verses:
            verses[key] = {"text": [], "verse_end": ve}
        verses[key]["text"].append(text)
        verses[key]["verse_end"] = max(verses[key]["verse_end"], ve)
    return {k: {"text": " ".join(v["text"]), "verse_end": v["verse_end"]} for k, v in verses.items()}


def tokenize(text: str) -> list[str]:
    """Generic unicode word tokens, order preserved (normalization happens later). A token is a run of
    letters + their combining marks (a grapheme cluster), so Indic/diacritized scripts tokenize by WORD,
    not shatter at every vowel sign — दाऊद stays one token, not द+ऊद (the old `[^\\W\\d_]+` regex broke at
    spacing marks like the Mc matra ा). `strip_marks` still drops nonspacing marks (Arabic harakat, Hebrew
    points, Indic viramas) for match-normalisation; spacing marks stay inside the cluster. Latin/Cyrillic
    unaffected (no combining marks after NFC)."""
    toks: list[str] = []
    cur: list[str] = []
    for ch in strip_marks(text):
        if unicodedata.category(ch)[0] in ("L", "M"):        # letter or combining mark → part of the word
            cur.append(ch)
        elif cur:
            toks.append("".join(cur))
            cur = []
    if cur:
        toks.append("".join(cur))
    return toks
