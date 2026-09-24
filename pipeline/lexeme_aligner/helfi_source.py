"""amikael/HELFI (Hebrew-Greek-Finnish parallel corpus, LREC 2020) -> our own Finnish 1933/1938 USJ
edition + a gold parquet in `pos_score.load_gold`'s schema. Step G0 of internal-docs/
aim1-typology-source-structure-plan.md — the one gold source this session that is NOT a SWORD module
(`sword_source.py`) and needs its own parser: HELFI's alignment is row-per-TARGET-WORD (the opposite
shape of SWORD's tag-wraps-target-word), morpheme-level, and cross-references a SEPARATE per-testament
source file rather than embedding the Strong's number in the same file as the target text.

LICENSING (github.com/amikael/HELFI/blob/master/LICENSES.md, read in full before touching this data):
the corpus mixes several separately-licensed parts, and only some of them are safe to use.
  - **Used here, all clean**: the Finnish 1933/1938 translation text (Public Domain); the alignment
    LINK NUMBERS and LINK QUALIFIERS ("the alignment data ... consists exclusively of the alignment
    link numbers, the gloss extractors, and the link qualifiers", CC-BY-4.0, attribute "the Finnish
    Analytical Bible Concordance Project of Aika[media] Oy"); the Greek1904 lemma+Strong's+morphology
    (Public Domain, Robinson/Sandborg-Petersen's Nestle1904 project); the Hebrew1008 lemma+morphology
    (CC-BY-4.0, Open Scriptures Hebrew Bible Project); the Leningrad Codex Hebrew text itself (PD).
  - **Deliberately NOT touched**: `Finnish1938/fi-FABC-2020m3-AIKA-morphology/` — the FULL Finnish-side
    morphological analysis is **CC-BY-NC-ND 4.0**, a materially different (and stricter) license than
    the alignment data. We fetch ONLY the `fi-FABC-2020m3-HELFI-alignment/` directory, which the
    repo's own README confirms is text + alignment WITHOUT that morphology layer.

FILE LAYOUT, fetched from raw.githubusercontent.com/amikael/HELFI/master/ (cached under
`pipeline/vendor/helfi/`, gitignored, pinned commit in `config/PROVENANCE.txt`):
  - `Finnish1938/fi-FABC-2020m3-HELFI-alignment/{01-66}-<code>.txt` — one row per TARGET (Finnish)
    token or punctuation mark, in document order. Both testaments are numbered 01-66 in the SAME
    canonical order our own `OT_BOOKS + NT_BOOKS` uses (verified against the file listing), so the
    file's own leading number is the book's position in that combined list — no name-matching table.
  - `Hebrew1008/{01-39}-<code>-<EngName>.txt` (OT, Leningrad Codex) / `Greek1904/{40-66}-<code>-
    <EngName>.txt` (NT, Nestle 1904) — one row per SOURCE token, column 3 = `lemma/strongish/ownid`
    where the MIDDLE field is a real Strong's number for a lexical token (verified against known
    words: Ἰούδας/**2455**=G2455, θεός/**2316**=G2316) or a single-letter grammatical-morpheme code
    for a Hebrew prefix particle (`c`=conjunction waw, `b`/`m`/`l`=prepositions, `d`=article — these
    have no Strong's number in the source dictionary and are correctly excluded, not a parsing gap).

ALIGNMENT ROW SHAPE (tab-separated; see `_parse_alignment_file`'s own docstring for the field-by-field
walkthrough) — the two things this parser has to get right that `sword_source.py` didn't need to:
  1. **Row-per-target-word, not tag-wraps-word**: a row's own presence/order in the file IS the
     target token sequence — there is no separate tokenizer step the way OSIS/GBF needed one, EXCEPT
     the final cross-check against `pos_score.clear_tokens` on the reconstructed text still applies
     (same "drop the verse rather than misplace a link" discipline as `sword_source.py`).
  2. **A row can cite SEVERAL source token ids** (`(4a) 4b` — one Finnish word rendering two Hebrew
     morphemes, one loosely-aligned/parenthesized and one confident) and the SAME source id can be
     cited by SEVERAL rows (`2b` on both "Siihen" and "aikaan" — one Hebrew word needing a two-word
     Finnish phrase) — the mirror image of OSIS's "one tag, several target words", handled the same
     way `sword_source.py` handles it: a per-verse `tag_seq` ordinal assigned on first appearance of
     each DISTINCT source id string, shared across every row that cites it.
Rows with no target surface at all (`UNTRANSLATED`, `%qualifier` rows like `%case LOC`, the synthetic
`VERSE` header row) contribute nothing to the target sequence or the gold — they mark information this
gold format doesn't need (an untranslated source word; a grammatical feature absorbed into a case
ending rather than a separate word), not a parsing failure.

VERIFIED against known Strong's numbers (Ruth + Jude, both testaments, by hand): Ἰούδας/2455->G2455,
θεός/2316->G2316, πατήρ/3962->G3962 all correctly reach their Finnish rendering; H1481a's suffix
survives normalization (an augmented/polysemous Strong's, same convention Clear's own gold uses).
DROPPED-VERSE CAUSES, checked (both benign, both the safety net doing its job, not a parsing bug):
(1) `pos_score.clear_tokens`'s own apostrophe-attachment lookahead treats Finnish direct-speech quote
marks ("Minulla on vielä toivoa'") as an attaching apostrophe the way French "d'"/"c'" needs — a real,
documented cross-tokenizer disagreement, not something this module should special-case around (the
verse is dropped, not mis-scored). (2) HELFI marks a compound-word alignment split with a literal
`-~/` placeholder in the target surface (`lunastus-~/lunastuskauppoja`) that `clear_tokens` shatters
into extra punctuation tokens — rare (measured ~2% of Ruth+Jude verses combined), left undetected-
around for the same reason.

    python3 -m lexeme_aligner.helfi_source --fetch                 # pull + cache the 3 directories
    python3 -m lexeme_aligner.helfi_source --build-usj <out-dir>   # write our own fin_helfi USJ edition
    python3 -m lexeme_aligner.helfi_source --build-gold [--book RUT ...] [--all]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import urllib.request
from pathlib import Path

from lexeme_aligner.pos_score import clear_tokens
from lexeme_aligner.refs import BOOK_NUMBERS
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
from lexeme_aligner.sword_source import _valid_strong, normalize_strong, write_parquet

_RAW = "https://raw.githubusercontent.com/amikael/HELFI/master/{path}"
_CACHE = Path("pipeline/vendor/helfi")
_ALL_BOOKS = OT_BOOKS + NT_BOOKS                                      # index 0..65 == HELFI file number 1..66

# Filenames as listed in the repo (2026-09-24) — fetched once via --fetch, not re-derived at runtime,
# so a future repo reorganisation doesn't silently break this (an explicit KeyError instead).
_ALIGN_CODES = ["gn", "ex", "lv", "nu", "dt", "js", "jg", "ru", "1s", "2s", "1k", "2k", "1x", "2x",
                "er", "ne", "es", "jb", "ps", "pr", "ec", "ca", "is", "jr", "lm", "ek", "da", "ho",
                "jl", "am", "ob", "on", "mi", "na", "ha", "zp", "hg", "zc", "ma",
                "mt", "mk", "lk", "jn", "ac", "rm", "1c", "2c", "ga", "ep", "pp", "cl", "1q", "2q",
                "1t", "2t", "ti", "pm", "hb", "jm", "1p", "2p", "1j", "2j", "3j", "jd", "rv"]
_HEB_NAMES = ["Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth", "1Sam", "2Sam", "1Kgs",
             "2Kgs", "1Chr", "2Chr", "Ezra", "Neh", "Esth", "Job", "Ps", "Prov", "Eccl", "Song",
             "Isa", "Jer", "Lam", "Ezek", "Dan", "Hos", "Joel", "Amos", "Obad", "Jonah", "Mic",
             "Nah", "Hab", "Zeph", "Hag", "Zech", "Mal"]
_GRK_NAMES = ["Matt", "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil",
             "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus", "Phlm", "Heb", "Jas", "1Pet",
             "2Pet", "1John", "2John", "3John", "Jude", "Rev"]
assert len(_ALIGN_CODES) == 66 == len(_HEB_NAMES) + len(_GRK_NAMES)

_TRIPLE = re.compile(r"^([^/]*)/([^/]*)/([^/]*)$")


def _align_path(n: int) -> str:
    return f"Finnish1938/fi-FABC-2020m3-HELFI-alignment/{n:02d}-{_ALIGN_CODES[n - 1]}.txt"


def _source_path(n: int) -> str:
    if n <= 39:
        return f"Hebrew1008/{n:02d}-{_ALIGN_CODES[n - 1]}-{_HEB_NAMES[n - 1]}.txt"
    return f"Greek1904/{n:02d}-{_ALIGN_CODES[n - 1]}-{_GRK_NAMES[n - 40]}.txt"


def fetch(cache_dir: Path = _CACHE, quiet: bool = False, workers: int = 12) -> None:
    """Download every file `build_gold`/`build_usj` need, if not already cached. Idempotent — safe to
    call before every run; only fetches what's missing. 132 small files (66 books x 2) over plain
    HTTPS one-at-a-time takes minutes (per-request latency dominates, not bandwidth) — fetched
    concurrently with a small thread pool instead."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [rel for n in range(1, 67) for rel in (_align_path(n), _source_path(n))
           if not (cache_dir / rel).exists()]
    if not todo:
        return

    def _get(rel: str) -> str:
        dest = cache_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(_RAW.format(path=rel), timeout=30) as resp:
            dest.write_bytes(resp.read())
        return rel

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_get, rel): rel for rel in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            rel = futures[fut]
            fut.result()                                              # surfaces any download error
            if not quiet:
                print(f"[helfi_source] fetched {i}/{len(todo)} {rel}", file=sys.stderr)


def _cached(cache_dir: Path, rel: str) -> str:
    fp = cache_dir / rel
    if not fp.exists():
        raise SystemExit(f"[helfi_source] {fp} missing — run --fetch first")
    return fp.read_text(encoding="utf-8")


def parse_source_file(text: str, letter: str) -> dict[tuple[int, int, str], str | None]:
    """{(chapter, verse, token_id): normalized Strong's id or None} for one Hebrew1008/Greek1904 file.
    `letter`: "H" or "G". Chapter "000" (single-chapter books: Jude, Philemon, Obadiah, 2/3 John) is
    Bible chapter 1 — HELFI's own convention, not a bug. None = a grammatical-morpheme letter code, a
    compound "1035+"-style id, or a bare "-" placeholder — none carry a lexical Strong's number, so
    they contribute no gold row (this is the OSHB/ETCBC convention for prefix particles, the same
    reason our own spine treats them as non-content tokens)."""
    out: dict[tuple[int, int, str], str | None] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        ref, token_id, triple = cols[0], cols[1], cols[2]
        m = re.match(r"[a-z0-9]+(\d{3}):(\d{3})$", ref)
        if not m:
            continue
        chapter = 1 if m.group(1) == "000" else int(m.group(1))
        verse = int(m.group(2))
        tm = _TRIPLE.match(triple)
        strong = None
        if tm:
            mid = tm.group(2)
            if re.match(r"^\d+[a-z]?$", mid):
                num_s = re.match(r"^(\d+)([a-z]?)$", mid)
                num, suffix = int(num_s.group(1)), num_s.group(2)
                if _valid_strong(letter, num):
                    strong = normalize_strong(letter, num, suffix)
        out[(chapter, verse, token_id)] = strong
    return out


def _clean_source_ids(field: str) -> list[str]:
    if field in ("", "-"):
        return []
    return [tok.strip("()") for tok in field.split()]


def parse_alignment_file(text: str) -> dict[tuple[int, int], tuple[list[str], list[list[str]]]]:
    """{(chapter, verse): (target_surface_tokens, source_id_lists)} — `source_id_lists[i]` is the list
    of source token ids row i's target token cites (empty if none). Both lists are in document/target
    order and the same length, one entry per row that carried an actual target surface (UNTRANSLATED/
    qualifier/VERSE rows are skipped — see module docstring). Trailing `␣` (U+2423, HELFI's own
    explicit "a space follows" marker) becomes a real space in the reconstructed surface; its absence
    means the next token attaches directly (typically before punctuation)."""
    verses: dict[tuple[int, int], tuple[list[str], list[list[str]]]] = {}
    chapter = verse = 0
    toks: list[str] = []
    src_lists: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        while len(cols) < 5:
            cols.append("")
        ref, src_field, _kind, tag, surface = cols[0], cols[1], cols[2], cols[3], cols[4]
        if tag == "VERSE":
            if toks:
                verses[(chapter, verse)] = (toks, src_lists)
            m = re.match(r"[a-z0-9]+(\d{3}):(\d{3})$", ref)
            chapter = 1 if m.group(1) == "000" else int(m.group(1))
            verse = int(m.group(2))
            toks, src_lists = [], []
            continue
        if not surface:
            continue                                                  # UNTRANSLATED / %qualifier row — no target word
        word = surface.replace("␣", " ")
        toks.append(word)
        src_lists.append(_clean_source_ids(src_field))
    if toks:
        verses[(chapter, verse)] = (toks, src_lists)
    return verses


def build_gold_and_text(align_text: str, source_lookup: dict[tuple[int, int, str], str | None],
                        book: str, base_text: str) -> tuple[list[dict], dict[tuple[int, int], str], dict]:
    """Gold rows (Clear schema) + {(chapter, verse): plain text} for one book, plus stats. `book` is
    our USFM code, used to build `ref`/`target_id` the way `pos_score.load_gold` expects."""
    rows: list[dict] = []
    texts: dict[tuple[int, int], str] = {}
    stats: collections.Counter = collections.Counter()
    for (chapter, verse), (toks, src_lists) in parse_alignment_file(align_text).items():
        stats["verses"] += 1
        text = " ".join(toks)                                        # HELFI already tokenizes; join with spaces
        if len(toks) != len(clear_tokens(text)):
            stats["verses_dropped_tokenization_mismatch"] += 1
            continue
        texts[(chapter, verse)] = text
        ref = f"{BOOK_NUMBERS[book]:02d}{chapter:03d}{verse:03d}"
        seen_source_ids: dict[str, int] = {}
        for pos_idx, src_ids in enumerate(src_lists):
            for sid in src_ids:
                strong = source_lookup.get((chapter, verse, sid))
                if strong is None:
                    continue
                if sid not in seen_source_ids:
                    seen_source_ids[sid] = len(seen_source_ids)
                rows.append({
                    "strong": strong, "lemma": "", "surface": toks[pos_idx],
                    "ref": ref, "target_id": f"{ref}{pos_idx + 1:03d}",
                    "source_id": f"n{ref}{seen_source_ids[sid]:03d}",
                    "method": "helfi", "source_corpus": ("WLC" if book in OT_BOOKS else "Nestle1904"),
                    "base_text": base_text,
                })
        stats["rows"] += sum(1 for src_ids in src_lists for sid in src_ids
                             if source_lookup.get((chapter, verse, sid)) is not None)
    return rows, texts, dict(stats)


def build_usj_book(book: str, texts: dict[tuple[int, int], str]) -> dict:
    """A minimal, valid USJ 3.0 book document — just enough structure for `usj_source.read_verses`
    (book id, chapter markers, one `para`/`verse`/text triple per verse). No per-word markup: our own
    alignment reads the shared spine for source-side structure, not anything embedded in the target
    USJ, so a plain-text verse is all a target edition ever needs to provide."""
    content: list = [{"type": "book", "marker": "id", "code": book,
                      "content": ["HELFI Finnish 1933/1938 (Public Domain text; CC-BY-4.0 alignment, "
                                 "Finnish Analytical Bible Concordance Project of Aika[media] Oy)"]}]
    cur_chapter = 0
    for (chapter, verse), text in sorted(texts.items()):
        if chapter != cur_chapter:
            content.append({"type": "chapter", "marker": "c", "number": str(chapter)})
            cur_chapter = chapter
        content.append({"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": str(verse)}, text,
        ]})
    return {"type": "USJ", "version": "3.0", "content": content}


def build_all(cache_dir: Path = _CACHE, base_text: str = "HELFI",
              books: list[str] | None = None) -> tuple[list[dict], dict[str, dict], dict]:
    """All gold rows + {book: {(ch,v): text}} + combined stats, for `books` (default: whole Bible)."""
    wanted = set(books) if books else None
    all_rows: list[dict] = []
    all_texts: dict[str, dict] = {}
    stats: collections.Counter = collections.Counter()
    for n in range(1, 67):
        book = _ALL_BOOKS[n - 1]
        if wanted and book not in wanted:
            continue
        letter = "H" if n <= 39 else "G"
        source_lookup = parse_source_file(_cached(cache_dir, _source_path(n)), letter)
        align_text = _cached(cache_dir, _align_path(n))
        rows, texts, book_stats = build_gold_and_text(align_text, source_lookup, book, base_text)
        all_rows.extend(rows)
        all_texts[book] = texts
        for k, v in book_stats.items():
            stats[k] += v
    return all_rows, all_texts, dict(stats)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--build-usj", type=Path, default=None, metavar="DIR",
                    help="write fin_helfi USJ books into DIR (e.g. pipeline/work/ingest-cache/usj-fin_helfi)")
    ap.add_argument("--build-gold", action="store_true")
    ap.add_argument("--out", type=Path,
                    default=Path("pipeline/vendor/resources/strongs/attestations/fin.parquet"))
    ap.add_argument("--base-text", default="HELFI")
    ap.add_argument("--book", action="append")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cache-dir", type=Path, default=_CACHE)
    ap.add_argument("--no-merge", action="store_true")
    a = ap.parse_args(argv)

    if a.fetch:
        fetch(a.cache_dir)
    books = (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in a.book] if a.book else None)
    if a.build_usj or a.build_gold:
        rows, texts, stats = build_all(a.cache_dir, a.base_text, books)
        print(f"[helfi_source] {stats}", file=sys.stderr)
        if a.build_usj:
            a.build_usj.mkdir(parents=True, exist_ok=True)
            for book, book_texts in texts.items():
                if not book_texts:
                    continue
                fp = a.build_usj / f"{BOOK_NUMBERS[book]:02d}-{book}.json"
                fp.write_text(json.dumps(build_usj_book(book, book_texts), ensure_ascii=False), encoding="utf-8")
            print(f"[helfi_source] wrote {sum(1 for t in texts.values() if t)} USJ book(s) to {a.build_usj}",
                 file=sys.stderr)
        if a.build_gold:
            write_parquet(rows, a.out, merge=not a.no_merge)
            print(f"[helfi_source] {len(rows)} gold rows -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
