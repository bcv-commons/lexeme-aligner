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
table. The build reports a round-trip on a fixed sample against the raw rows: exact on the 19 non-HTML
columns, tag-stripped/whitespace-collapsed on Hdg/Crossref/Par/End text (and the exact rate on those too).

License: berean.bible/licensing.htm — "The Berean Bible and Majority Bible texts are officially placed
into the public domain as of April 30, 2023." (read at fetch time, recorded in the pin sidecar); the same
text is distributed by BSB-publishing as CC0-1.0, which is what attribution carries.

    python -m lexeme_aligner.bsb_tables --fetch
    python -m lexeme_aligner.bsb_tables --build            # all 66 books → pipeline/work/full-align-bsb/
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
OUT_DIR = Path("pipeline/work/full-align-bsb")
USJ_DIR = Path("pipeline/work/ingest-cache/usj-engbsb")
EDITION = "engbsb"
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


def build(books: list[str], out_dir: Path = OUT_DIR, tsv: Path = VENDOR_TSV, usj_dir: Path = USJ_DIR,
          sample_refs: tuple[tuple[str, int], ...] = (("GEN", 1), ("GEN", 2), ("MAT", 1), ("LUK", 18), ("PSA", 23))
          ) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq
    pin = load_pin()
    rows_all = list(read_rows(tsv))
    parsing_table, exceptions = build_parsing_table(rows_all)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "parsing_table.json").write_text(json.dumps(parsing_table, ensure_ascii=False, indent=1) + "\n",
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
    manifest_books = {}
    per_testament: dict[str, collections.Counter] = {"OT": collections.Counter(), "NT": collections.Counter()}
    round_trip = {"rows": 0, "exact_non_html": 0, "exact_html": 0, "normalized_html": 0, "diff_examples": []}
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
                    bt[f"source_{s['match']}"] += 1
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
                    "bsb": {k: r[k] for k in KEYS if k != "parsing_long"}
                           | {"parsing_long": parsing_long_override(r, parsing_table),
                              "strong_suffix": r["strong_suffix"], "kind": r["kind"]},
                })
                if key_tok is not None:
                    for idx in s["h_idx"]:
                        sidecar.append({"ref": ref, "h_idx": idx, "keyed": idx == s["h_idx_key"],
                                        "bsb_sort": r["bsb_sort"], "base": r["base"],
                                        "base_variants": r["base_variants"], "translit": r["translit"],
                                        "parsing": r["parsing"], "strong": r["strong"],
                                        "strong_suffix": r["strong_suffix"]})
        for sub, data in (("manual-bsb-tables", records), ("source-sidecar", sidecar)):   # noqa
            d = out_dir / "eng" / EDITION / sub
            d.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(_json_safe(data)), d / f"{book}.parquet", compression="zstd")
        manifest_books[book] = {"rows": len(records), "sidecar_rows": len(sidecar)}
        # round trip on the sample chapters
        for sb, sch in sample_refs:
            if sb != book:
                continue
            for r in brows:
                if r["chapter"] != sch:
                    continue
                regen = emit_row(dict(r, parsing_long=parsing_long_override(r, parsing_table)), parsing_table)
                orig = r["_raw"]
                round_trip["rows"] += 1
                nh = [i for i, k in enumerate(KEYS) if k not in HTML_KEYS]
                if all(regen[i] == orig[i] for i in nh):
                    round_trip["exact_non_html"] += 1
                elif len(round_trip["diff_examples"]) < 5:
                    round_trip["diff_examples"].append({"bsb_sort": r["bsb_sort"], "diff": [
                        (KEYS[i], orig[i], regen[i]) for i in nh if regen[i] != orig[i]]})
                hi = [i for i, k in enumerate(KEYS) if k in HTML_KEYS]
                if all(regen[i] == orig[i] for i in hi):
                    round_trip["exact_html"] += 1
                if all(_norm_html(regen[i]) == _norm_html(orig[i]) for i in hi):
                    round_trip["normalized_html"] += 1
    manifest = {
        "edition": EDITION, "language": "eng", "source": "bsb-tables", "url": URL, "pin": pin,
        "license": "CC0-1.0", "license_statement": LICENSE_STATEMENT,
        "layers": {"manual-bsb-tables": "full-align rows (docs/architecture.md §4) + `bsb` extension",
                   "source-sidecar": "per spine token: translit / parsing / base / witness brackets"},
        "stats": dict(stats), "per_testament": {k: dict(v) for k, v in per_testament.items()},
        "parsing_table": {"entries": len(parsing_table), "non_function": [(k, sorted(v)) for k, v in exceptions]},
        "round_trip_sample": round_trip, "books": manifest_books,
        "reverse_direction": "experimental — `--emit-tsv <BOOK>` regenerates all 23 columns from the records",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    return manifest


def _json_safe(rows: list[dict]) -> list[dict]:
    """pyarrow infers struct types from values; a column that is a dict in some rows and a list in others
    (crossref/par: list normally, {'unparsed': …} on failure) must be one type — serialize those as JSON strings."""
    out = []
    for r in rows:
        r = dict(r)
        if "bsb" in r:
            b = dict(r["bsb"])
            for k in HTML_KEYS + ("footnotes",):
                b[k] = json.dumps(b[k], ensure_ascii=False) if b[k] is not None else None
            r["bsb"] = b
        out.append(r)
    return out


def _norm_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


# --- reverse -----------------------------------------------------------------------------------------
def emit_tsv_for_book(book: str, out_dir: Path = OUT_DIR, dest: Path | None = None) -> list[list[str]]:
    """Regenerate the 23-column rows for `book` from the parquet records + parsing table (experimental)."""
    import pyarrow.parquet as pq
    table = json.loads((out_dir / "parsing_table.json").read_text(encoding="utf-8"))
    recs = pq.read_table(out_dir / "eng" / EDITION / "manual-bsb-tables" / f"{book}.parquet").to_pylist()
    rows = []
    for rec in sorted(recs, key=lambda x: sort_key(x["bsb"]["bsb_sort"])):
        b = dict(rec["bsb"])
        for k in HTML_KEYS + ("footnotes",):
            b[k] = json.loads(b[k]) if b[k] is not None else None
        rows.append(emit_row(b, table))          # b["parsing_long"] is the per-row override or None
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
    ap.add_argument("--out", type=Path)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    if a.fetch:
        pin = fetch()
        print(f"[bsb_tables] fetched {pin['bytes']:,} bytes, sha256 {pin['sha256'][:12]}… → {VENDOR_TSV}",
              file=sys.stderr)
    if a.build:
        books = [b.upper() for b in a.book] if a.book else OT_BOOKS + NT_BOOKS
        m = build(books, out_dir=a.out_dir)
        print(json.dumps({k: m[k] for k in ("stats", "per_testament", "parsing_table", "round_trip_sample")},
                         ensure_ascii=False, indent=1))
    if a.emit_tsv:
        rows = emit_tsv_for_book(a.emit_tsv.upper(), out_dir=a.out_dir, dest=a.out)
        print(f"[bsb_tables] {len(rows)} rows regenerated for {a.emit_tsv.upper()}"
              + (f" → {a.out}" if a.out else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
