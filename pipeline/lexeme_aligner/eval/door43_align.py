"""Roadmap F4 (internal-docs/aim1-typology-source-structure-plan.md, "### full-align"): manual
alignment layers from Door43's real USFM3 `\\zaln` word-alignment markup — Arabic (`ar_arst`) plus,
per the 2026-09-26 extension below, every other language on Door43's own official catalog with
genuinely aligned text, not just the plan's original single-language scope.

CORRECTION TO THE PLAN'S OWN FRAMING, found while investigating (verified against real fetched files,
not assumed): the plan names TWO Arabic editions, `ar_avd`/`ar_arst`, as "the only aligned Bibles the
catalog now returns" — but only ONE of them actually carries alignment data. `ar_avd`
(Door43-Catalog/ar_avd, Arabic Van Dyck) is plain USFM2 text with ZERO `\\zaln` markers anywhere
(checked TIT in full: 0 matches) and is also missing 19-PSA.usfm/23-ISA.usfm from its own file list —
a real, separate gap. `ar_arst` (BSOJ/ar_arst, git.door43.org, updated 2026-09-24) genuinely has real
`\\zaln-s`/`\\zaln-e` markup, keyed to Greek Strong's/lemma/morph/occurrence, full 66-book coverage.
`ar_avd` cannot serve as an aligned manual layer at all, alignment data or not.

MULTI-LANGUAGE EXTENSION (2026-09-26): Door43's official `Door43-Catalog` org publishes a `_glt`/`_gst`
("Gateway Language Translation"/"Gateway Scripture Text") naming convention across many languages,
built via the SAME alignment tooling (tC Create) as `ar_arst`. Checked all 10 real candidates found in
that org's own catalog by fetching a real book from each and counting `\\zaln-s` occurrences — same
"verify, don't assume from the name" discipline as the `ar_avd`/`ar_arst` finding above, since it
recurs: **9 of 10 are genuinely aligned** (`hi_glt` 654 zaln-s in Titus alone, `mr_glt` 613, `bn_gst`
607, `es-419_glt` 652, `gu_glt` 600, `kn_glt` 607, `ne_glt` 576, `or_glt` 630, `te_glt` 599) — only
`vi_glt` (Vietnamese) has ZERO, despite the same naming convention. Coverage per language is real but
PARTIAL and ECLECTIC, not a clean NT/OT split — `hi_glt`'s own 27 books are a mix of some OT (Ruth,
Ezra, Nehemiah, Esther, Obadiah, Jonah) plus most-but-not-all NT (missing Matthew, Acts, Romans,
Galatians, Hebrews, Revelation), consistent with organic, distributed volunteer-checking progress
(shorter/simpler books completed first) rather than a deliberate split. `list_available_books()`
queries each repo's real file listing rather than assuming any fixed book set, for exactly this reason.

LANGUAGE REGISTRY (`LANGUAGES`): `{iso: {"org", "repo", "tag"}}` — `iso` is this project's own
published ISO 639-3 code, verified directly against `publish/lexeme-alignments/manifest.json` before
use (two real corrections found this way: Door43's `ne` maps to our `npi`, not the ISO 639-1-derived
`nep` macrolanguage code; Door43's `or` maps to our `ory`, not the deprecated `ori`). `es-419_glt`
(Latin American Spanish) maps to `spa` — the SAME top-level ISO 639-3 code as this project's own
existing `spa` edition, but likely a DIFFERENT regional/textual tradition; flagged, not silently
assumed equivalent. `vi`/Vietnamese is deliberately excluded (confirmed zero real alignment data).

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
(`usj_spans_for_book`) verified against live data end-to-end for all 9 confirmed-aligned languages,
plus `fetch_book()`/`build_book()`/`build_language()` that write one JSON file per book under
`pipeline/work/door43_dumps/<iso>/<tag>/manual/door43-<name>/<BOOK>.json` (one row per (verse_ref, strong,
lemma, morph, occurrence, target_words)), for whichever books a language's own repo actually has
(discovered via `list_available_books()`, never assumed). Does NOT yet replicate BSB-tables' full
sophistication (bsb_tables.py, ~900 lines: a Parquet struct schema, spine-side occurrence-
disambiguated mapping via `map_source`, a sidecar table, round-trip `--emit-tsv` verification,
manifest merge, gold-health gating). That remains real, comparable-scope follow-up work, for every
language here, not just Arabic.
"""
from __future__ import annotations

import functools
import json
import sys
import urllib.request
from pathlib import Path

import usfmtc

_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"
_API_BASE = "https://git.door43.org/api/v1/repos"
_CACHE_ROOT = Path("pipeline/work/door43_cache")
_PUBLISH_ROOT = Path("pipeline/work/door43_dumps")     # raw per-book dumps (inputs, not a layer); moved out of publish/ 2026-10-09

# {iso: {"org", "repo", "tag"}} — iso verified against publish/lexeme-alignments/manifest.json (see
# module docstring for the two real npi/ory corrections). `tag` follows this repo's own convention for
# a non-onboarded third-party manual-layer source (invented, not from config/pins/ — same pattern as
# `ararst` below), and doubles as the `door43-<tag>` output directory name.
LANGUAGES = {
    "arb": {"org": "BSOJ", "repo": "ar_arst", "tag": "ararst"},      # NOT ARABIC: the repo is the English UST (USFM `\\id ... EN_UST en_English`; found 2026-10-09) — quarantined in gold_to_fullalign
    "hin": {"org": "Door43-Catalog", "repo": "hi_glt", "tag": "higlt"},
    "mar": {"org": "Door43-Catalog", "repo": "mr_glt", "tag": "marglt"},
    "ben": {"org": "Door43-Catalog", "repo": "bn_gst", "tag": "bengst"},
    "spa": {"org": "Door43-Catalog", "repo": "es-419_glt", "tag": "es419glt"},
    "guj": {"org": "Door43-Catalog", "repo": "gu_glt", "tag": "gujglt"},
    "kan": {"org": "Door43-Catalog", "repo": "kn_glt", "tag": "knglt"},
    "npi": {"org": "Door43-Catalog", "repo": "ne_glt", "tag": "neglt"},
    "ory": {"org": "Door43-Catalog", "repo": "or_glt", "tag": "orglt"},
    "tel": {"org": "Door43-Catalog", "repo": "te_glt", "tag": "telglt"},
    # "vie": intentionally excluded — Door43-Catalog/vi_glt confirmed to carry ZERO real \zaln markers
    # despite the same naming convention (checked directly, not assumed from the name).
}

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
    return _walk_book(usj)[0]


def usj_verses_for_book(usj: dict) -> tuple[dict[tuple[int, int], list[dict]], dict[tuple[int, int], str],
                                            dict[tuple[int, int], list[str]]]:
    """E2 (2026-09-27): `(spans, texts, words)` — the same spans as `usj_spans_for_book` (each
    additionally carrying `target_positions`, the verse-local 0-based index of each of its `\\w` words),
    the verse's own clean text (every `\\w` word and every plain-string fragment — punctuation, spacing —
    in document order, milestones dropped), and the verse's full `\\w` word list in order (aligned or
    not, so `target_positions` index into it). The text is what gets written as this language's own
    ingest-cache edition, so the gold's target positions and OUR tokenization of the same verse derive
    from one string — the only way `pos_score.map_positions` can reconcile them."""
    return _walk_book(usj)


def _walk_book(usj: dict) -> tuple[dict[tuple[int, int], list[dict]], dict[tuple[int, int], str],
                                   dict[tuple[int, int], list[str]]]:
    out: dict[tuple[int, int], list[dict]] = {}
    texts: dict[tuple[int, int], list[str]] = {}
    words: dict[tuple[int, int], list[str]] = {}          # every \w word of the verse, aligned or not
    state = {"chapter": 0, "verse": None, "stack": [], "seq": 0, "wpos": 0}

    def close_span(span: dict) -> None:
        if state["verse"] is not None:
            out.setdefault((state["chapter"], state["verse"]), []).append(span)

    def _cur_text() -> list[str] | None:
        if state["verse"] is None:
            return None
        return texts.setdefault((state["chapter"], state["verse"]), [])

    def _clean(s: str) -> str:
        # Nepali (ne_glt) writes ZERO WIDTH JOINER/NON-JOINER inside words ("परमेश्‍वरको"); our
        # tokenizer and `clear_tokens` treat them differently, which left 4% of npi's gold words
        # unplaceable (5,432 of 131k, vs <0.5% for every other language) before this strip.
        return s.replace("‍", "").replace("‌", "")

    def walk(node) -> None:
        if isinstance(node, str):
            buf = _cur_text()
            if buf is not None and node.strip():
                buf.append(_clean(node))
            return
        if not isinstance(node, dict):
            return
        t = node.get("type")
        if t == "chapter":
            state["chapter"] = int(node.get("number", 0))
            state["verse"] = None
        elif t == "verse":
            # a bridged verse marker ("50-51") takes the first number — same convention
            # versification.py's own _usj_structure() already uses for the identical real case.
            state["verse"] = int(str(node.get("number", 0)).split("-")[0])
            state["wpos"] = 0
        elif t == "ms" and node.get("marker") == "zaln-s":
            # `seq`: the order this span OPENED in, monotonic in real text position regardless of
            # nesting (a parent phrase-level milestone and its children all open in left-to-right
            # order; only CLOSE order is LIFO/non-monotonic when spans nest). door43_map.py sorts on
            # this instead of trusting `x-occurrence`/`x-occurrences`, which are scoped to the
            # ENCLOSING nested milestone rather than the whole verse whenever spans nest (found on
            # real Hindi hi_glt data, 2026-09-27: a repeated word split across two different parent
            # phrase groups each report `occurrences=2` locally, not the true verse-wide count).
            state["seq"] += 1
            state["stack"].append({"strong": node.get("x-strong"), "lemma": node.get("x-lemma"),
                                   "morph": node.get("x-morph"), "occurrence": node.get("x-occurrence"),
                                   "occurrences": node.get("x-occurrences"), "seq": state["seq"],
                                   "content": node.get("x-content"), "target_words": [],
                                   "target_positions": []})
        elif t == "ms" and node.get("marker") == "zaln-e":
            if state["stack"]:
                close_span(state["stack"].pop())
        elif t == "char" and node.get("marker") == "w":
            word = _clean("".join(c for c in node.get("content", []) if isinstance(c, str)).strip())
            buf = _cur_text()
            if buf is not None:
                buf.append(word)
                words.setdefault((state["chapter"], state["verse"]), []).append(word)
            if state["stack"]:
                state["stack"][-1]["target_words"].append(word)
                state["stack"][-1]["target_positions"].append(state["wpos"])
            state["wpos"] += 1
            return                                   # a \w node's content is its word — already consumed
        for child in node.get("content", []) if isinstance(node.get("content"), list) else []:
            walk(child)

    for top in usj.get("content", []):
        walk(top)
    # any spans left open at book end (malformed/truncated markup) still get reported, not dropped
    for span in reversed(state["stack"]):
        close_span(span)
    clean = {}
    for key, pieces in texts.items():
        # words are separated by a space; a punctuation fragment attaches to the preceding word
        s = ""
        for p in pieces:
            p = p.replace("\n", " ").strip()
            if not p:
                continue
            if s and p[0].isalnum() or (s and not s[-1].isspace() and p[0] not in ",.;:!?)»”’"):
                s += " " + p
            else:
                s += p
        clean[key] = " ".join(s.split())
    return out, clean, words


def _api_get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https door43 origin
        return json.loads(r.read().decode("utf-8"))


def _raw_base(iso: str) -> str:
    lang = LANGUAGES[iso]
    return f"https://git.door43.org/{lang['org']}/{lang['repo']}/raw/branch/master"


def list_available_books(iso: str) -> dict[str, str]:
    """{book_code: filename_stem} for whichever books `iso`'s real Door43 repo actually has — queried
    live from the repo's own file listing, never assumed from `_BOOK_FILES`'s full 66-book set, since
    real coverage is partial and eclectic per language (see module docstring)."""
    lang = LANGUAGES[iso]
    url = f"{_API_BASE}/{lang['org']}/{lang['repo']}/contents"
    entries = _api_get_json(url)
    stems = {e["name"][:-5] for e in entries if isinstance(e, dict) and e["name"].endswith(".usfm")}
    stem_to_book = {v: k for k, v in _BOOK_FILES.items()}
    return {stem_to_book[s]: s for s in stems if s in stem_to_book}


def _fetch(iso: str, rel: str) -> str:
    cache_dir = _CACHE_ROOT / iso
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / rel
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    req = urllib.request.Request(f"{_raw_base(iso)}/{rel}", headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310 — fixed https door43 origin
        data = r.read().decode("utf-8")
    fp.write_text(data, encoding="utf-8")
    return data


def fetch_book(iso: str, book: str) -> str:
    fname = _BOOK_FILES.get(book)
    if not fname:
        raise ValueError(f"unknown book code {book!r}")
    return _fetch(iso, f"{fname}.usfm")


def _out_dir(iso: str) -> Path:
    return _PUBLISH_ROOT / iso / LANGUAGES[iso]["tag"] / "manual" / f"door43-{LANGUAGES[iso]['tag']}"


def build_book(iso: str, book: str, out_dir: Path | None = None) -> dict:
    """Fetch + parse one book for `iso`, write `<out_dir>/<BOOK>.json` (one row per (chapter, verse,
    span)), return a small per-book stat dict. No spine-side occurrence-disambiguated mapping yet (see
    module docstring's scope note) — rows carry `strong`/`occurrence` as-is, for a future mapper to
    consume."""
    text = fetch_book(iso, book)
    usj = usj_from_usfm(text)
    verses = usj_spans_for_book(usj)
    rows = []
    n_zero_target = 0
    for (ch, v), spans in sorted(verses.items()):
        for span in spans:
            if not span["target_words"]:
                n_zero_target += 1
            rows.append({"book": book, "chapter": ch, "verse": v, **span})
    out_dir = out_dir or _out_dir(iso)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{book}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    return {"book": book, "verses": len(verses), "spans": len(rows), "zero_target_spans": n_zero_target}


@functools.lru_cache(maxsize=None)
def parsed_book(iso: str, book: str) -> tuple:
    """`usj_verses_for_book(usj_from_usfm(fetch_book(iso, book)))`, cached per process — the USFM3
    parse is the expensive step (~10 s/book) and `write_edition` + `door43_gold.build_gold` both need
    the same parse for the same book in one run (2026-09-27: the first full build parsed every book
    twice)."""
    return usj_verses_for_book(usj_from_usfm(fetch_book(iso, book)))


def write_edition(iso: str, books: list[str] | None = None,
                  ingest_cache: Path = Path("pipeline/work/ingest-cache")) -> dict:
    """E2 (2026-09-27): write this Door43 language's OWN text as an ingest-cache edition
    `usj-<tag>/<NN>-<BOOK>.json` in this repo's USJ layout (run_pilot's book numbering, one
    `{"type":"verse"}` marker + one plain text string per verse — the shape every other adapter
    produces), from the same USFM the alignment rows come from, via `usj_verses_for_book`. That makes
    the human alignment usable as gold for OUR chain run on the SAME text (the SWORD/HELFI precedent),
    which no other language of ours has for Door43's Indic set. Returns {book: n_verses}."""
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    tag = LANGUAGES[iso]["tag"]
    dest = Path(ingest_cache) / f"usj-{tag}"
    dest.mkdir(parents=True, exist_ok=True)
    books = books or sorted(list_available_books(iso))
    written: dict[str, int] = {}
    for book in books:
        _spans, texts, _words = parsed_book(iso, book)
        content: list = [{"type": "book", "marker": "id", "code": book, "content": []}]
        cur_ch = None
        para: dict | None = None
        for (ch, v) in sorted(texts):
            if ch != cur_ch:
                content.append({"type": "chapter", "marker": "c", "number": str(ch)})
                para = {"type": "para", "marker": "p", "content": []}
                content.append(para)
                cur_ch = ch
            para["content"].append({"type": "verse", "marker": "v", "number": str(v)})
            para["content"].append(texts[(ch, v)] + "\n")
        doc = {"type": "USJ", "version": "3.0", "content": content}
        (dest / f"{_BOOK_FILE_NUM[book]}-{book}.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        written[book] = len(texts)
    return written


def build_language(iso: str, out_dir: Path | None = None) -> dict:
    """Build every book `iso`'s real Door43 repo actually has (via `list_available_books`), return a
    per-language stat dict. Never assumes full-Bible coverage."""
    books = list_available_books(iso)
    stats = [build_book(iso, book, out_dir) for book in sorted(books)]
    return {"iso": iso, "tag": LANGUAGES[iso]["tag"], "books": len(stats),
           "verses": sum(s["verses"] for s in stats), "spans": sum(s["spans"] for s in stats),
           "zero_target_spans": sum(s["zero_target_spans"] for s in stats)}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", action="append", choices=sorted(LANGUAGES),
                    help="language(s) to build; default all confirmed-aligned languages")
    ap.add_argument("--book", action="append", help="restrict to specific book code(s) (single --iso only)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    isos = args.iso or sorted(LANGUAGES)
    if args.book and len(isos) != 1:
        ap.error("--book requires exactly one --iso")
    for iso in isos:
        if args.book:
            for book in args.book:
                print(json.dumps(build_book(iso, book, args.out)), file=sys.stderr)
        else:
            print(json.dumps(build_language(iso, args.out)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
