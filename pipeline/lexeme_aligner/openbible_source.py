"""Ingest adapter for the catalog's `o` source: editions bibles republishes itself on its CDN, wherever the licence permits and
the original source is not directly usable (openbible/Biblica, audiobiblia.org; see each edition's own `_meta.json`).

    https://cdn.bibel.wiki/openbible/<iso>/<edition>/<BOOK>/<chapter>.json
    {"book": "MAT", "chapter": 1, "verses": [{"verse": "1", "text": "..."}, ...]}

`<edition>` is literally the catalog's `o:<ABBR>` value, `<BOOK>` the USFM code. A chapter number past the last chapter answers 404, so
a book is read until its first 404; a book whose chapter 1 is missing is skipped. The result is the same `<NN>-<BOOK>.json` USJ tree
every other adapter writes (verses under `\\p`, via usfmtc), plus the pin and the config/sources.json licence pointer.

    python -m lexeme_aligner.openbible_source --iso mgw --edition MATUMBI --tag matumbi --to-usj DIR [--pin FILE] [--book MAT]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from lexeme_aligner import dns_cache as _dns_cache

_dns_cache.install()

BASE = "https://cdn.bibel.wiki/openbible"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
MAX_CHAPTERS = 151


def _get(url: str, retries: int = 6) -> bytes | None:
    """The body, or None for a 404 (end of book / no such book). Other errors are retried with a capped backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:       # noqa: S310 — fixed https CDN origin
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries - 1:
                raise
        time.sleep(min(30, 2 ** attempt))
    return None


def chapter(iso: str, edition: str, book: str, ch: int) -> list[dict] | None:
    raw = _get(f"{BASE}/{iso}/{edition}/{book}/{ch}.json")
    if raw is None:
        return None
    return json.loads(raw).get("verses") or []


def book_usfm(book: str, chapters: list[list[dict]]) -> str:
    """Minimal USFM for one book: verses under \\p, one \\c per chapter (see helloao_source._book_usfm for why \\p)."""
    out = [f"\\id {book}"]
    for n, verses in enumerate(chapters, 1):
        out += [f"\\c {n}", "\\p"]
        for v in verses:
            text = " ".join(str(v.get("text") or "").split())
            if text:
                out.append(f"\\v {v['verse']} {text}")
    return "\n".join(out) + "\n"


def meta(iso: str, edition: str) -> dict:
    raw = _get(f"{BASE}/{iso}/{edition}/_meta.json")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def build_pin(iso: str, edition: str, tag: str, m: dict, n_books: int) -> dict:
    return {
        "iso": tag,
        "provider": "cdn.bibel.wiki/openbible",
        "language_name": m.get("language_name") or m.get("languageName"),
        "version_id": edition,
        "sha256": None,
        "books": n_books,
        "license_url": m.get("license_url") or m.get("licenseUrl") or m.get("license") or f"{BASE}/{iso}/{edition}/_meta.json",
        "name": m.get("name") or m.get("title"),
    }


def to_usj(iso: str, edition: str, usj_dir: Path, only: list[str] | None = None) -> int:
    try:
        import usfmtc
    except ImportError:
        raise SystemExit("[openbible] USFM→USJ needs usfmtc — pip install -e '.[ingest]'")
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM

    usj_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    with tempfile.TemporaryDirectory() as td:
        for book, nn in _BOOK_FILE_NUM.items():
            if only and book not in only:
                continue
            chapters: list[list[dict]] = []
            for ch in range(1, MAX_CHAPTERS):
                verses = chapter(iso, edition, book, ch)
                if verses is None:
                    break
                chapters.append(verses)
            if not chapters:
                continue
            uf = Path(td) / f"{book}.usfm"
            uf.write_text(book_usfm(book, chapters), encoding="utf-8")
            usfmtc.readFile(str(uf)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
            n += 1
    print(f"[openbible] {iso}/{edition}: {n} book(s) → {usj_dir}", file=sys.stderr)
    return n


def update_sources(pin: dict, path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[pin["iso"]] = {"provider": pin["provider"], "edition": pin["version_id"], "license_url": pin["license_url"]}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="the language code in the CDN path (the catalog's iso)")
    ap.add_argument("--edition", required=True, help="the catalog's o:<ABBR> value")
    ap.add_argument("--tag", required=True, help="our per-edition tag (pins/sources are keyed by it)")
    ap.add_argument("--to-usj", type=Path, required=True, metavar="DIR")
    ap.add_argument("--book", action="append", help="limit to book(s); repeatable")
    ap.add_argument("--pin", type=Path, default=None)
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    a = ap.parse_args(argv)
    m = meta(a.iso, a.edition)
    n = to_usj(a.iso, a.edition, a.to_usj, a.book)
    if not n:
        print(f"[openbible] {a.iso}/{a.edition}: no book answered — nothing ingested", file=sys.stderr)
        return 1
    pin = build_pin(a.iso, a.edition, a.tag, m, n)
    pin_path = a.pin or Path("config/pins") / f"{a.tag}.json"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.sources:
        update_sources(pin, a.sources)
    print(f"[openbible] {a.tag}: {a.edition} ({pin['name']}, {n} books) · license→{pin['license_url']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
