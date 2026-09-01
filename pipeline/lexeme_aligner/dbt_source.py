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

    python3 -m lexeme_aligner.dbt_source --bible-id SPARVC --iso spa --to-usj pipeline/work/ingest-cache/usj-spa-rvc
    python3 -m lexeme_aligner.dbt_source --bible-id SPARVC --iso spa --to-usj pipeline/work/ingest-cache/usj-spa-rvc --book RUT

Read-only fast path: the sibling `audio-sync` repo independently downloads DBT/helloAO chapter text
for its own audio-timing work into `downloads/BB/{ot,nt}/{iso}/{bible_id}/{book}/
{BOOK}_{chapter:03d}_{fileset_id}.raw.json` (a `{source, fileset_id, verses:[{verse_start, verse_end,
verse_text}]}` sidecar it added at our request — see internal-docs handover, 2026-08-28). When a
matching file exists there for the (bible_id, book, chapter) we're about to fetch, we use it instead
of hitting the live DBP API — same verse-level shape `chapter_verses()` would have returned, just
pre-fetched. Matched on bible_id alone (globbing across audio-sync's iso path segment), NOT our own
`--iso` — onboard.py actually passes our per-edition tag there (a slug of the bible_id), not the bare
ISO audio-sync's directories are keyed by, so bible_id is the only reliably shared key between the
two repos' independent naming. Falls back to the live call whenever the sibling repo, that edition,
or that specific chapter isn't cached there (most of the catalog won't be — audio-sync's coverage is
a curated subset, not the ~1,876-language catalog this repo sweeps). Never writes into that tree.
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
# audio-sync's pre-fetched chapter cache (see module docstring). Overridable for other machines/
# layouts; absent entirely is the common case for anyone without that sibling repo checked out —
# every lookup against it just no-ops back to the live API.
AUDIO_SYNC_BB = Path(os.environ.get("AUDIO_SYNC_BB_DIR",
                                     "/home/lgunnars/dev/bcv-commons/audio-sync/downloads/BB"))
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


def _cached_book_dirs(bible_id: str, testament: str, book: str) -> list[Path]:
    """Resolve which audio-sync downloads/BB director(y/ies) hold this bible_id's cached text for
    this book, if any. Matched on bible_id alone (a globally-unique DBP identifier), globbing across
    the iso path segment — deliberately NOT matched against our own `--iso`, which onboard.py
    actually populates with our per-edition TAG (a slug of the bible_id, e.g. 'khkntp'), not the bare
    ISO audio-sync keys its directories by (e.g. 'khk'); bible_id alone is sufficient to identify the
    edition and sidesteps that mismatch entirely. Called once per book, not once per chapter — the
    per-chapter lookup below reuses the result."""
    canon = {"OT": "ot", "NT": "nt"}.get(testament)
    if canon is None or not AUDIO_SYNC_BB.is_dir():
        return []
    return list(AUDIO_SYNC_BB.glob(f"{canon}/*/{bible_id}/{book}"))


def _cached_chapter_verses(book_dirs: list[Path], book: str, chapter: str) -> list[dict] | None:
    """Look up one chapter's pre-fetched verses across the book dirs `_cached_book_dirs` resolved.
    Returns None on ANY miss or doubt (no match, no file, unparseable JSON) — callers fall back to
    the live API, so a bad/absent cache entry never blocks real work, just skips the shortcut."""
    if not book_dirs:
        return None
    try:
        chapter_str = f"{int(chapter):03d}"
    except (TypeError, ValueError):
        return None
    for book_dir in book_dirs:
        for f in book_dir.glob(f"{book}_{chapter_str}_*.raw.json"):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                verses = doc.get("verses")
                if verses:
                    return verses
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _atomic_write(path: Path, data: str) -> None:
    """Write `data` to `path` without a reader ever observing a partial file — build in a temp file
    in the SAME directory (so the final os.replace is on one filesystem, hence atomic), then rename
    into place. Required here because audio-sync's own `audio-sync-batch.service` writes into this
    same tree continuously — confirmed with them (2026-08-28) that they hardened their own writers to
    match this exact pattern in response, so both sides are now atomic-write."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_back_to_audio_sync(bare_iso: str, bible_id: str, testament: str, book: str, chapter: str,
                               fileset_id: str, verses: list[dict]) -> None:
    """Contribute a chapter we just fetched LIVE (a cache miss) back into audio-sync's downloads/BB
    tree, in their own format, so their audio-timing work benefits from languages our full-catalog
    sweep reaches that they haven't touched yet. Confirmed design with them (2026-08-28) — see
    CLAUDE.local.md. Only ever called on a genuine cache miss (never overwrites/touches an existing
    cache hit), and only if the destination doesn't already exist (a lost race against their own
    concurrent downloader is harmless — both sides would be writing the same chapter fetched from the
    same upstream API, so a redundant write is identical bytes, never divergent content)."""
    canon = {"OT": "ot", "NT": "nt"}.get(testament)
    if canon is None or not AUDIO_SYNC_BB.is_dir():
        return
    try:
        chapter_str = f"{int(chapter):03d}"
    except (TypeError, ValueError):
        return
    book_dir = AUDIO_SYNC_BB / canon / bare_iso / bible_id / book
    stem = f"{book}_{chapter_str}_{fileset_id}"
    txt_path = book_dir / f"{stem}.txt"
    raw_path = book_dir / f"{stem}.raw.json"
    if txt_path.exists() or raw_path.exists():
        return
    book_dir.mkdir(parents=True, exist_ok=True)
    # Normalize to just the 3 agreed fields — the live DBP response carries several more
    # (book_id, book_name, chapter, *_alt variants) that aren't part of the shared contract.
    norm_verses = [{"verse_start": v.get("verse_start"), "verse_end": v.get("verse_end"),
                     "verse_text": v.get("verse_text")} for v in verses]
    txt = "\n".join((v.get("verse_text") or "") for v in verses) + "\n"
    raw = json.dumps({"source": "dbt", "fileset_id": fileset_id, "verses": norm_verses},
                      indent=2, ensure_ascii=False) + "\n"
    try:
        _atomic_write(txt_path, txt)
        _atomic_write(raw_path, raw)
    except OSError as e:
        print(f"[dbt_source] write-back to audio-sync failed for {stem}: {e}", file=sys.stderr)


def _book_usfm(book: str, chapters: dict[int, list[dict]]) -> str:
    out = [f"\\id {book}"]
    for ch, verses in sorted(chapters.items()):
        out += [f"\\c {ch}", "\\p"]
        for v in verses:
            text = (v.get("verse_text") or "").strip()
            if text:
                out.append(f"\\v {v['verse_start']} {text}")
    return "\n".join(out) + "\n"


def to_usj(bible_id: str, picks: dict, usj_dir: Path, only: list[str] | None,
           bare_iso: str | None = None) -> int:
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
    cache_hits = 0
    cache_misses = 0
    # Circuit breaker for a fileset that's entirely 404 (verified live: AYZYSS returned 404 for
    # EVERY chapter of the first book tried) — without this, the per-chapter 404-skip above would
    # dutifully grind through every chapter of every remaining book one at a time (each paced
    # BIBLE_API_DELAY_MS apart) before giving up, burning many minutes on a fileset that was never
    # going to produce anything. Two signals, both fast:
    #  1. Per book: translators fill in chapter 1 before chapter 2 — if chapter 1 itself 404s, the
    #     book has no content yet and there's no point probing the rest of its chapters one at a
    #     time. Try chapter 1 first; a 404 there aborts the whole book immediately (chapters list
    #     order doesn't have to start at "1" — we look it up explicitly rather than assuming index 0).
    #  2. Per fileset: two CONSECUTIVE books that both fail this way (not just one — a single
    #     genuinely-missing book is normal) marks the fileset dead for the rest of this run.
    dead_filesets: set[str] = set()
    consecutive_empty: dict[str, int] = {}
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
            if fileset_id in dead_filesets:
                print(f"[dbt_source] skip {book}: fileset {fileset_id} produced nothing for "
                      f"2 consecutive books — treating as dead, not trying further", file=sys.stderr)
                continue
            book_chapter_list = list(meta["chapters"])
            ordered = book_chapter_list
            if "1" in book_chapter_list:
                ordered = ["1"] + [c for c in book_chapter_list if c != "1"]
            cached_dirs = _cached_book_dirs(bible_id, meta["testament"], book)
            chapters = {}
            for i, ch in enumerate(ordered):
                # A single chapter 404ing later in the book is real and NOT rare — book_chapters()'s
                # own chapter list is sometimes stale/wrong relative to what's actually fetchable
                # per-chapter (verified live: BBCLAI's own chapter listing included at least one
                # chapter the fileset endpoint 404s on). _get() already treats 4xx (except 429) as
                # non-retryable — correctly, a 404 here means "this specific chapter genuinely isn't
                # there", not a transient blip. But chapter 1 (tried first, see above) is different:
                # if it 404s, the book has nothing yet — bail out of the whole book instead of
                # dutifully probing every remaining chapter one at a time.
                verses = _cached_chapter_verses(cached_dirs, book, ch)
                if verses is not None:
                    cache_hits += 1
                else:
                    cache_misses += 1
                    try:
                        verses = chapter_verses(fileset_id, book, ch)
                    except urllib.error.HTTPError as e:
                        if e.code != 404:
                            raise
                        print(f"[dbt_source] skip {book} {ch}: 404 (chapter listed but not fetchable)",
                              file=sys.stderr)
                        if i == 0:
                            break
                        continue
                    if verses and bare_iso:
                        _write_back_to_audio_sync(bare_iso, bible_id, meta["testament"], book, ch,
                                                   fileset_id, verses)
                if verses:
                    chapters[ch] = verses
            if not chapters:
                consecutive_empty[fileset_id] = consecutive_empty.get(fileset_id, 0) + 1
                if consecutive_empty[fileset_id] >= 2:
                    dead_filesets.add(fileset_id)
                print(f"[dbt_source] skip {book}: no verse text returned "
                      f"(fileset={fileset_id})", file=sys.stderr)
                continue
            consecutive_empty[fileset_id] = 0
            uf = Path(td) / f"{book}.usfm"
            uf.write_text(_book_usfm(book, chapters), encoding="utf-8")
            usfmtc.readFile(str(uf)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
            n += 1
    cache_note = f", {cache_hits} chapter(s) from audio-sync cache" if cache_hits else ""
    print(f"[dbt_source] {n} book(s) → {usj_dir}{cache_note}", file=sys.stderr)
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
    ap.add_argument("--bare-iso", default=None,
                     help="Bare ISO (e.g. 'khk'), distinct from --iso which is actually our "
                          "per-edition tag. Enables both the audio-sync cache read AND write-back — "
                          "omit to disable write-back (read-side caching works via --bible-id alone "
                          "regardless of this flag).")
    ap.add_argument("--to-usj", type=Path, required=True, metavar="DIR")
    ap.add_argument("--book", action="append", help="limit to book(s); repeatable")
    ap.add_argument("--pin", type=Path, default=None)
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    args = ap.parse_args()

    info = bible_info(args.bible_id)
    picks = text_filesets(info)
    pin = build_pin(info, picks, args.iso)
    pin_path = args.pin or Path("config/pins") / f"{args.iso}.json"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.sources:
        update_sources(pin, args.sources)
    print(f"[dbt_source] {args.iso}: {pin['bible_id']} (filesets={picks}, {pin['name']}) · "
          f"license→{pin['license_url']}", file=sys.stderr)

    to_usj(args.bible_id, picks, args.to_usj, args.book, bare_iso=args.bare_iso)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
