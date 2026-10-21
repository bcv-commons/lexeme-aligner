"""Ingest adapter for the catalog's `o` and `c` sources: editions bibles republishes itself on its CDN, wherever the licence permits
and the original source is not directly usable (`o`: openbible/Biblica, audiobiblia.org; `c`: CrossWire SWORD modules, since
2026-10-21, e.g. ell GreVamvas; see each edition's own `_meta.json`). Same layout under both roots (`--root openbible|crosswire`).

Since 2026-10-17 we read bibles' canonical form, unchanged: the book USJ named in the edition's `_meta.json` `formats`
(`<BOOK>.usj.json`, USJ 3.0 "as the source gave it"; bibles publishes the same text as Sofria per chapter, and either is canonical).
Each book is written as it came to `<NN>-<BOOK>.json`; usj_source reads its verse text with bibles' rule. The old per-chapter
verse-json (`<BOOK>/<chapter>.json`) was removed by bibles, and its minimal-USFM conversion lost text after a literal `|`.

    https://cdn.bibel.wiki/openbible/<iso>/<edition>/_meta.json           books, formats, licences
    https://cdn.bibel.wiki/openbible/<iso>/<edition>/<BOOK>.usj.json

    python -m lexeme_aligner.openbible_source --iso mgw --edition MATUMBI --tag matumbi --to-usj DIR [--pin FILE] [--book MAT]
    python -m lexeme_aligner.openbible_source --root crosswire --iso ell --edition GreVamvas --tag ell_grevamvas --to-usj DIR
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from lexeme_aligner import dns_cache as _dns_cache

_dns_cache.install()

CDN = "https://cdn.bibel.wiki"
ROOTS = ("openbible", "crosswire")              # `o` -> openbible, `c` -> crosswire
BASE = f"{CDN}/openbible"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"


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


def book_usj(iso: str, edition: str, book: str, path: str = "<BOOK>.usj.json", root: str = "openbible") -> bytes | None:
    """The book's USJ exactly as bibles publishes it (None = not published)."""
    return _get(f"{CDN}/{root}/{iso}/{edition}/{path.replace('<BOOK>', book)}")


def meta(iso: str, edition: str, root: str = "openbible") -> dict:
    raw = _get(f"{CDN}/{root}/{iso}/{edition}/_meta.json")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def build_pin(iso: str, edition: str, tag: str, m: dict, n_books: int, root: str = "openbible") -> dict:
    lic = [x.get("type") for x in (m.get("licenses") or []) if isinstance(x, dict) and x.get("type")]
    return {
        "iso": tag,
        "provider": f"cdn.bibel.wiki/{root}",
        **({"license": "; ".join(lic)} if lic else {}),
        **({"original_source": m["source"]} if isinstance(m.get("source"), str) else {}),
        "language_name": m.get("language_name") or m.get("languageName"),
        "version_id": edition,
        "sha256": None,
        "books": n_books,
        "license_url": m.get("license_url") or m.get("licenseUrl") or m.get("license") or f"{CDN}/{root}/{iso}/{edition}/_meta.json",
        "name": m.get("name") or m.get("title"),
    }


def to_usj(iso: str, edition: str, usj_dir: Path, only: list[str] | None = None, m: dict | None = None,
           root: str = "openbible") -> int:
    """Every book the edition's `_meta.json` lists, as bibles' book USJ, written unchanged to <NN>-<BOOK>.json."""
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    m = m if m is not None else meta(iso, edition, root)
    path = ((m.get("formats") or {}).get("usj") or {}).get("path") or "<BOOK>.usj.json"
    books = m.get("books") or list(_BOOK_FILE_NUM)
    usj_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for book in books:
        nn = _BOOK_FILE_NUM.get(book)
        if not nn or (only and book not in only):
            continue
        raw = book_usj(iso, edition, book, path, root)
        if raw is None:
            print(f"[openbible] {iso}/{edition} {book}: no USJ published — skipped", file=sys.stderr)
            continue
        doc = json.loads(raw)
        if doc.get("type") != "USJ":
            raise SystemExit(f"[openbible] {iso}/{edition} {book}: not a USJ document")
        (usj_dir / f"{nn}-{book}.json").write_bytes(raw)
        n += 1
    print(f"[openbible] {iso}/{edition}: {n} book(s) → {usj_dir} (bibles' USJ, unchanged)", file=sys.stderr)
    return n


def update_sources(pin: dict, path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[pin["iso"]] = {"provider": pin["provider"], "edition": pin["version_id"], "license_url": pin["license_url"]}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="openbible", choices=ROOTS, help="CDN folder: openbible (catalog source o) or crosswire (c)")
    ap.add_argument("--iso", required=True, help="the language code in the CDN path (the catalog's iso)")
    ap.add_argument("--edition", required=True, help="the catalog's o:<ABBR> value")
    ap.add_argument("--tag", required=True, help="our per-edition tag (pins/sources are keyed by it)")
    ap.add_argument("--to-usj", type=Path, required=True, metavar="DIR")
    ap.add_argument("--book", action="append", help="limit to book(s); repeatable")
    ap.add_argument("--pin", type=Path, default=None)
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    a = ap.parse_args(argv)
    m = meta(a.iso, a.edition, a.root)
    n = to_usj(a.iso, a.edition, a.to_usj, a.book, m, a.root)
    if not n:
        print(f"[openbible] {a.iso}/{a.edition}: no book answered — nothing ingested", file=sys.stderr)
        return 1
    pin = build_pin(a.iso, a.edition, a.tag, m, n, a.root)
    pin_path = a.pin or Path("config/pins") / f"{a.tag}.json"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.sources:
        update_sources(pin, a.sources)
    print(f"[openbible] {a.tag}: {a.edition} ({pin['name']}, {n} books) · license→{pin['license_url']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
