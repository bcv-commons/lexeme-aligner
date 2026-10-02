"""Version stamp + consistency check for the published source-side index (`_index/<BOOK>_lexemes.json`).

WHY (2026-09-29). Every compact-alignments file counts source tokens by `srcOrd`, the ordinal of a CONTENT
token inside its verse, and the published `_index/<BOOK>_lexemes.json` is the client's lookup for what that
ordinal means. The index was written ONCE (2025-07-25, "only if missing") and never compared to the spine
again. The spine was rebuilt on 2026-09-23 and ten verses (DAN 5:27, DAN 5:28, ECC 6:11, EZK 7:13,
EZK 16:53, EZK 30:16, ISA 24:16, JER 12:1, NAM 3:17, PRO 26:17) each gained ONE content token, so every
ordinal after the insertion point was off by one for any client using the old index against a file built
on the new spine, and vice versa. Nothing could notice: the index carried no record of the spine it was
derived from.

WHAT THIS ADDS
  * `_index/_source.json` — the stamp: the spine's own `macula_spine_sha256` plus, per book, the verse count,
    the content-token count and a sha256 of the exact bytes of that book's `_lexemes.json`.
  * `check_index()` — the two-way consistency check. It FAILS when the index disagrees with what the current
    spine yields (a different verse list, or a different lexeme sequence in any verse), when a book file is
    missing, or when a file no longer matches its stamped digest (hand edit). A spine hash that differs while
    every verse still matches is only a WARNING (nothing a client sees has changed) unless `strict=True`.
  * `ensure_current()` — what `compact_align` calls before it writes any array: cheap when the stamp's spine
    hash equals the spine's (one small query), a full content comparison otherwise; raises `IndexMismatch`
    with the exact refresh command instead of silently producing files whose ordinals disagree with the
    published index.
  * `refresh()` — regenerates only the books that differ (atomic write, deterministic bytes), then re-stamps.

    python3 -m lexeme_aligner.source_index --check [--strict]
    python3 -m lexeme_aligner.source_index --refresh [--books DAN ISA ...]

The index files are compared by content, not by mtime. Alignment files written BEFORE a spine change keep
the old numbering; see pipeline/scripts/migrate_source_index.py for the exact, lossless shift that brings
them onto the new one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner.config import SPINE_DB

INDEX_ROOT = Path("publish/compact-alignments/_index")
STAMP_NAME = "_source.json"
STAMP_VERSION = 1


class IndexMismatch(RuntimeError):
    """The published source index disagrees with the current spine."""


def spine_sha(spine_db: Path = SPINE_DB) -> str:
    """The spine's own recorded content pin (`spine_meta.macula_spine_sha256`); '' when the spine has none."""
    try:
        con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM spine_meta WHERE key='macula_spine_sha256'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return ""                     # no spine on this machine (tests, CI): the stamp then compares "" == ""
    return row[0] if row else ""


def file_bytes(lexemes: dict[str, list[str]]) -> bytes:
    """EXACTLY the bytes `compact_align` writes for a book's `_lexemes.json`."""
    return (json.dumps(lexemes, ensure_ascii=False) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _lexemes(heb, book: str) -> dict[str, list[str]]:
    from lexeme_aligner.compact_align import build_source_lexemes
    return build_source_lexemes(heb, book)


def shift_entry(entry: str, insert_pos: int) -> str:
    """Renumber one verse entry after ONE lexeme was inserted at content position `insert_pos`: every
    whitespace-separated item begins with its srcOrd ('5:9-10', '10:G:23', '10:name_after:48',
    '3:12:40:71'); each srcOrd >= insert_pos moves up by one, everything else is untouched. The inserted
    token itself stays unaligned. Raises ValueError on an item that does not start with '<int>:' — the
    caller must skip that file rather than guess."""
    out = []
    for item in entry.split():
        head, sep, rest = item.partition(":")
        if not sep or not head.isdigit():
            raise ValueError(f"not a srcOrd item: {item!r}")
        n = int(head)
        out.append(f"{n + 1 if n >= insert_pos else n}:{rest}")
    return " ".join(out)


def entry_ordinals(entry: str) -> list[int]:
    return [int(item.partition(":")[0]) for item in entry.split()]


def read_stamp(index_root: Path = INDEX_ROOT) -> dict | None:
    fp = index_root / STAMP_NAME
    return json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None


def compare_book(current: dict[str, list[str]], published: dict[str, list[str]]) -> dict:
    """{'keys_equal', 'differing': [verse refs], 'inserts': {ref: (position, lexeme)}} — `inserts` is filled
    only for a verse whose ONLY change is one inserted lexeme (the case the migration script can shift
    losslessly); any other kind of difference stays in `differing` without an `inserts` entry."""
    out = {"keys_equal": list(current) == list(published), "differing": [], "inserts": {}}
    if not out["keys_equal"]:
        return out
    for ref, new in current.items():
        old = published[ref]
        if new == old:
            continue
        out["differing"].append(ref)
        if len(new) == len(old) + 1:
            for pos in range(len(new)):
                if new[:pos] == old[:pos] and new[pos + 1:] == old[pos:]:
                    out["inserts"][ref] = (pos, new[pos])
                    break
    return out


def check_index(heb, index_root: Path = INDEX_ROOT, books: list[str] | None = None, *,
                strict: bool = False, spine_db: Path = SPINE_DB) -> dict:
    """{'ok': bool, 'errors': [...], 'warnings': [...], 'books': {book: compare_book result}}."""
    from lexeme_aligner.compact_align import ALL_BOOKS
    books = books or list(ALL_BOOKS)
    errors: list[str] = []
    warnings: list[str] = []
    per_book: dict[str, dict] = {}
    stamp = read_stamp(index_root)
    cur_sha = spine_sha(spine_db)
    if stamp is None:
        errors.append(f"no {STAMP_NAME} in {index_root} — run: python3 -m lexeme_aligner.source_index --refresh")
    elif stamp.get("spine_sha256") != cur_sha:
        (errors if strict else warnings).append(
            f"index stamped from spine {str(stamp.get('spine_sha256'))[:12]}…, current spine is {cur_sha[:12]}…")
    for book in books:
        fp = index_root / f"{book}_lexemes.json"
        if not fp.exists():
            errors.append(f"{book}: {fp.name} is missing")
            continue
        raw = fp.read_bytes()
        published = json.loads(raw.decode("utf-8"))
        cmp = compare_book(_lexemes(heb, book), published)
        per_book[book] = cmp
        if not cmp["keys_equal"]:
            errors.append(f"{book}: verse list differs from the spine's")
        elif cmp["differing"]:
            errors.append(f"{book}: {len(cmp['differing'])} verse(s) differ from the spine "
                          f"({', '.join(cmp['differing'][:5])}{'…' if len(cmp['differing']) > 5 else ''})")
        if stamp is not None:
            want = (stamp.get("books") or {}).get(book, {}).get("sha256")
            if want is None:
                errors.append(f"{book}: not present in the stamp")
            elif want != _digest(raw):
                errors.append(f"{book}: file no longer matches its stamped sha256 (edited after stamping)")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "books": per_book}


def write_stamp(index_root: Path, spine_db: Path = SPINE_DB, books: list[str] | None = None) -> dict:
    """Stamp built from the bytes ON DISK (never from memory), so it always describes what is published."""
    from lexeme_aligner.compact_align import ALL_BOOKS
    per_book = {}
    for book in books or list(ALL_BOOKS):
        fp = index_root / f"{book}_lexemes.json"
        if not fp.exists():
            continue
        raw = fp.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
        per_book[book] = {"verses": len(doc), "content_tokens": sum(len(v) for v in doc.values()),
                          "sha256": _digest(raw)}
    stamp = {"stamp_version": STAMP_VERSION, "spine_sha256": spine_sha(spine_db),
             "note": "srcOrd in every compact-alignments file indexes these per-verse lexeme lists; "
                     "`python3 -m lexeme_aligner.source_index --check` verifies them against the spine.",
             "books": dict(sorted(per_book.items()))}
    _atomic_write(index_root / STAMP_NAME,
                  (json.dumps(stamp, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return stamp


def refresh(heb, index_root: Path = INDEX_ROOT, books: list[str] | None = None,
            spine_db: Path = SPINE_DB) -> dict:
    """Rewrite the `_lexemes.json` of every requested (default: every) book whose content differs from the
    spine's or that is missing, then re-stamp. Returns {'rewritten': [books], 'stamp': stamp}."""
    from lexeme_aligner.compact_align import ALL_BOOKS
    rewritten = []
    for book in books or list(ALL_BOOKS):
        fp = index_root / f"{book}_lexemes.json"
        cur = _lexemes(heb, book)
        data = file_bytes(cur)
        if not fp.exists() or fp.read_bytes() != data:
            _atomic_write(fp, data)
            rewritten.append(book)
    return {"rewritten": rewritten, "stamp": write_stamp(index_root, spine_db)}


def ensure_current(heb, book: str, index_root: Path, spine_db: Path = SPINE_DB) -> None:
    """Called by compact_align before it writes a book's array. Fast path: the stamp's spine hash equals the
    spine's AND the stamp lists this book -> trusted (one tiny query). Otherwise compare this book's content
    with the spine's and raise `IndexMismatch` if they differ. A missing book file is NOT an error here
    (compact_align creates it and this function is not reached for it)."""
    stamp = read_stamp(index_root)
    if stamp and stamp.get("spine_sha256") == spine_sha(spine_db) and book in (stamp.get("books") or {}):
        return
    fp = index_root / f"{book}_lexemes.json"
    if not fp.exists():
        return
    cmp = compare_book(_lexemes(heb, book), json.loads(fp.read_text(encoding="utf-8")))
    if not cmp["keys_equal"] or cmp["differing"]:
        n = "the verse list" if not cmp["keys_equal"] else f"{len(cmp['differing'])} verse(s) ({cmp['differing'][0]}…)"
        raise IndexMismatch(
            f"published source index for {book} disagrees with the current spine in {n}: writing this "
            f"language's array would give srcOrd values that do not match {fp}. Run "
            f"`python3 -m lexeme_aligner.source_index --refresh --books {book}` (and "
            f"pipeline/scripts/migrate_source_index.py for files already published) first.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index-root", type=Path, default=INDEX_ROOT)
    ap.add_argument("--books", nargs="*", default=None)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 1 if the index disagrees with the spine")
    g.add_argument("--refresh", action="store_true", help="rewrite differing books and re-stamp")
    ap.add_argument("--strict", action="store_true", help="--check: a spine-hash-only difference also fails")
    a = ap.parse_args(argv)
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = HebrewSource()
    if a.check:
        res = check_index(heb, a.index_root, a.books, strict=a.strict)
        for w in res["warnings"]:
            print(f"[source_index] WARNING {w}", file=sys.stderr)
        for e in res["errors"]:
            print(f"[source_index] ERROR {e}", file=sys.stderr)
        print(f"[source_index] {'OK' if res['ok'] else 'FAILED'}: {len(res['books'])} book(s) compared",
              file=sys.stderr)
        return 0 if res["ok"] else 1
    res = refresh(heb, a.index_root, a.books)
    print(f"[source_index] rewrote {len(res['rewritten'])} book file(s): {', '.join(res['rewritten']) or '-'}; "
          f"stamped {len(res['stamp']['books'])} book(s) at spine {res['stamp']['spine_sha256'][:12]}…",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
