"""Roadmap F4 (internal-docs/aim1-typology-source-structure-plan.md, "### full-align"): a third Arabic
manual layer from Door43's real USFM3 `\\zaln` word-alignment markup.

CORRECTION TO THE PLAN'S OWN FRAMING, found while investigating (verified against real fetched files,
not assumed): the plan names TWO editions, `ar_avd`/`ar_arst`, as "the only aligned Bibles the catalog
now returns" — but only ONE of them actually carries alignment data. `ar_avd` (Door43-Catalog/ar_avd,
Arabic Van Dyck) is plain USFM2 text with ZERO `\\zaln` markers anywhere (checked TIT in full: 0
matches) and is also missing 19-PSA.usfm/23-ISA.usfm from its own file list — a real, separate gap.
`ar_arst` (BSOJ/ar_arst, git.door43.org, updated 2026-09-24) genuinely has real `\\zaln-s`/`\\zaln-e`
markup, keyed to Greek Strong's/lemma/morph/occurrence, full 66-book coverage. This module targets
`ar_arst` only — `ar_avd` cannot serve as an aligned manual layer at all, alignment data or not.

USJ CONVERSION (2026-09-26 rewrite, replacing a from-scratch regex/text-offset parser): this module
now parses real USFM3 via `usfmtc` (already a dependency of this repo — used by `cdn_source.py`/
`dbt_source.py`/`helloao_source.py` for ordinary ingestion; `ar_arst` is simply the first zaln-bearing
text this pipeline has touched, not a reason to hand-roll a second, narrower parser). Confirmed directly
against real `ar_arst` data (TIT, PHM) that `usfmtc.readFile(path).outUsj()` represents `\\zaln-s`/
`\\zaln-e` as proper `{"type": "ms", "marker": "zaln-s", "x-strong": ..., "x-lemma": ..., ...}` /
`{"type": "ms", "marker": "zaln-e"}` nodes, and `\\w` words as `{"type": "char", "marker": "w",
"content": [...]}`, sitting in the SAME flat content arrays this project's own `versification.py`
`_usj_structure()` already knows how to walk — chapters are `{"type": "chapter", "marker": "c", ...}`
top-level siblings, verses are `{"type": "verse", "marker": "v", ...}` nodes nested inside each `para`
block's own `content` list.

A real parsing wrinkle, confirmed to survive usfmtc's own conversion unchanged (real TIT 1:5 sequence):
`('ms','zaln-s'), ('ms','zaln-s'), ('w','ورسولٌ'), ('ms','zaln-e'), ('ms','zaln-e')` — a `\\zaln-s` for a
source word (ἀπόστολος, "apostle") can be followed IMMEDIATELY by another `\\zaln-s` with no `\\zaln-e`
in between (that source word gets zero target words — a real, valid case, not malformed markup).
Handled here the same way as before, just over clean USJ nodes instead of raw regex-matched text
offsets: a stack, each `ms/zaln-s` pushes a new open span, each `char/w` attaches its text to whichever
span is topmost/open, each `ms/zaln-e` pops and finalizes the topmost span.

Net effect of the rewrite: LESS code than the hand-rolled version (no `\\c`/`\\v` regex-splitting needed
— USJ's own chapter/verse nodes give that for free), and more robust (usfmtc is a real, spec-compliant,
actively-maintained USFM3 grammar parser handling the WHOLE format — footnotes, cross-references,
poetry, arbitrary nesting — not just the specific patterns two sample books happened to exercise).

SCOPE NOTE, still honest about what this pass delivers: a real, tested core parser
(`usj_spans_for_book`) verified against live TIT/PHM data end-to-end, plus a `fetch_book()`/
`build_book()` pair that writes one JSON file per book under `publish/full-align/arb/ararst/
manual/door43-arst/<BOOK>.json` (one row per (verse_ref, strong, lemma, morph, occurrence,
target_words)). Does NOT yet replicate BSB-tables' full sophistication (bsb_tables.py, ~900 lines: a
Parquet struct schema, spine-side occurrence-disambiguated mapping via `map_source`, a sidecar table,
round-trip `--emit-tsv` verification, manifest merge, gold-health gating). That remains real,
comparable-scope follow-up work.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import usfmtc

_BASE = "https://git.door43.org/BSOJ/ar_arst/raw/branch/master"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
OUT_DIR = Path("publish/full-align/arb/ararst/manual/door43-arst")
_CACHE_DIR = Path("pipeline/work/door43_cache/ar_arst")

_BOOK_FILES = {
    "GEN": "01-GEN", "EXO": "02-EXO", "LEV": "03-LEV", "NUM": "04-NUM", "DEU": "05-DEU",
    "JOS": "06-JOS", "JDG": "07-JDG", "RUT": "08-RUT", "1SA": "09-1SA", "2SA": "10-2SA",
    "1KI": "11-1KI", "2KI": "12-2KI", "1CH": "13-1CH", "2CH": "14-2CH", "EZR": "15-EZR",
    "NEH": "16-NEH", "EST": "17-EST", "JOB": "18-JOB", "PSA": "19-PSA", "PRO": "20-PRO",
    "ECC": "21-ECC", "SNG": "22-SNG", "ISA": "23-ISA", "JER": "24-JER", "LAM": "25-LAM",
    "EZK": "26-EZK", "DAN": "27-DAN", "HOS": "28-HOS", "JOL": "29-JOL", "AMO": "30-AMO",
    "OBA": "31-OBA", "JON": "32-JON", "MIC": "33-MIC", "NAM": "34-NAM", "HAB": "35-HAB",
    "ZEP": "36-ZEP", "HAG": "37-HAG", "ZEC": "38-ZEC", "MAL": "39-MAL", "MAT": "41-MAT",
    "MRK": "42-MRK", "LUK": "43-LUK", "JHN": "44-JHN", "ACT": "45-ACT", "ROM": "46-ROM",
    "1CO": "47-1CO", "2CO": "48-2CO", "GAL": "49-GAL", "EPH": "50-EPH", "PHP": "51-PHP",
    "COL": "52-COL", "1TH": "53-1TH", "2TH": "54-2TH", "1TI": "55-1TI", "2TI": "56-2TI",
    "TIT": "57-TIT", "PHM": "58-PHM", "HEB": "59-HEB", "JAS": "60-JAS", "1PE": "61-1PE",
    "2PE": "62-2PE", "1JN": "63-1JN", "2JN": "64-2JN", "3JN": "65-3JN", "JUD": "66-JUD",
    "REV": "67-REV",
}

def usj_from_usfm(text: str) -> dict:
    """Real USFM3 text -> USJ dict, via `usfmtc` (a real, spec-compliant grammar parser — see module
    docstring for why this replaces a from-scratch regex/text-offset parser)."""
    return usfmtc.USX.fromUsfm(text).outUsj()


def usj_spans_for_book(usj: dict) -> dict[tuple[int, int], list[dict]]:
    """{(chapter, verse): [span, ...]} from a whole book's USJ dict. One depth-first walk over the
    tree, tracking chapter/verse state as `{"type":"chapter"}`/`{"type":"verse"}` milestone nodes are
    encountered (they sit inline in flat `content` arrays, chapters at the top level, verses nested
    inside each `para` block — see module docstring), and a `\\zaln-s`/`\\zaln-e` STACK exactly like the
    superseded regex version used, just applied to real `{"type":"ms"}`/`{"type":"char"}` nodes instead
    of raw text-offset matches. A span is finalized (appended to `out[(chapter, verse)]`) the moment its
    `ms/zaln-e` closes, so a verse accumulates its spans in source order regardless of how many `para`
    blocks or nested elements it's split across."""
    out: dict[tuple[int, int], list[dict]] = {}
    state = {"chapter": 0, "verse": None, "stack": []}

    def close_span(span: dict) -> None:
        if state["verse"] is not None:
            out.setdefault((state["chapter"], state["verse"]), []).append(span)

    def walk(node) -> None:
        if isinstance(node, str):
            return
        if not isinstance(node, dict):
            return
        t = node.get("type")
        if t == "chapter":
            state["chapter"] = int(node.get("number", 0))
            state["verse"] = None
        elif t == "verse":
            state["verse"] = int(node.get("number", 0))
        elif t == "ms" and node.get("marker") == "zaln-s":
            state["stack"].append({"strong": node.get("x-strong"), "lemma": node.get("x-lemma"),
                                   "morph": node.get("x-morph"), "occurrence": node.get("x-occurrence"),
                                   "occurrences": node.get("x-occurrences"),
                                   "content": node.get("x-content"), "target_words": []})
        elif t == "ms" and node.get("marker") == "zaln-e":
            if state["stack"]:
                close_span(state["stack"].pop())
        elif t == "char" and node.get("marker") == "w":
            word = "".join(c for c in node.get("content", []) if isinstance(c, str)).strip()
            if state["stack"]:
                state["stack"][-1]["target_words"].append(word)
        for child in node.get("content", []) if isinstance(node.get("content"), list) else []:
            walk(child)

    for top in usj.get("content", []):
        walk(top)
    # any spans left open at book end (malformed/truncated markup) still get reported, not dropped
    for span in reversed(state["stack"]):
        close_span(span)
    return out


def _fetch(rel: str, cache_dir: Path = _CACHE_DIR) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / rel
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    req = urllib.request.Request(f"{_BASE}/{rel}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https door43 origin
        data = r.read().decode("utf-8")
    fp.write_text(data, encoding="utf-8")
    return data


def fetch_book(book: str, cache_dir: Path = _CACHE_DIR) -> str:
    fname = _BOOK_FILES.get(book)
    if not fname:
        raise ValueError(f"unknown book code {book!r}")
    return _fetch(f"{fname}.usfm", cache_dir)


def build_book(book: str, out_dir: Path = OUT_DIR, cache_dir: Path = _CACHE_DIR) -> dict:
    """Fetch + parse one book, write `<out_dir>/<BOOK>.json` (one row per (chapter, verse, span)),
    return a small per-book stat dict. No spine-side occurrence-disambiguated mapping yet (see module
    docstring's scope note) — rows carry `strong`/`occurrence` as-is, for a future mapper to consume."""
    text = fetch_book(book, cache_dir)
    usj = usj_from_usfm(text)
    verses = usj_spans_for_book(usj)
    rows = []
    n_zero_target = 0
    for (ch, v), spans in sorted(verses.items()):
        for span in spans:
            if not span["target_words"]:
                n_zero_target += 1
            rows.append({"book": book, "chapter": ch, "verse": v, **span})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{book}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    return {"book": book, "verses": len(verses), "spans": len(rows), "zero_target_spans": n_zero_target}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", action="append", help="book code(s) to build; default a small real sample")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    books = args.book or ["RUT", "TIT"]
    for book in books:
        stat = build_book(book, args.out)
        print(json.dumps(stat), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
