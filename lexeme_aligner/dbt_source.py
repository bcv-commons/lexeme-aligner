"""DBT ingest adapter — fetch target text from the Digital Bible Platform (DBP) v4 API ("Bible
Brain", Faith Comes By Hearing) → USJ. Pure Python, no Node edge — same recipe-layer shape as
`cdn_source` (PKF) and `helloao_source`.

Unblocks the ~750 catalog-known languages that were DBT-only (cdn.bibel.wiki exposes DBT
*discovery* metadata — this catalog — but never fetchable text; see `catalog_source.py`'s
docstring). Needs a DBP API key (free, request at https://4.dbt.io/api_key/request) in the
`BIBLE_API_KEY` env var (`BIBLE_API_BASE_URL` optional override, default `https://4.dbt.io/api`).

Verified live (2026-07-22) against the actual DBP v4 routes (`github.com/faithcomesbyhearing/dbp`,
`routes/api.php` + `AccessControl` middleware — not guessed):
  - auth: `?key=<key>` query param on every call
  - `GET /bibles/{bible_id}` → `filesets` (a bible_id's fileset_id can DIFFER from the bible_id
    itself, e.g. bible `SPARVC` → fileset `SPNRVC` — always resolve via this call, never assume
    fileset_id == bible_id)
  - `GET /bibles/{bible_id}/book` → per-book `chapters` (list of chapter numbers) — the only
    source-of-truth for how many chapters a book has; the API has no bulk book+chapter fetch
  - `GET /bibles/filesets/{fileset_id}/{book}/{chapter}` → verse text (`book_id, chapter,
    verse_start, verse_end, verse_text`) — the actual fetchable-text endpoint (NOT
    `/bibles/{bible_id}/{book}/{chapter}`, which 404s per-bible; must go through the fileset_id)

    python3 -m lexeme_aligner.dbt_source --bible-id SPARVC --iso spa --to-usj data/usj-spa-rvc
    python3 -m lexeme_aligner.dbt_source --bible-id SPARVC --iso spa --to-usj data/usj-spa-rvc --book RUT
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("BIBLE_API_BASE_URL", "https://4.dbt.io/api")
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
# A whole-Bible edition means hundreds to 1000+ individual calls (book_chapters + one per
# book/chapter — no bulk endpoint exists). No delay at all appears to trigger server-side
# throttling that shows up as multi-minute latency spikes on individual calls (observed empirically
# across several onboarding batches — no official rate-limit is documented). Default matches that
# empirical finding; override via BIBLE_API_DELAY_MS (e.g. "0" to disable).
_REQUEST_DELAY = float(os.environ.get("BIBLE_API_DELAY_MS", "500")) / 1000.0
# Preference order — text_plain/text_format are VERIFIED to work with the chapter-verse endpoint
# (live-tested); text_json/text_usx/text_html are untested there and, for at least one real bible
# (PORNLH), text_usx silently 404s on that endpoint even though the fileset exists. Only fall back
# to them if no plain/format fileset is offered at all.
_PREFERRED_TYPES = ("text_plain", "text_format", "text_json", "text_usx", "text_html")


def _api_key() -> str:
    key = os.environ.get("BIBLE_API_KEY")
    if not key:
        raise SystemExit("[dbt_source] BIBLE_API_KEY not set — request a free key at "
                          "https://4.dbt.io/api_key/request and put it in .env")
    return key


def _get(path: str, params: dict, retries: int = 5) -> dict:
    """GET a DBP endpoint, key injected, with backoff on transient errors. Paced by
    BIBLE_API_DELAY_MS before every call (see module-level comment) — the single choke point all
    DBT calls go through, so this covers bible_info/book_chapters/chapter_verses uniformly."""
    if _REQUEST_DELAY:
        time.sleep(_REQUEST_DELAY)
    q = dict(params)
    q["key"] = _api_key()
    q["v"] = "4"
    url = f"{BASE}/{path}?{urllib.parse.urlencode(q)}"
    err: Exception = RuntimeError("no attempt")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https origin
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise
            err = e
        except (urllib.error.URLError, OSError) as e:
            err = e
        if attempt < retries - 1:
            print(f"[dbt_source] retry {attempt + 1}/{retries - 1} after {err} — {path}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise err


def bible_info(bible_id: str) -> dict:
    """GET /bibles/{bible_id} — metadata + filesets. Raises if the bible_id doesn't exist."""
    d = _get(f"bibles/{bible_id}", {})
    if "data" not in d:
        raise SystemExit(f"[dbt_source] '{bible_id}': {d.get('error', d)}")
    return d["data"]


def text_filesets(info: dict) -> dict:
    """Pick the best fileset for each testament. Some bibles split NT and OT into SEPARATE filesets
    (`size` == 'OT'/'NT' — e.g. PORNLH, live-verified); others have one combined fileset (any other
    `size`, e.g. 'C', 'NTPOTP' — e.g. SPARVC, live-verified); some have BOTH, of differing quality
    (e.g. INDASV: a combined text_plain 'INDASV' AND testament-specific text_usx ones — live-
    verified: preferring the testament-specific one blindly picked the broken text_usx fileset over
    the working combined one). So candidates for a testament are the union of its own testament-
    specific filesets AND any combined (non-OT/NT-sized) ones, ranked by _PREFERRED_TYPES — type
    quality wins regardless of whether the fileset is testament-specific or combined. Returns
    {'OT': fileset_id|None, 'NT': fileset_id|None}."""
    all_filesets = [f for group in (info.get("filesets") or {}).values() for f in group]

    def best_for(testament: str) -> str | None:
        candidates = [f for f in all_filesets
                      if f.get("size") == testament or f.get("size") not in ("OT", "NT")]
        for t in _PREFERRED_TYPES:
            for f in candidates:
                if f.get("type") == t:
                    return f["id"]
        return None

    picks = {"OT": best_for("OT"), "NT": best_for("NT")}
    if not any(picks.values()):
        raise SystemExit(f"[dbt_source] '{info.get('abbr')}' has no text fileset (audio/video-only)")
    return picks


def book_chapters(bible_id: str) -> dict:
    """GET /bibles/{bible_id}/book -> {book_id: {"chapters": [...], "testament": "OT"|"NT"}} — the
    only source of truth for how many chapters each book has (and which testament it's in, needed
    to route to the right fileset); no bulk book+chapter fetch exists on this API."""
    d = _get(f"bibles/{bible_id}/book", {})
    return {b["book_id"]: {"chapters": b["chapters"], "testament": b.get("testament")}
            for b in d.get("data", [])}


def chapter_verses(fileset_id: str, book: str, chapter: int) -> list[dict]:
    """GET /bibles/filesets/{fileset_id}/{book}/{chapter} -> verse dicts (verse_start, verse_text)."""
    d = _get(f"bibles/filesets/{fileset_id}/{book}/{chapter}", {})
    return d.get("data", [])


def _book_usfm(book: str, chapters: dict[int, list[dict]]) -> str:
    out = [f"\\id {book}"]
    for ch, verses in sorted(chapters.items()):
        out += [f"\\c {ch}", "\\p"]
        for v in verses:
            text = (v.get("verse_text") or "").strip()
            if text:
                out.append(f"\\v {v['verse_start']} {text}")
    return "\n".join(out) + "\n"


def to_usj(bible_id: str, picks: dict, usj_dir: Path, only: list[str] | None) -> int:
    """Fetch every book/chapter for a bible and convert to USJ <NN>-<BOOK>.json. Each book is routed
    to its OWN testament's fileset (falling back to 'ALL') — see text_filesets()."""
    try:
        import usfmtc
    except ImportError:
        raise SystemExit("[dbt_source] USFM→USJ needs usfmtc — pip install -e '.[ingest]'")
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM

    usj_dir.mkdir(parents=True, exist_ok=True)
    books_meta = book_chapters(bible_id)
    wanted = [b for b in books_meta if not only or b in only]
    n = 0
    with tempfile.TemporaryDirectory() as td:
        for book in wanted:
            nn = _BOOK_FILE_NUM.get(book)
            if not nn:
                print(f"[dbt_source] skip {book}: not in NN map", file=sys.stderr)
                continue
            meta = books_meta[book]
            fileset_id = picks.get(meta["testament"])
            if not fileset_id:
                print(f"[dbt_source] skip {book}: no fileset covers testament "
                      f"{meta['testament']!r}", file=sys.stderr)
                continue
            chapters = {}
            for ch in meta["chapters"]:
                verses = chapter_verses(fileset_id, book, ch)
                if verses:
                    chapters[ch] = verses
            if not chapters:
                print(f"[dbt_source] skip {book}: no verse text returned "
                      f"(fileset={fileset_id})", file=sys.stderr)
                continue
            uf = Path(td) / f"{book}.usfm"
            uf.write_text(_book_usfm(book, chapters), encoding="utf-8")
            usfmtc.readFile(str(uf)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
            n += 1
    print(f"[dbt_source] {n} book(s) → {usj_dir}", file=sys.stderr)
    return n


def build_pin(info: dict, picks: dict, iso: str) -> dict:
    publishers = info.get("publishers") or []
    license_url = next((p.get("url_website") for p in publishers if p.get("url_website")), None)
    return {
        "iso": iso,
        "provider": "4.dbt.io (Digital Bible Platform / Bible Brain)",
        "language_name": info.get("language"),   # e.g. "Cebuano" — DBP's own language field
        "bible_id": info.get("abbr"),
        "filesets": picks,   # {'OT':..., 'NT':..., 'ALL':...} — see text_filesets()
        "name": info.get("vname") or info.get("name"),
        "copyright": info.get("mark"),
        "license_url": license_url,
    }


def update_sources(pin: dict, path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[pin["iso"]] = {"provider": pin["provider"], "edition": pin["bible_id"],
                       "license_url": pin["license_url"]}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bible-id", required=True, help="DBP bible_id, e.g. SPARVC")
    ap.add_argument("--iso", required=True)
    ap.add_argument("--to-usj", type=Path, required=True, metavar="DIR")
    ap.add_argument("--book", action="append", help="limit to book(s); repeatable")
    ap.add_argument("--pin", type=Path, default=None)
    ap.add_argument("--sources", type=Path, default=Path("data/sources.json"))
    args = ap.parse_args()

    info = bible_info(args.bible_id)
    picks = text_filesets(info)
    pin = build_pin(info, picks, args.iso)
    pin_path = args.pin or Path("data/pins") / f"{args.iso}.json"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.sources:
        update_sources(pin, args.sources)
    print(f"[dbt_source] {args.iso}: {pin['bible_id']} (filesets={picks}, {pin['name']}) · "
          f"license→{pin['license_url']}", file=sys.stderr)

    to_usj(args.bible_id, picks, args.to_usj, args.book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
