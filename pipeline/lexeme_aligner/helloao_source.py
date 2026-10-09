"""helloAO ingest adapter — fetch target text from bible.helloao.org → USJ.

Pure Python, **no Node edge**. One robust fetch of the translation's `complete.json` (the whole
translation — metadata + all books in a single request) → minimal USFM per book → `usfmtc` → USJ.
helloAO carries ~1,256 translations, including many absent from cdn.bibel.wiki's PKF set (e.g. Swedish
`swe_fol`), so this reaches text beyond the 589 PKF languages. Same recipe-layer contract as
`cdn_source`: the text stays at origin, we pin the translation's own `sha256` and link its `licenseUrl`.

    python3 -m lexeme_aligner.helloao_source --translation swe_fol --iso swe --to-usj pipeline/work/ingest-cache/usj-swe
    python3 -m lexeme_aligner.helloao_source --translation swe_fol --iso swe --to-usj pipeline/work/ingest-cache/usj-swe --book RUT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from lexeme_aligner import dns_cache as _dns_cache  # last-known-good DNS fallback; this machine's DNS goes flaky
_dns_cache.install()

API = "https://bible.helloao.org/api"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"


def _get(url: str, retries: int = 5) -> bytes:
    """GET with backoff — a big complete.json can drop mid-stream; retry transient errors (resets,
    timeouts, 5xx/429) but fail fast on real 4xx like 404."""
    err: Exception = RuntimeError("no attempt")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=120) as r:   # noqa: S310 — fixed https origin
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise
            err = e
        except (urllib.error.URLError, OSError) as e:              # incl. ConnectionResetError, timeouts
            err = e
        if attempt < retries - 1:
            print(f"[helloao] retry {attempt + 1}/{retries - 1} after {err} — {url}", file=sys.stderr)
            time.sleep(2 ** attempt)                               # 1,2,4,8s
    raise err


def complete(translation: str) -> dict:
    """Whole translation in one fetch: {'translation': {...meta...}, 'books': [{id, chapters:[…]}]}."""
    d = json.loads(_get(f"{API}/{translation}/complete.json"))
    if "books" not in d:
        raise SystemExit(f"[helloao] '{translation}' has no complete.json books (id typo?)")
    return d


def _verse_text(content: list) -> str:
    """Join a verse's text spans, dropping inline objects ({noteId}, formatting markers)."""
    parts = [x if isinstance(x, str) else (x.get("text") if isinstance(x, dict) else None)
             for x in content]
    return " ".join(p for p in parts if p).strip()


INLINE_NOTES = Path("config/inline_notes.json")
_NOTE = re.compile(r"\\f\s+.*?\\f\*", re.S)
_MARKER = re.compile(r"\\\+?[a-z]+\d*\*?")


def ebible_notes(usfm_zip: Path) -> dict[tuple[str, int, int], list[str]]:
    """{(BOOK, chapter, verse): [note text, ...]} from an eBible USFM zip: every `\\f … \\f*` with its caller and markers
    removed and whitespace collapsed — the text helloAO glues into the verse (`\\fr 2:9 \\fq … \\ft …` -> "2:9 … …")."""
    out: dict[tuple[str, int, int], list[str]] = {}
    with zipfile.ZipFile(usfm_zip) as z:
        for name in z.namelist():
            if not name.endswith(".usfm"):
                continue
            text = z.read(name).decode("utf-8")
            m = re.search(r"^\\id (\w{3})", text, re.M)
            if not m:
                continue
            book, ch, v = m.group(1), 0, 0
            for part in re.split(r"(\\[cv] \d+)", text):           # whole verses: a note may run over several lines (\\fp)
                mc = re.fullmatch(r"\\([cv]) (\d+)", part)
                if mc:
                    if mc.group(1) == "c":
                        ch, v = int(mc.group(2)), 0
                    else:
                        v = int(mc.group(2))
                    continue
                if True:
                    for fn in _NOTE.findall(part):
                        body = re.sub(r"^\\f\s+\S\s*", "", fn[:-3])           # drop "\\f + " and the closing "\\f*"
                        body = " ".join(_MARKER.sub(" ", body).split())
                        if body:
                            out.setdefault((book, ch, v), []).append(body)
    return out


def strip_notes(text: str, notes: list[str]) -> tuple[str, int]:
    """Remove each note text (whitespace-insensitive) from a helloAO verse string; returns (text, notes removed)."""
    n = 0
    for note in notes:
        pat = r"\s*".join(re.escape(w) for w in note.split())
        m = re.search(pat, text)
        if m:
            text = text[:m.start()] + " " + text[m.end():]
            n += 1
    return " ".join(text.split()), n


def load_inline_notes(translation: str, config: Path = INLINE_NOTES) -> dict | None:
    """The edition's eBible footnotes, when config/inline_notes.json lists it (downloaded once, sha256-checked), else None."""
    if not config.exists():
        return None
    e = json.loads(config.read_text(encoding="utf-8")).get(translation)
    if not isinstance(e, dict):
        return None
    zp = Path(e["usfm_zip"])
    if not zp.exists():
        zp.parent.mkdir(parents=True, exist_ok=True)
        zp.write_bytes(_get(e["url"]))
    sha = hashlib.sha256(zp.read_bytes()).hexdigest()
    if sha != e["zip_sha256"]:
        raise SystemExit(f"[helloao] {zp}: sha256 {sha[:12]}… is not the pinned {e['zip_sha256'][:12]}… (eBible changed the file; re-check)")
    return ebible_notes(zp)


def _book_usfm(book: dict, notes: dict | None = None, stats: dict | None = None) -> str:
    """One complete.json book → minimal USFM. Verses go under \\p — not the \\s heading, or usfmtc
    nests them inside it and read_verses skips the whole heading paragraph. Headings are dropped.
    `notes` (load_inline_notes): footnote texts helloAO glued into the verse are removed."""
    out = [f"\\id {book['id']}"]
    for chwrap in book["chapters"]:
        ch = chwrap.get("chapter", chwrap)                        # complete.json wraps: {chapter:{…}}
        out += [f"\\c {ch.get('number')}", "\\p"]
        for item in ch.get("content", []):
            if isinstance(item, dict) and item.get("type") == "verse":
                text = _verse_text(item.get("content", []))
                want = (notes or {}).get((book["id"], int(ch.get("number")), int(item["number"])))
                if want:
                    text, n = strip_notes(text, want)
                    if stats is not None:
                        stats["notes"] = stats.get("notes", 0) + len(want)
                        stats["removed"] = stats.get("removed", 0) + n
                out.append(f"\\v {item['number']} {text}")
    return "\n".join(out) + "\n"


def to_usj(comp: dict, usj_dir: Path, only: list[str] | None, notes: dict | None = None) -> int:
    """complete.json books → USJ <NN>-<BOOK>.json (aligner numbering). Returns book count."""
    try:
        import usfmtc
    except ImportError:
        raise SystemExit("[helloao] USFM→USJ needs usfmtc — pip install -e '.[ingest]'")
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM

    usj_dir.mkdir(parents=True, exist_ok=True)
    by_id = {b["id"]: b for b in comp["books"]}
    wanted = [b for b in by_id if not only or b in only]
    n = 0
    stats: dict = {}
    with tempfile.TemporaryDirectory() as td:
        for book in wanted:
            nn = _BOOK_FILE_NUM.get(book)
            if not nn:
                print(f"[helloao] skip {book}: not in NN map", file=sys.stderr)
                continue
            uf = Path(td) / f"{book}.usfm"
            uf.write_text(_book_usfm(by_id[book], notes, stats), encoding="utf-8")
            usfmtc.readFile(str(uf)).outUsj(str(usj_dir / f"{nn}-{book}.json"))
            n += 1
    if notes is not None:
        print(f"[helloao] inline footnotes (config/inline_notes.json): {stats.get('removed', 0)} of {stats.get('notes', 0)} "
              f"eBible notes found in the verse text and removed", file=sys.stderr)
    print(f"[helloao] {n} book(s) → {usj_dir}", file=sys.stderr)
    return n


def build_pin(meta: dict, iso: str) -> dict:
    return {
        "iso": iso,
        "provider": "bible.helloao.org",
        "language_name": meta.get("languageEnglishName"),   # e.g. "Cebuano" — distinct from meta['name'],
        # which is the translation's own title (e.g. "Balaan nga Bibliya"), not the language name.
        "version_id": meta["id"],
        "sha256": meta.get("sha256"),
        "books": meta.get("numberOfBooks"),
        "license_url": meta.get("licenseUrl") or meta.get("website"),
        "name": meta.get("name"),
    }


def update_sources(pin: dict, path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc[pin["iso"]] = {"provider": pin["provider"], "edition": pin["version_id"],
                       "license_url": pin["license_url"]}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--translation", required=True, help="helloAO id, e.g. swe_fol")
    ap.add_argument("--iso", required=True)
    ap.add_argument("--to-usj", type=Path, required=True, metavar="DIR")
    ap.add_argument("--book", action="append", help="limit to book(s); repeatable")
    ap.add_argument("--pin", type=Path, default=None)
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    args = ap.parse_args()

    comp = complete(args.translation)
    pin = build_pin(comp["translation"], args.iso)
    pin_path = args.pin or Path("config/pins") / f"{args.iso}.json"
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    pin_path.write_text(json.dumps(pin, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.sources:
        update_sources(pin, args.sources)
    print(f"[helloao] {args.iso}: {pin['version_id']} ({pin['name']}, {pin['books']} books) · "
          f"license→{pin['license_url']} · sha256={(pin['sha256'] or '')[:12]}…", file=sys.stderr)

    to_usj(comp, args.to_usj, args.book, load_inline_notes(args.translation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
