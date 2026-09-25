"""BSB Translation Tables (bereanbible.com/bsb_tables.tsv) → full-align rows, losslessly, with the reverse
direction planned in (docs/architecture.md §4, the BSB round-trip source).

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
  source: BSB row → spine token(s), 1:n. A BSB row is a whole word ("Prep-b | N-fs"); MACULA splits the
          prefix into its own token with its own Strong's (בְּ = H0871). Keying is (strong, k-th occurrence
          in the verse) exactly as pos_score does; the spine's unassigned tokens immediately preceding a
          keyed token are attached to that row (prefix convention), trailing ones to the previous row
          (suffix convention), each recorded separately from the keyed token (`h_idx_key` vs `h_idx`).
          A strongless row (e.g. לָהֶם, "Prep | 3mp", no Str Heb) gets the unassigned tokens lying strictly
          between its neighbours' tokens (`match: positional`) or nothing (`match: none`).
  target: ` BSB version ` spans in BSB Sort order → our `engbsb` token positions, by the same letters-run
          walk `pos_score.map_positions` uses (our text IS the BSB text); "vvv" (BSB's own folded-content
          placeholder) and letterless spans ("-") map to no position; a verse whose spans do not tile our
          tokens is REFUSED (all its rows get `t_idx = None`) and counted.

Reverse direction (`--emit-tsv`, experimental): regenerates all 23 columns from the records + the parsing
table. The build reports a round-trip on a fixed sample against the raw rows, going THROUGH the written
Parquet (read back, not in-memory): exact on the 19 non-HTML columns, exact and tag-stripped/whitespace-
collapsed rates on Hdg/Crossref/Par/End text.

Where it lands (docs/architecture.md §4, the published full-align layout): `--build` writes DIRECTLY into
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

    python -m lexeme_aligner.bsb_tables --fetch
    python -m lexeme_aligner.bsb_tables --build            # all 66 books → publish/full-align/eng/engbsb/manual/BSB-tables/
    python -m lexeme_aligner.bsb_tables --emit-tsv GEN --out /tmp/GEN.tsv
"""
from __future__ import annotations

import argparse
import collections
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
OUT_DIR = Path("publish/full-align")                # the published full-align root (docs/architecture.md §4)
STAGING_DIR = Path("pipeline/work/full-align-bsb")   # the first build's staging tree — still accepted via --out-dir
USJ_DIR = Path("pipeline/work/ingest-cache/usj-engbsb")
EDITION = "engbsb"
LANG = "eng"
PARTITION = "BSB-tables"                             # manual/<partition>/ — beside Clear's manual/BSB/
OWNER = "bsb_tables"                                 # manifest marker gold_to_fullalign.py carries over on rerun


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
    return f"{'H' if lang == 'Hebrew' else 'G'}{int(m.group(1)):04d}", (m.group(2) or None)


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
    r["strong"], r["strong_suffix"] = norm_strong(r["str_heb"] if r["language"] == "Hebrew" else r["str_grk"],
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
    from lexeme_aligner.benchmark import norm_surface
    return "".join(c for c in norm_surface(s) if unicodedata.category(c)[0] in ("L", "M"))


def map_target(rows: list[dict], toks: list[str]) -> dict[int, list[int]] | None:
    """{bsb_sort: our positions} for a verse's rows (any order), or None if the spans do not tile our tokens."""
    ours = [_letters(w) for w in toks]
    out: dict[int, list[int]] = {}
    j = 0
    for r in sorted(rows, key=lambda r: sort_key(r["bsb_sort"])):
        span = r["text"]
        letters = "" if span.strip() == "vvv" else _letters(span)
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


def _word_forms(surface: str) -> list[str]:
    """Mark-stripped words of a source surface, maqaf/sof-pasuq/paseq removed — the exact-equality key
    for the surface-containment check below (never fuzzy)."""
    out = []
    for w in surface.replace("\u05be", " ").split():
        # NFD then drop every combining mark: Hebrew points/cantillation AND Greek accents/breathings
        # (precomposed ά vs ὰ must compare equal — BSB's "Ἰσαὰκ" vs the spine's "Ἰσαάκ")
        w = "".join(c for c in unicodedata.normalize("NFD", w) if unicodedata.category(c) != "Mn")
        w = re.sub(r"[\u05be\u05c0\u05c3\u05c6\u05f3\u05f4\u05e4\u05e1]+$", "", w)   # ־ ׀ ׃ ׆ ׳ ״ and פ/ס section marks
        w = re.sub(r"^[\u05be\u05c0\u05c3]+", "", w)
        if w:
            out.append(w)
    return out


def map_source(rows: list[dict], spine: list[tuple]) -> dict[str, dict]:
    """{bsb_sort: {h_idx_key, h_idx, match}} for a verse. `spine` = [(idx, strong[, surface])] in spine
    order. Keyed on (strong, k-th occurrence) in each side's own order. Our spine FUSES adjacent words
    into ONE token in two cases BSB keeps as separate rows — consecutive same-Strong's words ("נֹחַ נֹחַ",
    "τὸν Ἰσαάκ, Ἰσαὰκ δὲ") and compound names carrying one Strong's ("תּוּבַל קַיִן" H8423, "בֵּית אֵל"
    H1008, "פַּדַּן אֲרָם" H6307) — the fused surface has a space per absorbed word. A BSB row is
    attached to such a token as `fused` only when its own base is EXACTLY one of that surface's words
    (mark-stripped) and it is adjacent in source order to a row already on that token — equality, never
    a guess; the fused rows do not consume a k-th occurrence. Unassigned spine tokens are attached by
    adjacency (prefix to the next keyed row, suffix to the previous one) and to strongless rows lying
    between neighbours — each attachment recorded as such, never presented as a keyed match."""
    lang = rows[0]["language"] if rows else "Hebrew"
    skey = "heb_sort" if lang == "Hebrew" else "grk_sort"
    src_rows = sorted((r for r in rows if r["kind"] != "empty" and r["base"].strip()), key=lambda r: sort_key(r[skey]))
    spine_k: dict[tuple[str, int], int] = {}
    words_of: dict[int, list[str]] = {}
    seen: collections.Counter = collections.Counter()
    for entry in spine:
        idx, strong = entry[0], entry[1]
        words_of[idx] = _word_forms(entry[2]) if len(entry) > 2 and entry[2] else []
        if strong:
            spine_k[(strong, seen[strong])] = idx
            seen[strong] += 1
    owner: dict[int, str] = {}                          # spine idx -> bsb_sort (the keyed row)
    result: dict[str, dict] = {}
    seen_b: collections.Counter = collections.Counter()   # spine occurrences CONSUMED per strong
    fused_left: dict[int, int] = {}                     # fused spine idx -> words still unclaimed
    prev_row = None
    for r in src_rows:
        res = {"h_idx_key": None, "h_idx": [], "match": "none"}
        pidx = result[prev_row["bsb_sort"]]["h_idx_key"] if prev_row is not None else None
        base_w = _word_forms(r["base"])
        # (1) the continuation of a fused token the previous row sits on: exact word containment
        if (pidx is not None and fused_left.get(pidx, 0) > 0 and len(base_w) == 1
                and base_w[0] in words_of.get(pidx, [])):
            fused_left[pidx] -= 1
            res.update(h_idx_key=pidx, h_idx=[pidx], match="fused")
        elif r["strong"]:
            k = seen_b[r["strong"]]
            idx = spine_k.get((r["strong"], k))
            if idx is not None and idx not in owner:
                owner[idx] = r["bsb_sort"]
                seen_b[r["strong"]] += 1
                res.update(h_idx_key=idx, h_idx=[idx], match="strong")
                n_words = len(words_of.get(idx, []))
                if n_words > 1:
                    fused_left[idx] = n_words - 1
        # (2) a row whose Strong's the spine does not have here, but whose base is the FIRST word of the
        #     fused token the NEXT keyed row will take ("בֵּית" before "אֵל"/H1008): resolved after the pass
        result[r["bsb_sort"]] = res
        prev_row = r
    # second pass for (2): an unmatched single-word row immediately before a keyed row on a fused token
    for i, r in enumerate(src_rows):
        res = result[r["bsb_sort"]]
        if res["match"] != "none" or i + 1 >= len(src_rows):
            continue
        nres = result[src_rows[i + 1]["bsb_sort"]]
        nidx = nres["h_idx_key"]
        if nidx is None or nres["match"] != "strong" or fused_left.get(nidx, 0) <= 0:
            continue
        base_w = _word_forms(r["base"])
        if len(base_w) == 1 and base_w[0] in words_of.get(nidx, []):
            fused_left[nidx] -= 1
            res.update(h_idx_key=nidx, h_idx=[nidx], match="fused")
    # adjacency attachment of unassigned spine tokens
    order = [entry[0] for entry in spine]
    keyed_rows = [r for r in src_rows if result[r["bsb_sort"]]["match"] == "strong"]
    keyed_rows.sort(key=lambda r: result[r["bsb_sort"]]["h_idx_key"])
    pending: list[int] = []
    for idx in order:
        if idx in owner:
            b = owner[idx]
            for p in pending:
                result[b]["h_idx"].append(p)
                owner[p] = b
            pending = []
        else:
            pending.append(idx)
    if pending and keyed_rows:                          # trailing tokens: suffix convention → last keyed row
        b = keyed_rows[-1]["bsb_sort"]
        for p in pending:
            result[b]["h_idx"].append(p)
            owner[p] = b
        pending = []
    # strongless rows: take the tokens strictly between their neighbours' tokens, if any were attached to
    # the wrong side above — done by re-deriving: a strongless row between keyed rows A and B claims the
    # unkeyed tokens that sit between A's key and B's key and were attached as B's prefix.
    for i, r in enumerate(src_rows):
        res = result[r["bsb_sort"]]
        if res["match"] != "none" or not r["base"].strip():
            continue
        prev_row = next((x for x in reversed(src_rows[:i]) if result[x["bsb_sort"]]["match"] == "strong"), None)
        next_row = next((x for x in src_rows[i + 1:] if result[x["bsb_sort"]]["match"] == "strong"), None)
        prev_key = result[prev_row["bsb_sort"]]["h_idx_key"] if prev_row else None
        if next_row is not None:                        # tokens attached as the NEXT keyed row's prefix
            donor = result[next_row["bsb_sort"]]
            between = sorted(p for p in donor["h_idx"] if p != donor["h_idx_key"]
                             and (prev_key is None or p > prev_key) and p < donor["h_idx_key"])
        elif prev_row is not None:                      # trailing: tokens attached as the PREVIOUS row's suffix
            donor = result[prev_row["bsb_sort"]]
            between = sorted(p for p in donor["h_idx"] if p > prev_key)
        else:
            continue
        if between:
            for p in between:
                donor["h_idx"].remove(p)
                owner[p] = r["bsb_sort"]
            res.update(h_idx=between, match="positional")
    for res in result.values():
        res["h_idx"].sort()
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
        for ref, vrows in sorted(by_verse.items()):
            rec = spine.get(ref)
            bt["verses"] += 1
            if rec is None:
                bt["verses_no_spine"] += 1
                tmap, smap, sp_tokens = None, {}, {}
            else:
                sp = sorted(rec.heb, key=lambda t: t.idx)
                sp_tokens = {t.idx: t for t in sp}
                smap = map_source(vrows, [(t.idx, t.strong, t.surface) for t in sp])
                tmap = map_target(vrows, list(rec.toks))
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
                    "ref": ref, "book": book, "chapter": r["chapter"], "verse": r["verse"],
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
                        sidecar.append({"ref": ref, "h_idx": idx, "keyed": idx == s["h_idx_key"],
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
            "source_positional": sum(v.get("source_positional", 0) for v in pt.values()),
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
        "reverse_direction": "experimental — `python -m lexeme_aligner.bsb_tables --emit-tsv <BOOK>` regenerates "
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
    from lexeme_aligner.gold_to_fullalign import write_card
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
