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

TEXT ROUTES (2026-10-17, bcv-commons/bibles). DBT offers an edition's text in several forms; they are NOT the same text at word level
(BIVWBT Acts 13: the plain text keeps `\\rq` references the structured text marks; BVZYSS Luke 18: "fi koadamam" vs "fikoadamam"; CARBSS /
RELBTL / BGTNEW / FFMWBT / MZWNVS: DBT's Sofria is damaged and the USX is canonical). bibles names the CANONICAL text per edition and testament
in catalog/text-route.json (config/dbt_catalog/text-route.json, pinned in PROVENANCE): `sofria` (DBT's `text_json`), `usx` (`text_usx`,
converted to USJ with bibles' usx_to_usj.py), or `plain` (`text_plain`, only where nothing structured exists). We read exactly that route and
fileset. A book that fileset does not have (a partial `text_json`, e.g. BVZYSS: 8 of 27 books) is taken from the next route DOWN the order
sofria > usx > plain that has it, never from a higher one and never when the catalog gives a `why` (damaged Sofria: its plain text is damaged
too); the route used per book is recorded in <dir>/_routes.txt (JSON). (To be confirmed with bibles: the catalog names one route per edition.)
An edition the catalog does not list uses Sofria > USX > plain by what the bible offers. Nothing is converted between formats on our
side: a Sofria book is stored as fetched (`{"format": "sofria", ...}`), plain verses as given (`{"format": "verses", ...}`), USX as the USJ
bibles' converter makes; usj_source reads their verse text with bibles' extractors. Structured books cost ONE API call per book
(`bibles/filesets/{id}?type=text_json|text_usx&book_id=`), which lists a signed CloudFront link per chapter (Sofria) or for the whole book (USX).

Cache fast path: the sibling `audio-sync` repo independently downloads DBT/helloAO chapter text
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
a curated subset, not the ~1,876-language catalog this repo sweeps). On a genuine live-fetch (cache
miss) success, also WRITES the chapter back into that tree (`_write_back_to_audio_sync`, gated on
`--bare-iso` being passed) — see that function's docstring for the reverse-sharing design (confirmed
with audio-sync 2026-08-28); never overwrites an existing file there.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from lexeme_aligner import dns_cache as _dns_cache  # last-known-good DNS fallback; this machine's DNS goes flaky
_dns_cache.install()

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
_PREFERRED_TYPES = ("text_json", "text_usx", "text_plain", "text_format")        # = the route order json > usx > plain (see TEXT ROUTES)
_ROUTE_OF_TYPE = {"text_json": "sofria", "text_usx": "usx", "text_plain": "plain", "text_format": "plain"}
_TYPE_OF_ROUTE = {"sofria": "text_json", "usx": "text_usx"}
TEXT_ROUTE = Path("config/dbt_catalog/text-route.json")                           # bibles' catalog/text-route.json (pinned copy)


def _api_key() -> str:
    key = os.environ.get("BIBLE_API_KEY")
    if not key:
        raise SystemExit("[dbt_source] BIBLE_API_KEY not set — request a free key at "
                          "https://4.dbt.io/api_key/request and put it in .env")
    return key


def _get(path: str, params: dict, retries: int = 8) -> dict:
    """GET a DBP endpoint, key injected, with backoff on transient errors. Paced by
    BIBLE_API_DELAY_MS before every call (see module-level comment) — the single choke point all
    DBT calls go through, so this covers bible_info/book_chapters/chapter_verses uniformly.
    2026-10-01: 8 attempts with backoff capped at 30 s (was 5 attempts, 1-8 s): this machine's DNS goes flaky for
    10-60 s at a time, and a language's whole chain aborted after the old ~15 s of patience (xtm, ydd)."""
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
            time.sleep(min(2 ** attempt, 30))
    raise err


def bible_info(bible_id: str) -> dict:
    """GET /bibles/{bible_id} — metadata + filesets. Raises if the bible_id doesn't exist."""
    try:
        d = _get(f"bibles/{bible_id}", {})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit(f"[dbt_source] '{bible_id}': DBT does not know this bible (404)")
        raise
    if "data" not in d:
        raise SystemExit(f"[dbt_source] '{bible_id}': {d.get('error', d)}")
    return d["data"]


@functools.lru_cache(maxsize=None)
def catalog_routes() -> dict:
    """{bible_id: {"ot"|"nt": {"route", "id", "why"?}}} for the DBT (`d:`) sources of bibles' text-route.json; {} if the file is absent."""
    if not TEXT_ROUTE.exists():
        return {}
    out: dict[str, dict] = {}
    for key, srcs in json.loads(TEXT_ROUTE.read_text(encoding="utf-8")).get("entries", {}).items():
        canon = key.split(":")[1]
        for sid, entry in srcs.items():
            if sid.startswith("d:") and entry.get("id"):
                out.setdefault(sid[2:], {})[canon] = entry
    return out


def text_filesets(info: dict) -> dict:
    """The text fileset(s) to read per testament: {'OT': [(route, fileset_id), ...], 'NT': [...]}. The catalog's canonical route is the
    ONLY candidate when bibles lists the edition (see TEXT ROUTES); otherwise every route the bible offers, Sofria > USX > plain, and a
    book tries them in order."""
    listed = catalog_routes().get(info.get("abbr"))
    all_filesets = [f for group in (info.get("filesets") or {}).values() for f in group]

    def best_for(testament: str) -> list[tuple[str, str]]:
        cands = [f for f in all_filesets if f.get("size") in (testament, testament + "P") or f.get("size") not in ("OT", "NT", "OTP", "NTP")]
        out: list[tuple[str, str]] = []
        for t in _PREFERRED_TYPES:
            route = _ROUTE_OF_TYPE[t]
            for f in sorted((f for f in cands if f.get("type") == t), key=lambda f: f.get("size") not in (testament, testament + "P")):
                if all(r != route for r, _ in out):
                    out.append((route, f["id"]))
        return out

    own = {"OT": best_for("OT"), "NT": best_for("NT")}
    if not listed:
        if not any(own.values()):
            raise SystemExit(f"[dbt_source] '{info.get('abbr')}' has no text fileset (audio/video-only)")
        return own
    order = ("sofria", "usx", "plain")
    picks: dict[str, list[tuple[str, str]]] = {}
    for t in ("OT", "NT"):
        e = listed.get(t.lower())
        if not e:
            picks[t] = []
            continue
        picks[t] = [(e["route"], e["id"])]                                  # the catalog's canonical fileset, even if the bible listing lacks it
        if not e.get("why") and e["route"] in order:                       # lower routes only; none for a damaged-Sofria edition
            picks[t] += [(r, fid) for r, fid in own[t] if r in order and order.index(r) > order.index(e["route"])]
    if not any(picks.values()):
        raise SystemExit(f"[dbt_source] '{info.get('abbr')}': text-route.json lists no OT/NT text")
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


def _download(url: str, retries: int = 5) -> bytes:
    """A signed CloudFront link from the API (valid for minutes): fetched at once, with retries on transient errors."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=90).read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        time.sleep(min(2 ** attempt, 30))
    raise last  # type: ignore[misc]


def book_links(fileset_id: str, route: str, book: str) -> list[dict]:
    """ONE API call: the rows (with a signed `path`) of a structured fileset's book — a Sofria document per chapter (`chapter_start`),
    or one row with the whole book's USX. [] when the fileset does not have the book."""
    try:
        d = _get(f"bibles/filesets/{fileset_id}", {"type": _TYPE_OF_ROUTE[route], "book_id": book})
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return []
        raise
    return [r for r in (d.get("data") or []) if r.get("path")]


DOWNLOAD_THREADS = int(os.environ.get("DBT_DOWNLOAD_THREADS", "6"))        # CloudFront downloads inside one book (the API calls stay paced)


def sofria_book(fileset_id: str, book: str) -> dict | None:
    """The book's Sofria chapters exactly as DBT gives them, in the stored wrapper; None = the fileset has no such book / no chapters."""
    from concurrent.futures import ThreadPoolExecutor
    rows = [r for r in book_links(fileset_id, "sofria", book) if r.get("chapter_start")]
    rows.sort(key=lambda r: int(r["chapter_start"]))

    def fetch(r):
        try:
            return str(int(r["chapter_start"])), json.loads(_download(r["path"]))
        except urllib.error.HTTPError as e:
            if e.code not in (403, 404):
                raise
            return None
    with ThreadPoolExecutor(max(1, DOWNLOAD_THREADS)) as ex:
        chapters = dict(x for x in ex.map(fetch, rows) if x)
    return {"format": "sofria", "book": book, "fileset": fileset_id, "chapters": chapters} if chapters else None


def usx_book(fileset_id: str, book: str) -> dict | None:
    """The book's USX through bibles' usx_to_usj.py (usfmtc; USX and USJ are one document model). None = the fileset has no such book."""
    rows = book_links(fileset_id, "usx", book)
    if not rows:
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "bibles"))
    from usx_to_usj import usx_to_usj
    usj = usx_to_usj(_download(rows[0]["path"]).decode("utf-8"))
    return usj if isinstance(usj, dict) and usj.get("content") else None


def _plain_book(bible_id, fileset_id, book, meta, cached_dirs, bare_iso, stats) -> dict | None:
    """The plain verses of a book as the API (or audio-sync's cache of it) gives them, in the stored wrapper."""
    book_chapter_list = list(meta["chapters"])
    ordered = ["1"] + [c for c in book_chapter_list if c != "1"] if "1" in book_chapter_list else book_chapter_list
    chapters: dict[str, list[dict]] = {}
    for i, ch in enumerate(ordered):
        verses = _cached_chapter_verses(cached_dirs, book, ch)
        if verses is not None:
            stats["cache_hits"] += 1
        else:
            stats["cache_misses"] += 1
            try:
                verses = chapter_verses(fileset_id, book, ch)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                if i == 0:
                    break                      # chapter 1 missing: the book has nothing in this fileset
                continue
            if verses and bare_iso:
                _write_back_to_audio_sync(bare_iso, bible_id, meta["testament"], book, ch, fileset_id, verses)
        if verses:
            chapters[ch] = [{"verse_start": v["verse_start"], "verse_end": v.get("verse_end"), "verse_text": v.get("verse_text") or ""}
                            for v in verses]
    return {"format": "verses", "book": book, "fileset": fileset_id, "chapters": chapters} if chapters else None


def to_usj(bible_id: str, picks: dict, usj_dir: Path, only: list[str] | None,
           bare_iso: str | None = None) -> dict:
    """Fetch every book of a bible in its canonical form (TEXT ROUTES in the module docstring) into <NN>-<BOOK>.json. A book tries its
    testament's routes in order (json > usx > plain, or the edition's override first) and keeps the first that has it. Returns
    {"books": n, "routes": {"sofria": n, ...}, "missing": [books listed by DBT but not fetchable]}."""
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM

    usj_dir.mkdir(parents=True, exist_ok=True)
    books_meta = book_chapters(bible_id)
    wanted = [b for b in books_meta if not only or b in only]
    routes: dict[str, int] = {}
    per_book: dict[str, dict] = {}
    missing: list[str] = []
    stats = {"cache_hits": 0, "cache_misses": 0}
    # Circuit breaker for a bible that is entirely empty (verified live: AYZYSS 404s EVERY chapter). Only when NOTHING at all has come
    # through after 15 books in a row (a partial fileset can start late; ATBWYI's first five books are in none of its filesets' first
    # route). It used to stop after two empty books and so dropped the rest of partial filesets (458 books in 34 editions, 2026-10-17).
    consecutive_empty = 0
    got_any = False
    for book in wanted:
        nn = _BOOK_FILE_NUM.get(book)
        if not nn:
            print(f"[dbt_source] skip {book}: not in NN map", file=sys.stderr)
            continue
        meta = books_meta[book]
        candidates = picks.get(meta["testament"]) or []
        if not candidates:
            print(f"[dbt_source] skip {book}: no fileset covers testament {meta['testament']!r}", file=sys.stderr)
            continue
        if not got_any and consecutive_empty >= 15:
            print(f"[dbt_source] skip {book}: nothing came through for {consecutive_empty} books — treating the bible as empty", file=sys.stderr)
            continue
        doc = None
        cached_dirs = None
        for route, fileset_id in candidates:
            if route == "sofria":
                doc = sofria_book(fileset_id, book)
            elif route == "usx":
                doc = usx_book(fileset_id, book)
            else:
                if cached_dirs is None:
                    cached_dirs = _cached_book_dirs(bible_id, meta["testament"], book)
                doc = _plain_book(bible_id, fileset_id, book, meta, cached_dirs, bare_iso, stats)
            if doc:
                routes[route] = routes.get(route, 0) + 1
                per_book[book] = {"route": route, "fileset": fileset_id}
                break
        if not doc:
            consecutive_empty += 1
            missing.append(book)
            print(f"[dbt_source] skip {book}: no verse text from any route {[r for r, _ in candidates]}", file=sys.stderr)
            continue
        consecutive_empty = 0
        got_any = True
        (usj_dir / f"{nn}-{book}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (usj_dir / "_routes.txt").write_text(json.dumps(per_book, indent=1, sort_keys=True), encoding="utf-8")
    n = sum(routes.values())
    cache_note = f", {stats['cache_hits']} plain chapter(s) from audio-sync cache" if stats["cache_hits"] else ""
    print(f"[dbt_source] {n} book(s) → {usj_dir} · routes {routes}{cache_note}"
          + (f" · NOT fetched: {missing}" if missing else ""), file=sys.stderr)
    return {"books": n, "routes": routes, "missing": missing}


def build_pin(info: dict, picks: dict, iso: str, result: dict | None = None) -> dict:
    publishers = info.get("publishers") or []
    license_url = next((p.get("url_website") for p in publishers if p.get("url_website")), None)
    return {
        "iso": iso,
        "provider": "4.dbt.io (Digital Bible Platform / Bible Brain)",
        "language_name": info.get("language"),   # e.g. "Cebuano" — DBP's own language field
        "bible_id": info.get("abbr"),
        "filesets": {t: [fid for _, fid in c] for t, c in picks.items()},   # the fileset(s) read per testament
        "text_route": {t: c[0][0] for t, c in picks.items() if c},       # canonical route per testament (bibles' catalog/text-route.json)
        "text_route_books": (result or {}).get("routes"),                # books fetched per route: {"sofria": n, "usx": n, "plain": n}
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

    result = to_usj(args.bible_id, picks, args.to_usj, args.book, bare_iso=args.bare_iso)
    pin = build_pin(info, picks, args.iso, result)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if result["books"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
