"""Text input for `align-text` (2026-10-29): USJ, USFM or plain text files, turned into our USJ book files (`NN-BOOK.json`).

`--text PATH` takes a folder or one file. What is in it decides the format:

    USJ          *.json book files named NN-BOOK.json (our ingest layout); used as they are
    USFM         *.usfm / *.sfm / *.SFM, one book per file (book code from the \\id line); converted with usfmtc
    eBible       vref.txt + one text file with the same number of lines (the eBible corpus layout: line N of the text is the
                 verse named on line N of vref.txt; an empty line = no text; `<range>` = this verse is part of the one before)
    plain text   *.txt with one verse per line:  `BOOK C:V text`  (e.g. `RUT 1:1 In the days when the judges judged ...`)
                 or `C:V text` when the file name starts with the book code (RUT.txt, 08-RUT.txt). `C:V-W` for a bridged verse.
                 Empty lines and lines starting with # are skipped.

Book codes are the usual USFM ones (GEN ... REV). Books we do not align (deuterocanon etc.) are skipped with a warning. The converted
files go into OUT/text-usj; the run record keeps the sha256 of the original files.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

USFM_SUFFIXES = {".usfm", ".sfm"}
_REF = re.compile(r"^\s*(?:([1-4]?[A-Z]{2,3}|[1-4][A-Z]{2})\s+)?(\d+)[:.](\d+)(?:-(\d+))?(?:\s+(.*))?$")
_VREF = re.compile(r"^([1-4]?[A-Z0-9]{2,3})\s+(\d+):(\d+)$")


def _book_nums() -> dict[str, str]:
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    return _BOOK_FILE_NUM


def detect(src: Path) -> str:
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file())
    names = {p.name.lower() for p in files}
    if any(re.fullmatch(r"\d\d-[1-4A-Z]{3}\.json", p.name) for p in files):
        return "usj"
    if any(p.suffix.lower() in USFM_SUFFIXES for p in files):
        return "usfm"
    if "vref.txt" in names:
        return "ebible"
    if any(p.suffix.lower() == ".txt" for p in files):
        return "plain"
    raise SystemExit(f"align-text: no text found in {src} (expected NN-BOOK.json USJ files, .usfm/.sfm files, vref.txt + a text "
                     "file, or .txt files with one verse per line)")


def source_digest(src: Path) -> str:
    h = hashlib.sha256()
    for fp in ([src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file())):
        h.update(fp.name.encode() + b"\0" + fp.read_bytes() + b"\0")
    return h.hexdigest()


def book_usj(book: str, verses: dict[int, list[tuple[str, str]]]) -> dict:
    """{chapter: [(verse label, text), ...]} -> a USJ book: one paragraph per chapter, a verse marker before each verse."""
    content: list = [{"type": "book", "marker": "id", "code": book, "content": []}]
    for ch in sorted(verses):
        content.append({"type": "chapter", "marker": "c", "number": str(ch)})
        para: list = []
        for label, text in verses[ch]:
            para += [{"type": "verse", "marker": "v", "number": label}, text.strip() + " "]
        content.append({"type": "para", "marker": "p", "content": para})
    return {"type": "USJ", "version": "3.0", "content": content}


def _add(books: dict, book: str, ch: int, vs: int, ve: int | None, text: str) -> None:
    label = f"{vs}-{ve}" if ve and ve != vs else str(vs)
    books.setdefault(book, {}).setdefault(ch, []).append((label, text))


def read_plain(files: list[Path]) -> dict:
    books: dict = {}
    for fp in files:
        m = re.match(r"(?:\d+-)?([1-4]?[A-Z]{2,3})", fp.stem.upper())
        file_book = m.group(1) if m and m.group(1) in _book_nums() else None
        for n, line in enumerate(fp.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            r = _REF.match(line)
            book = (r.group(1) or file_book) if r else None
            if not r or not book:
                raise SystemExit(f"align-text: {fp.name} line {n}: expected `BOOK C:V text`"
                                 + ("" if file_book else " (or name the file after the book, e.g. RUT.txt, and write `C:V text`)")
                                 + f": {line[:60]!r}")
            if r.group(5):
                _add(books, book.upper(), int(r.group(2)), int(r.group(3)), int(r.group(4)) if r.group(4) else None, r.group(5))
    return books


def read_ebible(folder: Path) -> dict:
    vref = folder / "vref.txt"
    if not vref.exists():
        vref = next(p for p in folder.iterdir() if p.name.lower() == "vref.txt")
    texts = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt" and p != vref]
    if len(texts) != 1:
        raise SystemExit(f"align-text: {folder} has vref.txt and {len(texts)} other .txt files; put exactly one text file beside it")
    refs = vref.read_text(encoding="utf-8-sig").splitlines()
    lines = texts[0].read_text(encoding="utf-8-sig").splitlines()
    if len(lines) != len(refs):
        raise SystemExit(f"align-text: {texts[0].name} has {len(lines)} lines, vref.txt {len(refs)}; they must match line for line")
    books: dict = {}
    last: tuple | None = None                        # (book, chapter, index in its list) of the verse a <range> line extends
    for ref, text in zip(refs, lines):
        m = _VREF.match(ref.strip())
        if not m:
            continue
        book, ch, vs = m.group(1), int(m.group(2)), int(m.group(3))
        if text.strip() == "<range>" and last and last[:2] == (book, ch):
            lst = books[book][ch]
            label, t = lst[last[2]]
            lst[last[2]] = (f"{label.split('-')[0]}-{vs}", t)
        elif text.strip():
            _add(books, book, ch, vs, None, text)
            last = (book, ch, len(books[book][ch]) - 1)
        else:
            last = None
    return books


def prepare(src: Path, dest: Path, warn=sys.stderr) -> tuple[Path, str]:
    """The folder of USJ book files the chain reads, and the input format. USJ input is used in place; anything else is
    converted into `dest`."""
    fmt = detect(src)
    if fmt == "usj":
        if src.is_file():
            raise SystemExit("align-text: give the folder of the USJ book files, not one file")
        return src, fmt
    nums = _book_nums()
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.json"):
        old.unlink()
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file())
    written = 0
    if fmt == "usfm":
        try:
            from lexeme_aligner import usfm_to_usj
            from lexeme_aligner.cdn_source import _book_id
        except ImportError:
            raise SystemExit("align-text: reading USFM needs usfmtc (pip install usfmtc)")
        for fp in files:
            if fp.suffix.lower() not in USFM_SUFFIXES:
                continue
            book = _book_id(fp)
            if book not in nums:
                print(f"[align-text] skip {fp.name}: book {book} is not aligned", file=warn)
                continue
            usfm_to_usj.write(fp, dest / f"{nums[book]}-{book}.json")
            written += 1
    else:
        books = read_ebible(src if src.is_dir() else src.parent) if fmt == "ebible" else \
            read_plain([p for p in files if p.suffix.lower() == ".txt"])
        for book, verses in sorted(books.items()):
            if book not in nums:
                print(f"[align-text] skip {book}: not an aligned book", file=warn)
                continue
            (dest / f"{nums[book]}-{book}.json").write_text(json.dumps(book_usj(book, verses), ensure_ascii=False), encoding="utf-8")
            written += 1
    if not written:
        raise SystemExit(f"align-text: no aligned book found in {src} ({fmt})")
    print(f"[align-text] {fmt}: {written} book(s) -> {dest}", file=warn)
    return dest, fmt
