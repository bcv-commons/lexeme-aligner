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

bibles owns the tokenizer and the fingerprint since verse-text 1.0.0 (2026-10-17; plan section 11): verse_line, chapter_lines,
fingerprints and file_hash are their vendored bcv_tokenize.py (via usj_source). This module adds what is ours: reading an ingested
book, and the per-edition _fingerprints.txt map.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lexeme_aligner.usj_source import _BCV, TOKENIZER_VERSION, read_verse_ranges

TEXT_FP_VERSION = _BCV.TEXT_FP_VERSION
NAME_HEX = _BCV.NAME_HEX     # book fingerprint characters in a file name
MAP_HEX = 5                  # chapter fingerprint characters in the optional _fingerprints.txt map
MAP_NAME = "_fingerprints.txt"

__all__ = ["TEXT_FP_VERSION", "TOKENIZER_VERSION", "NAME_HEX", "MAP_HEX", "MAP_NAME", "verse_line", "chapter_lines",
           "fingerprints", "usj_verses", "usj_fingerprints", "file_hash", "map_line", "book_file_hash", "update_map", "update_language_map", "layer_map_lines", "read_map"]

verse_line = _BCV.verse_line          # "{verse}:{tokens}" or None (verse with no tokens: left out)
chapter_lines = _BCV.chapter_lines    # {(chapter, verse): text} -> {chapter: [verse lines]}
fingerprints = _BCV.fingerprints      # -> (book fingerprint, {chapter: fingerprint}), full 64-hex digests
file_hash = _BCV.file_hash            # the hash in a compact file name: <BOOK>_<file_hash>.json


def usj_verses(usj_path: Path) -> dict[tuple[int, int], str]:
    """The edition's own verses from one USJ book file (raw text, no strip rules)."""
    return {k: v["text"] for k, v in read_verse_ranges(Path(usj_path), rules={}).items()}


def usj_fingerprints(usj_path: Path) -> tuple[str, dict[int, str]]:
    return fingerprints(usj_verses(usj_path))


def map_line(book: str, book_fp: str, built: str, chapters: dict[int, str]) -> str:
    """One line of the optional per-edition _fingerprints.txt: book, file hash, build date, chapter fingerprints
    (MAP_HEX each) in chapter order; "n:xxxxx" only where the chapter sequence breaks."""
    parts, expect = [], 1
    for c in sorted(chapters):
        h = chapters[c][:MAP_HEX]
        parts.append(h if c == expect else f"{c}:{h}")
        expect = c + 1
    return " ".join([book, file_hash(book_fp), built, *parts])


def book_file_hash(usj_path: Path) -> str:
    """The NAME_HEX hash a compact (or full-align) file for this ingested book is named with."""
    return file_hash(usj_fingerprints(usj_path)[0])


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_map(edition_dir: Path) -> dict[str, str]:
    """{book: its map line} from an edition's _fingerprints.txt ({} when there is none)."""
    fp = Path(edition_dir) / MAP_NAME
    if not fp.exists():
        return {}
    return {ln.split(" ", 1)[0]: ln for ln in fp.read_text(encoding="utf-8").splitlines() if ln.strip()}


def update_map(edition_dir: Path, lines: dict[str, str], language: bool = True) -> Path:
    """Put each {book: map line} into the edition's _fingerprints.txt, REPLACING that book's line (the map names only the latest
    version of each book; older book files stay published). Lines sorted in canonical book order; locked + atomic, because
    several builders can write one edition folder."""
    import fcntl
    import os
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    order = {b: i for i, b in enumerate(OT_BOOKS + NT_BOOKS)}
    edition_dir = Path(edition_dir)
    edition_dir.mkdir(parents=True, exist_ok=True)
    path = edition_dir / MAP_NAME
    with open(edition_dir / f".{MAP_NAME}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cur = read_map(edition_dir)
            cur.update(lines)
            tmp = edition_dir / f".{MAP_NAME}.{os.getpid()}.tmp"
            tmp.write_text("".join(cur[b] + "\n" for b in sorted(cur, key=lambda b: (order.get(b, len(order)), b))), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    if language:                          # compact edition folders only; a full-align layer folder has no language map of its own
        update_language_map(edition_dir.parent)
    return path


def layer_map_lines(layer_dir: Path, usj_dir: Path) -> dict[str, str]:
    """{book: map line} for a full-align layer folder, from the text it was built on: only books whose file name is that text's
    fingerprint (a layer built on an older text gets no line). Build date = the main file's mtime."""
    import re
    out = {}
    for f in sorted(Path(layer_dir).glob("*_*.json")):
        m = re.fullmatch(r"([0-9A-Z]{3})_([0-9a-f]{%d})\.json" % NAME_HEX, f.name)
        usj = next(Path(usj_dir).glob(f"*-{m[1]}.json"), None) if m else None
        if usj is None:
            continue
        fp, chapters = usj_fingerprints(usj)
        if file_hash(fp) == m[2]:
            out[m[1]] = map_line(m[1], fp, datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d"), chapters)
    return out


def update_language_map(lang_dir: Path, layers: dict[str, Path] | None = None) -> Path | None:
    """Rewrite the language's combined <iso[0]>/<iso>/_fingerprints.txt: every edition map's lines, each prefixed by the edition folder
    name, editions in name order (client request 2026-10-21: find which edition, if any, matches a text with one request, without the
    tree API). Then the editions that have ONLY a full-alignment layer (client request 2026-10-21, e.g. Door43 texts): their layer map's
    lines prefixed by "<dataset>/<layer id>" (e.g. "full-alignments-manual-sa/hin_higlt+manual+door43"), the repo and folder that hold
    the files. `layers` = {that prefix: layer folder}; publish_safe passes the layers that pass the publish gate. None keeps the layer
    lines the file already has, so a chain rewriting an edition map does not drop them. Removed when nothing is left. Locked + atomic."""
    import fcntl
    import os
    lang_dir = Path(lang_dir)
    path = lang_dir / MAP_NAME
    with open(lang_dir / f".{MAP_NAME}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            out = [f"{d.name} {ln}\n" for d in sorted(p for p in lang_dir.iterdir() if (p / MAP_NAME).is_file())
                   for ln in read_map(d).values()]
            if layers is None:
                out += [ln + "\n" for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
                        if "/" in ln.split(" ", 1)[0]]
            else:
                out += [f"{prefix} {ln}\n" for prefix in sorted(layers) for ln in read_map(layers[prefix]).values()]
            if not out:
                path.unlink(missing_ok=True)
                return None
            tmp = lang_dir / f".{MAP_NAME}.{os.getpid()}.tmp"
            tmp.write_text("".join(out), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return path
