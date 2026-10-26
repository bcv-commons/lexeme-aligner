"""BSB Translation Tables (bereanbible.com/bsb_tables.tsv) → full-align rows, losslessly, with the reverse
direction planned in (docs/data-structures.md §4, the BSB round-trip source).

The table is the Berean Standard Bible publisher's master interlinear: ONE tsv (85.5 MB, utf-8-sig, 23
columns, header row), the 39 OT books (Hebrew rows, `Greek Sort = 0`) followed by the 27 NT books (Greek
rows, `Heb Sort = 999999`), sorted by `BSB Sort` (English reading order) throughout. Every row is one BSB
word/phrase slot paired with at most one source word; `Heb Sort`/`Greek Sort` give the SOURCE order — the
three sort keys *are* the alignment. ~40% of rows are padding (sort keys + verse only, no source, no
text); they are kept, because dropping them makes the file unreproducible. Everything upstream of us in
BSB-publishing/bsb-data-output (`base/display/`, the per-book tsvs) is a lossy derivative of this file.

Column disposition (nothing discarded as DATA; only HTML *tags* are replaced by their structured meaning):
  sort keys, Verse, Language, VerseId      verbatim
  WLC/Nestle base (col 5)                   verbatim   `base`
  col 6 with witness brackets               verbatim   `base_variants`  ({TR} ⧼RP⧽ (WH) 〈NE〉 [NA] ‹SBL› [[ECM]])
  Translit, Parsing (short)                 verbatim
  Parsing (long)                            kept ONCE as a short→long table (`parsing_table.json`), regenerated
  Str Heb / Str Grk                         raw kept + normalized `strong` (H0430 / G0976)
  ` BSB version `                           verbatim, surrounding spaces included (rendering data)
  pnc, begQ, endQ, Space                    verbatim
  footnotes                                 text, `<i>…</i>` → `*…*`
  Hdg                                       {tag, cls, text}          (no tags)
  Crossref                                  [{text, href}, …]         (no tags)
  Par                                       [{tag, cls}, …]           `span class=|red|` = red-letter, content
  End text                                  {text, close: [tag, …]}
Anything an HTML parser here cannot account for is kept under `unparsed` (and counted) rather than dropped.

Mapping onto our own coordinates (both reported, never guessed):
  source: BSB row → spine WORD (2026-10-26, `map_source`): a table row is one source word, so rows (Heb Sort / Greek Sort
          order) are aligned to MACULA words (key[:11]) in order, on Strong's number and spelling together, and a row takes
          every token of its word (a Hebrew word's prefix, stem and suffix tokens). `match`: strong / spelling (same place and
          spelling, another Strong's number) / fused (second row on a token the spine fused from two words) / none (a word the
          spine's text does not have). Before 2026-10-26 rows were keyed on (Strong's, k-th occurrence) and prefixes/suffixes
          attached by adjacency, which swapped repeated numbers and gave suffixes to the next word (bsb-add-books B2, B4).
  target: ` BSB version ` spans in BSB Sort order → our `engbsb` token positions, by the same letters-run
          walk `pos_score.map_positions` uses (our text IS the BSB text); "vvv" (BSB's own folded-content
          placeholder) and letterless spans ("-") map to no position; a verse whose spans do not tile our
          tokens is REFUSED (all its rows get `t_idx = None`) and counted.

Reverse direction (`--emit-tsv`, experimental): regenerates all 23 columns from the records + the parsing
table. The build reports a round-trip on a fixed sample against the raw rows, going THROUGH the written
Parquet (read back, not in-memory): exact on the 19 non-HTML columns, exact and tag-stripped/whitespace-
collapsed rates on Hdg/Crossref/Par/End text.

Where it lands (docs/data-structures.md §4, the published full-align layout): `--build` writes DIRECTLY into
`publish/full-align/eng/engbsb/manual/BSB-tables/<BOOK>.parquet` (a second manual partition beside Clear's
`manual/BSB/`), the per-spine-token sidecar under `.../BSB-tables/sidecar/<BOOK>.parquet`, and
`parsing_table.json` + `layer_manifest.json` next to them; then it MERGES its partition entry into
`publish/full-align/manifest.json` (atomic re-read/update/replace — `gold_to_fullalign.py` owns that file
and carries partitions marked `owner: "bsb_tables"` over when it regenerates a language) and regenerates
the dataset card through `gold_to_fullalign.write_card`, so there is exactly one README writer.
`pipeline/work/full-align-bsb/` was the staging tree of the first build; `--out-dir` still accepts it.
The `bsb` column is a proper nested Parquet struct (`bsb_struct_type()`): lists of structs where the
source was a list, structs where a dict, `*_unparsed` string siblings for the rare HTML a parser here
could not account for — never JSON-in-a-string.

License: berean.bible/licensing.htm — "The Berean Bible and Majority Bible texts are officially placed
into the public domain as of April 30, 2023." (read at fetch time, recorded in the pin sidecar); the same
text is distributed by BSB-publishing as CC0-1.0, which is what attribution carries.

    python -m lexeme_aligner.eval.bsb_tables --fetch
    python -m lexeme_aligner.eval.bsb_tables --build            # all 66 books → publish/full-align/eng/engbsb/manual/BSB-tables/
    python -m lexeme_aligner.eval.bsb_tables --emit-tsv GEN --out /tmp/GEN.tsv
"""
from __future__ import annotations

import argparse
import collections
import difflib
import datetime as _dt
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

from lexeme_aligner.refs import BOOK_NUMBERS, encode

URL = "https://bereanbible.com/bsb_tables.tsv"
VENDOR_DIR = Path("pipeline/vendor/bsb/tables")
VENDOR_TSV = VENDOR_DIR / "bsb_tables.tsv"
PIN_FILE = VENDOR_DIR / "bsb_tables.pin.json"
OUT_DIR = Path("publish/full-align")                # the published full-align root (docs/data-structures.md §4)
STAGING_DIR = Path("pipeline/work/full-align-bsb")   # the first build's staging tree — still accepted via --out-dir
_SOURCES_FILE = Path("config/sources.json")
_INGEST_ROOT = Path("pipeline/work/ingest-cache")


def _resolve_bsb_usj_dir(sources_path: Path = _SOURCES_FILE, ingest_root: Path = _INGEST_ROOT,
                         prefer: str = "engbsb") -> Path:
    """Which ingest-cache directory actually holds BSB's text — resolved from config/sources.json's own
    tag->edition mapping (`sources[tag]["edition"] == "BSB"`) rather than a hardcoded tag name. Fixed
    2026-09-28: this used to be the literal constant `usj-engbsb`, which silently stopped matching once
    a production regeneration started writing the SAME text under tag `bsb` instead (config/sources.json
    already registers both `bsb` and `engbsb` against `edition: "BSB"` — verified byte-identical text,
    the historical drift documented in this session's own notes, not a new source). `prefer` keeps
    picking `engbsb` first for as long as it still exists on disk (no behavior change today — both
    currently exist), falling back to any other tag sharing the same edition (sorted, so the choice is
    deterministic) the moment `engbsb`'s own directory is ever cleaned up or stops being maintained.
    Does NOT touch `EDITION`/`LANG`'s own published path (`publish/full-align/eng/engbsb/...` already
    has real published data under that exact name — renaming it would orphan that path, not fix a
    lookup) — only WHERE we read the source text from changes here, never where we publish to."""
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    candidates = sorted(tag for tag, v in sources.items() if (v.get("edition") or "").upper() == "BSB")
    if prefer in candidates and (ingest_root / f"usj-{prefer}").exists():
        return ingest_root / f"usj-{prefer}"
    for tag in candidates:
        if (ingest_root / f"usj-{tag}").exists():
            return ingest_root / f"usj-{tag}"
    return ingest_root / f"usj-{prefer}"   # last resort: the historical default, even if absent


USJ_DIR = _resolve_bsb_usj_dir()
EDITION = "engbsb"
LANG = "eng"
PARTITION = "BSB-tables"                             # manual/<partition>/ — beside Clear's manual/BSB/
OWNER = "bsb_tables"                                 # manifest marker gold_to_fullalign.py carries over on rerun


_TITLE_SKIP = 24     # leading rows a verse may leave out when they are the psalm title (long titles run to ~20 rows; see map_target)


def layer_dir(out_dir: Path = OUT_DIR) -> Path:
    return out_dir / LANG / EDITION / "manual" / PARTITION
LICENSE_STATEMENT = ("The Berean Bible and Majority Bible texts are officially placed into the public "
                     "domain as of April 30, 2023.")

# The 23 columns, in file order, and the short keys the records use for them.
COLUMNS = ["Heb Sort", "Greek Sort", "BSB Sort", "Verse", "Language",
           "WLC / Nestle Base TR RP WH NE NA SBL",
           "WLC / Nestle Base {TR} ⧼RP⧽ (WH) 〈NE〉 [NA] ‹SBL› [[ECM]]",   # NE brackets are U+2329/U+232A in the file (not U+3008/9)
           "Translit", "Parsing", "Parsing", "Str Heb", "Str Grk", "VerseId", "Hdg", "Crossref", "Par",
           "Space", "begQ", " BSB version ", "pnc", "endQ", "footnotes", "End text"]
KEYS = ["heb_sort", "grk_sort", "bsb_sort", "verse_num", "language", "base", "base_variants", "translit",
        "parsing", "parsing_long", "str_heb", "str_grk", "verse_id", "hdg", "crossref", "par", "space",
        "beg_q", "text", "pnc", "end_q", "footnotes", "end_text"]
HTML_KEYS = ("hdg", "crossref", "par", "end_text")

BOOK_NAMES = {
    "Genesis": "GEN", "Exodus": "EXO", "Leviticus": "LEV", "Numbers": "NUM", "Deuteronomy": "DEU",
    "Joshua": "JOS", "Judges": "JDG", "Ruth": "RUT", "1 Samuel": "1SA", "2 Samuel": "2SA",
    "1 Kings": "1KI", "2 Kings": "2KI", "1 Chronicles": "1CH", "2 Chronicles": "2CH", "Ezra": "EZR",
    "Nehemiah": "NEH", "Esther": "EST", "Job": "JOB", "Psalm": "PSA", "Psalms": "PSA", "Proverbs": "PRO",
    "Ecclesiastes": "ECC", "Song of Solomon": "SNG", "Song of Songs": "SNG", "Canticles": "SNG",
    "Isaiah": "ISA", "Jeremiah": "JER", "Lamentations": "LAM", "Ezekiel": "EZK", "Daniel": "DAN",
    "Hosea": "HOS", "Joel": "JOL", "Amos": "AMO", "Obadiah": "OBA", "Jonah": "JON", "Micah": "MIC",
    "Nahum": "NAM", "Habakkuk": "HAB", "Zephaniah": "ZEP", "Haggai": "HAG", "Zechariah": "ZEC",
    "Malachi": "MAL", "Matthew": "MAT", "Mark": "MRK", "Luke": "LUK", "John": "JHN", "Acts": "ACT",
    "Romans": "ROM", "1 Corinthians": "1CO", "2 Corinthians": "2CO", "Galatians": "GAL",
    "Ephesians": "EPH", "Philippians": "PHP", "Colossians": "COL", "1 Thessalonians": "1TH",
    "2 Thessalonians": "2TH", "1 Timothy": "1TI", "2 Timothy": "2TI", "Titus": "TIT", "Philemon": "PHM",
    "Hebrews": "HEB", "James": "JAS", "1 Peter": "1PE", "2 Peter": "2PE", "1 John": "1JN", "2 John": "2JN",
    "3 John": "3JN", "Jude": "JUD", "Revelation": "REV", "Revelation of John": "REV",
}
_CODE_TO_NAME = {}
for _n, _c in BOOK_NAMES.items():                       # first (canonical) name wins for reversal
    _CODE_TO_NAME.setdefault(_c, _n)


# --- fetch --------------------------------------------------------------------------------------------
def fetch(dest: Path = VENDOR_TSV, url: str = URL, pin_file: Path = PIN_FILE) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "lexeme-aligner/bsb_tables"})
    h = hashlib.sha256()
    n = 0
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
            n += len(chunk)
    pin = {"url": url, "sha256": h.hexdigest(), "bytes": n,
           "fetched": _dt.date.today().isoformat(),
           "license": "Public Domain (CC0-1.0 as distributed by BSB-publishing)",
           "license_statement": LICENSE_STATEMENT,
           "license_source": "https://berean.bible/licensing.htm"}
    pin_file.write_text(json.dumps(pin, indent=1) + "\n", encoding="utf-8")
    return pin


def load_pin(pin_file: Path = PIN_FILE) -> dict:
    return json.loads(pin_file.read_text(encoding="utf-8")) if pin_file.exists() else {}


# --- HTML columns → structure, and back --------------------------------------------------------------
_TAG_OPEN = re.compile(r"<(p|span) class=\|([^|]*)\|>")
_ANCHOR = re.compile(r"<a href =\|([^|]*)\|>(.*?)</a>", re.S)
_CROSSREF = re.compile(r"^<br /><span class=\|cross\|>\((.*)\)</span>$", re.S)
_CLOSE = re.compile(r"</(\w+)>")
_ITALIC = re.compile(r"<i>(.*?)</i>", re.S)


def parse_hdg(s: str) -> dict | None:
    if not s:
        return None
    m = _TAG_OPEN.match(s)
    if m and not _TAG_OPEN.search(s, m.end()) and "<" not in s[m.end():]:
        return {"tag": m.group(1), "cls": m.group(2), "text": s[m.end():]}
    return {"unparsed": s}


def emit_hdg(d: dict | None) -> str:
    if not d:
        return ""
    if "unparsed" in d:
        return d["unparsed"]
    return f"<{d['tag']} class=|{d['cls']}|>{d['text']}"


def parse_crossref(s: str) -> list | dict | None:
    if not s:
        return None
    m = _CROSSREF.match(s)
    if not m:
        return {"unparsed": s}
    inner = m.group(1)
    refs = [{"text": t, "href": h} for h, t in _ANCHOR.findall(inner)]
    if _ANCHOR.sub("", inner).replace("; ", "") != "":     # something other than "; "-joined anchors
        return {"unparsed": s}
    return refs


def emit_crossref(v) -> str:
    if not v:
        return ""
    if isinstance(v, dict):
        return v["unparsed"]
    return "<br /><span class=|cross|>(" + "; ".join(f"<a href =|{r['href']}|>{r['text']}</a>" for r in v) + ")</span>"


def parse_par(s: str) -> list | dict | None:
    if not s:
        return None
    tags = [{"tag": t, "cls": c} for t, c in _TAG_OPEN.findall(s)]
    if _TAG_OPEN.sub("", s) != "":
        return {"unparsed": s}
    return tags


def emit_par(v) -> str:
    if not v:
        return ""
    if isinstance(v, dict):
        return v["unparsed"]
    return "".join(f"<{t['tag']} class=|{t['cls']}|>" for t in v)


def parse_end_text(s: str) -> list | dict | None:
    """Ordered segments — text and closing tags interleave ('’</span>”'), so order is data."""
    if not s:
        return None
    segs = []
    for part in re.split(r"(</\w+>)", s):
        if not part:
            continue
        m = re.fullmatch(r"</(\w+)>", part)
        if m:
            segs.append({"close": m.group(1)})
        elif "<" in part:
            return {"unparsed": s}
        else:
            segs.append({"text": part})
    return segs


def emit_end_text(v) -> str:
    if not v:
        return ""
    if isinstance(v, dict):
        return v["unparsed"]
    return "".join(f"</{g['close']}>" if "close" in g else g["text"] for g in v)


def parse_footnote(s: str) -> str | dict | None:
    if not s:
        return None
    t = _ITALIC.sub(lambda m: f"*{m.group(1)}*", s)
    if "<" in t:
        return {"unparsed": s}
    return t


def emit_footnote(v) -> str:
    if not v:
        return ""
    if isinstance(v, dict):
        return v["unparsed"]
    return re.sub(r"\*(.*?)\*", lambda m: f"<i>{m.group(1)}</i>", v, flags=re.S)


def norm_strong(raw: str, lang: str) -> tuple[str | None, str | None]:
    """"430" (Hebrew) → ("H0430", None); "5445a" → ("H5445", "a"); blank → (None, None)."""
    raw = raw.strip()
    if not raw:
        return None, None
    m = re.match(r"^(\d+)([a-zA-Z]?)$", raw)
    if not m:
        return None, raw
    return f"{'G' if lang == 'Greek' else 'H'}{int(m.group(1)):04d}", (m.group(2) or None)


# --- rows ---------------------------------------------------------------------------------------------
def parse_row(cols: list[str]) -> dict:
    """One tsv row (23 cells) → structured record. Raw HTML columns are parsed; everything else verbatim."""
    if len(cols) != len(COLUMNS):
        raise ValueError(f"expected {len(COLUMNS)} columns, got {len(cols)}")
    r = dict(zip(KEYS, cols))
    # Sort keys stay STRINGS: 67 Hebrew rows carry fractional inserted-row keys ("8132.5", "106465.1") — an
    # int would corrupt them and break the round trip. Order numerically via `sort_key()`.
    r["verse_num"] = int(r["verse_num"])
    r["hdg"] = parse_hdg(r["hdg"])
    r["crossref"] = parse_crossref(r["crossref"])
    r["par"] = parse_par(r["par"])
    r["end_text"] = parse_end_text(r["end_text"])
    r["footnotes"] = parse_footnote(r["footnotes"])
    # 2026-10-26: Aramaic rows (DAN, EZR, JER 10:11, GEN 31:47) carry their number in Str Heb, like Hebrew ones; testing
    # == "Hebrew" read the empty Greek column and left every Aramaic row without a Strong's number
    r["strong"], r["strong_suffix"] = norm_strong(r["str_grk"] if r["language"] == "Greek" else r["str_heb"],
                                                  r["language"])
    r["kind"] = "empty" if not r["base"].strip() and not r["text"].strip() else "row"
    return r


def sort_key(v: str) -> float:
    return float(v)


def emit_row(r: dict, parsing_table: dict[str, str]) -> list[str]:
    """Inverse of parse_row: the 23 cells, in file order."""
    return [r["heb_sort"], r["grk_sort"], r["bsb_sort"], str(r["verse_num"]), r["language"],
            r["base"], r["base_variants"], r["translit"], r["parsing"],
            r.get("parsing_long") if r.get("parsing_long") is not None else parsing_table.get(r["parsing"], ""),
            r["str_heb"], r["str_grk"], r["verse_id"], emit_hdg(r["hdg"]), emit_crossref(r["crossref"]),
            emit_par(r["par"]), r["space"], r["beg_q"], r["text"], r["pnc"], r["end_q"],
            emit_footnote(r["footnotes"]), emit_end_text(r["end_text"])]


def read_rows(path: Path = VENDOR_TSV):
    """Yield parsed rows with `book`/`chapter`/`verse` filled in (VerseId appears only on a verse's first
    row; it is propagated by `verse_num`) and `_raw` = the 23 original cells (for the round-trip check).
    A VerseId naming an unknown book raises rather than guessing."""
    cur: dict[int, tuple[str, int, int]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
        if header != COLUMNS:
            raise SystemExit(f"[bsb_tables] unexpected header: {header}")
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            cols = line.split("\t")
            r = parse_row(cols)
            r["_raw"] = cols
            if r["verse_id"].strip():
                name, cv = r["verse_id"].strip().rsplit(" ", 1)
                code = BOOK_NAMES.get(name)
                if code is None:
                    raise SystemExit(f"[bsb_tables] unknown book name in VerseId: {r['verse_id']!r}")
                ch, v = cv.split(":")
                cur[r["verse_num"]] = (code, int(ch), int(v))
            bcv = cur.get(r["verse_num"])
            r["book"], r["chapter"], r["verse"] = bcv if bcv else (None, None, None)
            yield r


def build_parsing_table(rows) -> tuple[dict[str, str], list[tuple[str, set[str]]]]:
    """short → long, plus the shorts that map to more than one long (a non-function — reported, not forced)."""
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        if r["parsing"].strip():                      # a blank short never enters the table (its long, if
            seen[r["parsing"]][r["parsing_long"]] += 1   # any, is kept per row as an override instead)
    table = {k: c.most_common(1)[0][0] for k, c in seen.items()}
    exceptions = [(k, set(c)) for k, c in seen.items() if len(c) > 1]
    return table, exceptions


def parsing_long_override(r: dict, table: dict[str, str]) -> str | None:
    """The row's own `Parsing (long)` cell when it is NOT what the table would regenerate (a non-function
    short, or a long with a blank short) — else None. Stored per row so the round trip stays exact."""
    return None if table.get(r["parsing"], "") == r["parsing_long"] else r["parsing_long"]


# --- mapping -------------------------------------------------------------------------------------------
def _letters(s: str) -> str:
    from lexeme_aligner.eval.benchmark import norm_surface
    return "".join(c for c in norm_surface(s) if unicodedata.category(c)[0] in ("L", "M"))


_TAG_RE = re.compile(r"<[^>]*>")
_VVV_RE = re.compile(r"\bvvv\b")


def _cell_letters(span: str) -> str:
    """The letters a table cell contributes to the verse: markup (`<p class=|list2|>`) and the joint marker `vvv` inside a cell are
    not target words."""
    if span.strip() == "vvv":
        return ""
    return _letters(_VVV_RE.sub(" ", _TAG_RE.sub(" ", span)))


def map_target(rows: list[dict], toks: list[str], max_skip: int = 0, tolerant: bool = False
               ) -> dict[int, list[int]] | None:
    """{bsb_sort: our positions} for a verse's rows (any order), or None if the spans do not tile our tokens.

    `max_skip` (2026-10-07): the table puts a psalm's title into verse 1 ("A Psalm", "of David", ...) while our BSB text prints
    it as an unnumbered heading, so the verse text starts after those rows. With `max_skip` > 0, up to that many LEADING rows
    may be left out of the tiling when the rest tile the verse exactly; the skipped rows get no target positions.

    `tolerant` (2026-10-07): a last resort for verses whose table and English text genuinely differ by a few words: our text may
    hold a few words no row carries (speaker labels in Song of Songs, a sentence the table omits), and a row may hold words our
    text lacks (a bracketed insertion). Up to `_MAX_EXTRA_TOKENS` of our tokens may be left unclaimed and up to
    `_MAX_MISSING_ROWS` rows may find no tokens (they get no positions); every other row must still match exactly, in order."""
    srt = sorted(rows, key=lambda r: sort_key(r["bsb_sort"]))
    for strict in ((True, False) if tolerant else (True,)):
        for skip in range(0, max_skip + 1):
            out = _tile(srt[skip:], toks) if strict else _tile_tolerant(srt[skip:], toks)
            if out is not None:
                for r in srt[:skip]:
                    out[r["bsb_sort"]] = []
                return out
    return None


_MAX_EXTRA_TOKENS = 6
_MAX_MISSING_ROWS = 3
_LOOKAHEAD = 6


def _tile(srt: list[dict], toks: list[str]) -> dict[int, list[int]] | None:
    ours = [_letters(w) for w in toks]
    out: dict[int, list[int]] = {}
    j = 0
    for r in srt:
        letters = _cell_letters(r["text"])
        if not letters:
            out[r["bsb_sort"]] = []
            continue
        taken: list[int] = []
        acc = ""
        while j < len(ours) and len(acc) < len(letters):
            acc += ours[j]
            taken.append(j)
            j += 1
        if acc != letters:
            return None
        out[r["bsb_sort"]] = taken
    if j != len(ours):
        return None
    return out


def _match_at(ours: list[str], j: int, letters: str) -> list[int] | None:
    acc, taken = "", []
    while j < len(ours) and len(acc) < len(letters):
        acc += ours[j]
        taken.append(j)
        j += 1
    return taken if acc == letters else None


def _tile_tolerant(srt: list[dict], toks: list[str]) -> dict[int, list[int]] | None:
    ours = [_letters(w) for w in toks]
    out: dict[int, list[int]] = {}
    j = extra = missing = 0
    for r in srt:
        letters = _cell_letters(r["text"])
        if not letters:
            out[r["bsb_sort"]] = []
            continue
        for off in range(0, _LOOKAHEAD + 1):
            taken = _match_at(ours, j + off, letters)
            if taken is not None:
                extra += off
                out[r["bsb_sort"]] = taken
                j = taken[-1] + 1
                break
        else:
            missing += 1
            out[r["bsb_sort"]] = []
    extra += len(ours) - j
    if extra > _MAX_EXTRA_TOKENS or missing > _MAX_MISSING_ROWS:
        return None
    return out


_FINALS = str.maketrans("ךםןףץς", "כמנפצσ")


def _skel(s: str) -> str:
    """Letters only, marks dropped, lower case, final forms folded: the spelling key of the row-to-word alignment."""
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c)[0] == "L")
    return s.lower().translate(_FINALS)


def spine_words(spine: list[tuple]) -> list[list]:
    """[[word id, [token idx], surface, {strongs}]] in source order. `spine` = [(idx, strong, surface[, keys])] in spine order.
    A word is a MACULA word: key[:11] (Hebrew keys carry a 12th digit, the morpheme), so a Hebrew word's prefix, stem and suffix
    tokens are one word; a token holding two words (the spine fuses "Ἰσαάκ, Ἰσαὰκ", "בֵּית אֵל") belongs to both, each taking its
    own part of the surface. Without keys every token is one word (old callers, tests)."""
    out: list[list] = []
    at: dict[str, int] = {}
    for entry in spine:
        idx, strong = entry[0], entry[1]
        surface = entry[2] if len(entry) > 2 and entry[2] else ""
        keys = list(entry[3]) if len(entry) > 3 and entry[3] else [f"t{idx}"]
        parts = surface.split()
        for j, k in enumerate(keys):
            wid = k[:11]
            if wid not in at:
                at[wid] = len(out)
                out.append([wid, [], "", set()])
            w = out[at[wid]]
            if idx not in w[1]:
                w[1].append(idx)
            w[2] += parts[j] if len(parts) == len(keys) else (surface if j == 0 else "")
            if strong:
                w[3].add(strong)
    return out


_BAND = 12          # rows and words are aligned within this distance of the diagonal (verses differ by a few variant words)


def _row_word_score(r: dict, w: list) -> float | None:
    same = bool(r["strong"]) and r["strong"] in w[3]
    a, b = _skel(r["base"]), _skel(w[2])
    sim = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0
    if not same and sim < 0.6:
        return None
    return (2.0 if same else 0.0) + sim


def map_source(rows: list[dict], spine: list[tuple]) -> dict[str, dict]:
    """{bsb_sort: {h_idx_key, h_idx, match}} for a verse. `spine` = [(idx, strong, surface, keys)] in spine order.

    2026-10-26 (bsb-add-books B2/B4): a table row is ONE source word (Heb Sort / Greek Sort order), so rows are aligned to the
    spine's WORDS (`spine_words`) in order, by Strong's number and spelling together (an order-keeping alignment that maximises
    2 x [same Strong's] + spelling similarity; a pair with another Strong's needs spelling similarity >= 0.6). The row then takes
    every token of its word: a Hebrew prefix and a pronoun suffix go with their own word (the old adjacency rule gave a suffix
    to the NEXT row, 68% of OT suffixes), and a repeated Strong's number can no longer swap rows (the old (Strong's, k-th
    occurrence) key swapped εἶπεν/λέγω, εἶδον/ἰδοὺ and genealogy names). h_idx_key = the word's token with the row's Strong's
    number, else its content token, else its first.
    match: `strong` (the word carries the row's Strong's number), `spelling` (same place and spelling, another number:
    MACULA's G3004 for the table's G2036, ketiv/qere, a row with no Strong's), `fused` (a second row on a token the spine fused
    from two words, e.g. Ἰσαάκ Ἰσαὰκ), `none` (no word of the spine: a reading of another edition, e.g. ‹SBL› [NA])."""
    lang = rows[0]["language"] if rows else "Hebrew"
    skey = "grk_sort" if lang == "Greek" else "heb_sort"          # Hebrew AND Aramaic rows (DAN, EZR) carry Heb Sort
    src_rows = sorted((r for r in rows if r["kind"] != "empty" and r["base"].strip()), key=lambda r: sort_key(r[skey]))
    words = spine_words(spine)
    strong_of = {e[0]: e[1] for e in spine}
    content_of = {e[0]: e[4] for e in spine if len(e) > 4}
    n, m = len(src_rows), len(words)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    B = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = D[i - 1][j], "r"
            if D[i][j - 1] > best:
                best, how = D[i][j - 1], "w"
            if abs(i * m - j * n) <= _BAND * max(n, m):
                sc = _row_word_score(src_rows[i - 1], words[j - 1])
                if sc is not None and D[i - 1][j - 1] + sc > best:
                    best, how = D[i - 1][j - 1] + sc, "m"
            D[i][j], B[i][j] = best, how
    pair: dict[int, int] = {}
    i, j = n, m
    while i and j:
        if B[i][j] == "m":
            pair[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif B[i][j] == "r":
            i -= 1
        else:
            j -= 1
    result: dict[str, dict] = {}
    keyed: set[int] = set()
    for i, r in enumerate(src_rows):
        if i not in pair:
            result[r["bsb_sort"]] = {"h_idx_key": None, "h_idx": [], "match": "none"}
            continue
        w = words[pair[i]]
        toks = sorted(w[1])
        cand = ([x for x in toks if r["strong"] and strong_of.get(x) == r["strong"]]
                or [x for x in toks if content_of.get(x)] or toks)
        key = cand[0]
        match = "fused" if key in keyed else "strong" if r["strong"] in w[3] else "spelling"
        keyed.add(key)
        result[r["bsb_sort"]] = {"h_idx_key": key, "h_idx": toks, "match": match}
    return result


# --- build -------------------------------------------------------------------------------------------
def _spine_by_verse(books: list[str], usj_dir: Path):
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    recs = build_corpus(books, usj_dir, HebrewSource(), remap=remapper(EDITION, str(usj_dir)))
    out = {}
    for r in recs:
        out[encode(r.book, r.ch, r.v)] = r
    return out


# --- Parquet schema: the `bsb` extension as a real nested struct ---------------------------------------
def bsb_struct_type():
    """Explicit Arrow type for the `bsb` column. A parsed HTML column is a struct/list-of-structs; the rare
    row an HTML parser here could not account for keeps the raw cell in the `*_unparsed` string sibling
    (the structured field is null then) — one type per field, no JSON-in-a-string anywhere."""
    import pyarrow as pa
    tagcls = pa.struct([("tag", pa.string()), ("cls", pa.string())])
    return pa.struct([
        ("heb_sort", pa.string()), ("grk_sort", pa.string()), ("bsb_sort", pa.string()),   # strings: "8132.5" exists
        ("verse_num", pa.int32()), ("language", pa.string()),
        ("base", pa.string()), ("base_variants", pa.string()), ("translit", pa.string()),
        ("parsing", pa.string()), ("parsing_long", pa.string()),      # per-row override only, else null (table)
        ("str_heb", pa.string()), ("str_grk", pa.string()), ("verse_id", pa.string()),
        ("hdg", pa.struct([("tag", pa.string()), ("cls", pa.string()), ("text", pa.string())])),
        ("hdg_unparsed", pa.string()),
        ("crossref", pa.list_(pa.struct([("text", pa.string()), ("href", pa.string())]))),
        ("crossref_unparsed", pa.string()),
        ("par", pa.list_(tagcls)), ("par_unparsed", pa.string()),
        ("space", pa.string()), ("beg_q", pa.string()), ("text", pa.string()), ("pnc", pa.string()),
        ("end_q", pa.string()),
        ("footnotes", pa.string()), ("footnotes_unparsed", pa.string()),
        ("end_text", pa.list_(pa.struct([("text", pa.string()), ("close", pa.string())]))),
        ("end_text_unparsed", pa.string()),
        ("strong_suffix", pa.string()), ("kind", pa.string()),
    ])


def record_schema():
    import pyarrow as pa
    return pa.schema([
        ("ref", pa.int64()), ("book", pa.string()), ("chapter", pa.int32()), ("verse", pa.int32()),
        ("h_idx", pa.list_(pa.int32())), ("h_idx_key", pa.int32()), ("lexeme", pa.string()), ("strong", pa.string()),
        ("t_idx", pa.list_(pa.int32())), ("target", pa.string()), ("content", pa.bool_()),
        ("method", pa.string()), ("score", pa.float64()),
        ("attribution", pa.struct([("source", pa.string()), ("kind", pa.string()), ("base_text", pa.string()),
                                   ("license", pa.string()), ("pin_sha256", pa.string()), ("match", pa.string())])),
        ("bsb", bsb_struct_type()),
    ])


def sidecar_schema():
    import pyarrow as pa
    return pa.schema([
        ("ref", pa.int64()), ("h_idx", pa.int32()), ("keyed", pa.bool_()), ("bsb_sort", pa.string()),
        ("base", pa.string()), ("base_variants", pa.string()), ("translit", pa.string()), ("parsing", pa.string()),
        ("strong", pa.string()), ("strong_suffix", pa.string()),
    ])


def _split(v):
    """parsed value → (structured, unparsed_raw)."""
    if isinstance(v, dict) and "unparsed" in v:
        return None, v["unparsed"]
    return v, None


def to_struct(r: dict, parsing_table: dict[str, str]) -> dict:
    """A parsed row (parse_row/read_rows shape) → the `bsb` struct value."""
    hdg, hdg_u = _split(r["hdg"])
    cr, cr_u = _split(r["crossref"])
    par, par_u = _split(r["par"])
    et, et_u = _split(r["end_text"])
    fn, fn_u = _split(r["footnotes"])
    return {
        "heb_sort": r["heb_sort"], "grk_sort": r["grk_sort"], "bsb_sort": r["bsb_sort"],
        "verse_num": r["verse_num"], "language": r["language"],
        "base": r["base"], "base_variants": r["base_variants"], "translit": r["translit"],
        "parsing": r["parsing"], "parsing_long": parsing_long_override(r, parsing_table),
        "str_heb": r["str_heb"], "str_grk": r["str_grk"], "verse_id": r["verse_id"],
        "hdg": hdg, "hdg_unparsed": hdg_u,
        "crossref": cr, "crossref_unparsed": cr_u,
        "par": par, "par_unparsed": par_u,
        "space": r["space"], "beg_q": r["beg_q"], "text": r["text"], "pnc": r["pnc"], "end_q": r["end_q"],
        "footnotes": fn, "footnotes_unparsed": fn_u,
        "end_text": None if et is None else [{"text": g.get("text"), "close": g.get("close")} for g in et],
        "end_text_unparsed": et_u,
        "strong_suffix": r["strong_suffix"], "kind": r["kind"],
    }


def from_struct(b: dict) -> dict:
    """Inverse of to_struct: the struct value (as pyarrow's to_pylist returns it) → the row shape emit_row reads."""
    def join(v, u):
        return {"unparsed": u} if u is not None else v
    et = b["end_text"]
    end_text = None if et is None else [({"close": g["close"]} if g.get("close") is not None else {"text": g["text"]})
                                        for g in et]
    r = {k: b[k] for k in KEYS if k not in HTML_KEYS + ("footnotes",)}       # incl. parsing_long = override/None
    r["hdg"] = join(b["hdg"], b["hdg_unparsed"])
    r["crossref"] = join(b["crossref"], b["crossref_unparsed"])
    r["par"] = join(b["par"], b["par_unparsed"])
    r["end_text"] = join(end_text, b["end_text_unparsed"])
    r["footnotes"] = join(b["footnotes"], b["footnotes_unparsed"])
    r["strong_suffix"], r["kind"] = b["strong_suffix"], b["kind"]
    return r


def _none_breakdown(none_rows: list[dict]) -> dict:
    """The `source_none` gap, documented not gated: BSB source rows (per testament) that found no spine token,
    by parsing category, by witness-bracketing (a word present only in another Greek witness has no
    Nestle1904 spine token to map to), by Strong's presence, and by book."""
    out = {}
    for test in ("OT", "NT"):
        rows = [r for r in none_rows if r["_test"] == test]
        out[test] = {
            "rows": len(rows),
            "witness_bracketed": sum(r["base_variants"] != r["base"] for r in rows),
            "no_strong": sum(not (r["str_heb"] if test == "OT" else r["str_grk"]).strip() for r in rows),
            "by_parsing_top15": collections.Counter(r["parsing"] or "(blank)" for r in rows).most_common(15),
            "by_book_top10": collections.Counter(r["book"] for r in rows).most_common(10),
        }
    return out


def _sha256(fp: Path) -> str:
    return hashlib.sha256(fp.read_bytes()).hexdigest()


def build(books: list[str], out_dir: Path = OUT_DIR, tsv: Path = VENDOR_TSV, usj_dir: Path = USJ_DIR,
          sample_refs: tuple[tuple[str, int], ...] = (("GEN", 1), ("GEN", 2), ("MAT", 1), ("LUK", 18), ("PSA", 23))
          ) -> dict:
    """Parse the pinned TSV, map onto our coordinates, write the partition (rows + sidecar + parsing table +
    layer_manifest.json) under `layer_dir(out_dir)`, verify the round trip THROUGH the written Parquet on the
    sample chapters, then merge the partition entry into `<out_dir>/manifest.json` (if that is a full-align
    manifest) and regenerate the card. Returns the layer manifest (the merged entry lives in its `entry`)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    pin = load_pin()
    rows_all = list(read_rows(tsv))
    parsing_table, exceptions = build_parsing_table(rows_all)
    ldir = layer_dir(out_dir)
    sdir = ldir / "sidecar"
    ldir.mkdir(parents=True, exist_ok=True)
    sdir.mkdir(parents=True, exist_ok=True)
    (ldir / "parsing_table.json").write_text(json.dumps(parsing_table, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
    stats: collections.Counter = collections.Counter()
    stats["rows_total"] = len(rows_all)
    stats["rows_empty"] = sum(r["kind"] == "empty" for r in rows_all)
    stats["rows_hebrew"] = sum(r["language"] == "Hebrew" for r in rows_all)
    stats["rows_greek"] = sum(r["language"] == "Greek" for r in rows_all)
    for r in rows_all:
        for k in HTML_KEYS + ("footnotes",):
            if isinstance(r[k], dict) and "unparsed" in r[k]:
                stats[f"unparsed_{k}"] += 1
    by_book: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows_all:
        if r["book"]:
            by_book[r["book"]].append(r)
        else:
            stats["rows_no_verse_id"] += 1
    files, sidecar_files = {}, {}
    per_testament: dict[str, collections.Counter] = {"OT": collections.Counter(), "NT": collections.Counter()}
    round_trip = {"rows": 0, "exact_non_html": 0, "exact_html": 0, "normalized_html": 0, "diff_examples": [],
                  "through_parquet": True}
    none_rows: list[dict] = []
    nh = [i for i, k in enumerate(KEYS) if k not in HTML_KEYS]
    hi = [i for i, k in enumerate(KEYS) if k in HTML_KEYS]
    # 2026-10-06: the table's verse numbers are ENGLISH; our spine verses are Hebrew-numbered in the OT. Rows are looked up
    # through the inverse of the edition's verse remap (English verse -> the spine verse whose pooled group holds its
    # text) and written under the SPINE ref, like every other full-align layer. An English verse with no spine verse
    # keeps its English ref (the codec files those rows under `offindex`).
    from lexeme_aligner.eval.pos_score import spine_ref_of_target
    from lexeme_aligner.versification import remapper
    remap = remapper(EDITION, str(usj_dir))
    for book in books:
        brows = by_book.get(book)
        if not brows:
            stats["books_absent"] += 1
            continue
        test = "OT" if BOOK_NUMBERS[book] <= 39 else "NT"
        bt = per_testament[test]
        spine = _spine_by_verse([book], usj_dir)
        by_verse: dict[int, list[dict]] = collections.defaultdict(list)
        for r in brows:
            by_verse[encode(book, r["chapter"], r["verse"])].append(r)
        records, sidecar = [], []
        inv = spine_ref_of_target([book], remap)
        for ref, vrows in sorted(by_verse.items()):
            sref = inv.get(ref)
            rec = spine.get(sref) if sref is not None else None
            out_ref = sref if rec is not None else ref
            bt["verses"] += 1
            if rec is None:
                bt["verses_no_spine"] += 1
                tmap, smap, sp_tokens = None, {}, {}
            else:
                sp = sorted(rec.heb, key=lambda t: t.idx)
                sp_tokens = {t.idx: t for t in sp}
                smap = map_source(vrows, [(t.idx, t.strong, t.surface, t.keys, bool(t.is_content)) for t in sp])
                tmap = map_target(vrows, list(rec.toks), max_skip=_TITLE_SKIP, tolerant=True)
                if tmap is None:
                    bt["verses_target_refused"] += 1
                else:
                    bt["verses_target_mapped"] += 1
            for r in sorted(vrows, key=lambda r: sort_key(r["bsb_sort"])):
                s = smap.get(r["bsb_sort"], {"h_idx_key": None, "h_idx": [], "match": "empty" if r["kind"] == "empty" else "none"})
                if r["kind"] != "empty" and r["base"].strip():
                    bt["source_rows"] += 1
                    bt[f"source_{s['match']}"] += 1
                    if s["match"] == "none":
                        none_rows.append(dict(r, _test=test))
                key_tok = sp_tokens.get(s["h_idx_key"]) if s["h_idx_key"] is not None else None
                t_idx = None if tmap is None else tmap.get(r["bsb_sort"], [])
                if r["kind"] != "empty" and r["text"].strip():
                    bt["target_rows"] += 1
                    if t_idx:
                        bt["target_rows_positioned"] += 1
                records.append({
                    "ref": out_ref, "book": book, "chapter": out_ref // 1000 % 1000, "verse": out_ref % 1000,
                    "h_idx": s["h_idx"] or None, "h_idx_key": s["h_idx_key"],
                    "lexeme": key_tok.lexeme if key_tok else None,
                    "strong": r["strong"], "t_idx": t_idx, "target": r["text"],
                    "content": bool(key_tok.is_content) if key_tok else None,
                    "method": "manual", "score": None,
                    "attribution": {"source": "bsb-tables", "kind": "manual", "base_text": "BSB",
                                    "license": "CC0-1.0", "pin_sha256": pin.get("sha256"),
                                    "match": s["match"]},
                    "bsb": to_struct(r, parsing_table),
                })
                if key_tok is not None:
                    for idx in s["h_idx"]:
                        sidecar.append({"ref": out_ref, "h_idx": idx, "keyed": idx == s["h_idx_key"],
                                        "bsb_sort": r["bsb_sort"], "base": r["base"],
                                        "base_variants": r["base_variants"], "translit": r["translit"],
                                        "parsing": r["parsing"], "strong": r["strong"],
                                        "strong_suffix": r["strong_suffix"]})
        fp = ldir / f"{book}.parquet"
        pq.write_table(pa.Table.from_pylist(records, schema=record_schema()), fp, compression="zstd")
        sfp = sdir / f"{book}.parquet"
        pq.write_table(pa.Table.from_pylist(sidecar, schema=sidecar_schema()), sfp, compression="zstd")
        files[book] = {"rows": len(records), "content_sha256": _sha256(fp)}
        sidecar_files[book] = {"rows": len(sidecar), "content_sha256": _sha256(sfp)}
        # round trip on the sample chapters — THROUGH the written parquet, not the in-memory rows
        chapters = {sch for sb, sch in sample_refs if sb == book}
        if chapters:
            raw_by_sort = {r["bsb_sort"]: r["_raw"] for r in brows if r["chapter"] in chapters}
            back = pq.read_table(fp).to_pylist()
            for rec in back:
                orig = raw_by_sort.get(rec["bsb"]["bsb_sort"])
                if orig is None or rec["chapter"] not in chapters:
                    continue
                regen = emit_row(from_struct(rec["bsb"]), parsing_table)
                round_trip["rows"] += 1
                if all(regen[i] == orig[i] for i in nh):
                    round_trip["exact_non_html"] += 1
                elif len(round_trip["diff_examples"]) < 5:
                    round_trip["diff_examples"].append({"bsb_sort": rec["bsb"]["bsb_sort"], "diff": [
                        (KEYS[i], orig[i], regen[i]) for i in nh if regen[i] != orig[i]]})
                if all(regen[i] == orig[i] for i in hi):
                    round_trip["exact_html"] += 1
                if all(_norm_html(regen[i]) == _norm_html(orig[i]) for i in hi):
                    round_trip["normalized_html"] += 1
    pt = {k: dict(v) for k, v in per_testament.items()}
    entry = {
        "source": "bsb-tables", "owner": OWNER, "kind": "manual", "gold_method": "manual",
        "license": "CC0-1.0", "license_statement": LICENSE_STATEMENT, "url": URL,
        "pin": {k: pin.get(k) for k in ("sha256", "bytes", "fetched", "license_source")},
        "publishable": True, "quarantined": False, "quarantine_reason": None,
        "health": None,
        "health_note": "the publisher's own interlinear tagging; no positional-vs-lexical health is computed for "
                       "it (contest_rule's gold_health is Clear-shaped) — coverage below is the honesty record",
        "rows": sum(f["rows"] for f in files.values()), "files": files,
        "sidecar": {"rows": sum(f["rows"] for f in sidecar_files.values()), "files": sidecar_files,
                    "path": f"{PARTITION}/sidecar/<BOOK>.parquet",
                    "note": "one row per spine token that a BSB row was attached to (keyed or by adjacency): "
                            "translit / parsing / base / witness brackets — reusable without the alignment"},
        "coverage": {
            "rows": sum(f["rows"] for f in files.values()), "rows_empty": stats["rows_empty"],
            "verses": sum(v.get("verses", 0) for v in pt.values()),
            "verses_mapped": sum(v.get("verses_target_mapped", 0) for v in pt.values()),
            "verses_refused": sum(v.get("verses_target_refused", 0) for v in pt.values()),
            "verses_no_spine": sum(v.get("verses_no_spine", 0) for v in pt.values()),
            "source_rows": sum(v.get("source_rows", 0) for v in pt.values()),
            "source_strong": sum(v.get("source_strong", 0) for v in pt.values()),
            "source_fused": sum(v.get("source_fused", 0) for v in pt.values()),
            "source_spelling": sum(v.get("source_spelling", 0) for v in pt.values()),
            "source_none": sum(v.get("source_none", 0) for v in pt.values()),
            "target_rows": sum(v.get("target_rows", 0) for v in pt.values()),
            "target_rows_positioned": sum(v.get("target_rows_positioned", 0) for v in pt.values()),
            "per_testament": pt,
        },
        "source_none_breakdown": _none_breakdown(none_rows),
        "stats": dict(stats),
        "parsing_table": {"file": f"{PARTITION}/parsing_table.json", "entries": len(parsing_table),
                          "non_function": [(k, sorted(v)) for k, v in exceptions]},
        "round_trip_sample": round_trip,
        "reverse_direction": "experimental — `python -m lexeme_aligner.eval.bsb_tables --emit-tsv <BOOK>` regenerates "
                             "all 23 columns from the records + parsing_table.json",
        "contract": {
            "padding_rows": "rows with bsb.kind == 'empty' are the file's sort-key-only padding (kept for "
                            "reversibility) — filter them for alignment use",
            "spans_keep_spaces": "`target` (= BSB version cell) keeps its leading/trailing spaces verbatim — "
                                 "strip() for clean text",
            "h_idx_is_a_list": "BSB tags whole words, MACULA splits prefixes/suffixes — h_idx_key is the keyed "
                               "token, h_idx every token attached to the row",
            "witness_brackets": "bsb.base_variants carries {TR} ⧼RP⧽ (WH) 〈NE〉 [NA] ‹SBL› [[ECM]]; a word present "
                                "only in another witness has no Nestle1904 spine token (attribution.match = none)",
        },
    }
    layer_manifest = {"edition": EDITION, "language": LANG, "partition": PARTITION, "entry": entry}
    (ldir / "layer_manifest.json").write_text(json.dumps(layer_manifest, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
    merge_into_manifest(out_dir, entry)
    return layer_manifest


def merge_into_manifest(out_dir: Path, entry: dict) -> bool:
    """Update `<out_dir>/manifest.json` (a full-align manifest, owned by gold_to_fullalign.py) with this
    partition under languages.eng.editions.engbsb.layers.manual[PARTITION], atomically (tmp + os.replace),
    then regenerate the card through the ONE README writer. Returns False (no-op) when there is no
    full-align manifest at `out_dir` — e.g. the old staging tree — in which case a minimal one is written
    so the tree is still self-describing."""
    import os
    mf = out_dir / "manifest.json"
    if mf.exists():
        m = json.loads(mf.read_text(encoding="utf-8"))
        if "languages" not in m:
            m = {"languages": {}}
    else:
        m = {"languages": {}}
    lang = m["languages"].setdefault(LANG, {"editions": {}})
    ed = lang.setdefault("editions", {}).setdefault(EDITION, {"layers": {}})
    ed.setdefault("layers", {}).setdefault("manual", {})[PARTITION] = entry
    m.setdefault("layout", "<iso>/<edition>/{statistical,manual/<base_text>,llm/<cell>}/<BOOK>.parquet")
    tmp = mf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, mf)
    from lexeme_aligner.eval.gold_to_fullalign import write_card
    write_card(out_dir, m, internal=False)
    return True


def _norm_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


# --- reverse -----------------------------------------------------------------------------------------
def emit_tsv_for_book(book: str, out_dir: Path = OUT_DIR, dest: Path | None = None) -> list[list[str]]:
    """Regenerate the 23-column rows for `book` from the parquet records + parsing table (experimental)."""
    import pyarrow.parquet as pq
    ldir = layer_dir(out_dir)
    table = json.loads((ldir / "parsing_table.json").read_text(encoding="utf-8"))
    recs = pq.read_table(ldir / f"{book}.parquet").to_pylist()
    rows = [emit_row(from_struct(rec["bsb"]), table)
            for rec in sorted(recs, key=lambda x: sort_key(x["bsb"]["bsb_sort"]))]
    if dest:
        with dest.open("w", encoding="utf-8", newline="") as fh:
            fh.write("\t".join(COLUMNS) + "\n")
            for r in rows:
                fh.write("\t".join(r) + "\n")
    return rows


# --- CLI ----------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--book", action="append")
    ap.add_argument("--emit-tsv", metavar="BOOK")
    ap.add_argument("--out", type=Path, help="--emit-tsv: where to write the regenerated tsv")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR,
                    help=f"full-align root to write the partition under (default {OUT_DIR}; the first build's "
                         f"staging tree {STAGING_DIR} is still accepted)")
    a = ap.parse_args(argv)
    if a.fetch:
        pin = fetch()
        print(f"[bsb_tables] fetched {pin['bytes']:,} bytes, sha256 {pin['sha256'][:12]}… → {VENDOR_TSV}",
              file=sys.stderr)
    if a.build:
        books = [b.upper() for b in a.book] if a.book else OT_BOOKS + NT_BOOKS
        m = build(books, out_dir=a.out_dir)
        e = m["entry"]
        print(json.dumps({k: e[k] for k in ("rows", "coverage", "source_none_breakdown", "parsing_table",
                                            "round_trip_sample")}, ensure_ascii=False, indent=1))
        print(f"[bsb_tables] → {layer_dir(a.out_dir)} ({len(e['files'])} book file(s)); manifest merged into "
              f"{a.out_dir / 'manifest.json'}", file=sys.stderr)
    if a.emit_tsv:
        rows = emit_tsv_for_book(a.emit_tsv.upper(), out_dir=a.out_dir, dest=a.out)
        print(f"[bsb_tables] {len(rows)} rows regenerated for {a.emit_tsv.upper()}"
              + (f" → {a.out}" if a.out else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
