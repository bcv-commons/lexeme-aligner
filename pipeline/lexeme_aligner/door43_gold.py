"""E2 (internal-docs/aim1-multiword-grammar-tools-plan.md, 2026-09-27): Door43's human word alignment
as a GOLD source in the exact shape `pos_score.load_gold` already reads — the Clear/SWORD/HELFI
attestation parquet (`pipeline/vendor/resources/strongs/attestations/<iso>.parquet`, columns
`strong, lemma, surface, ref, target_id, source_id, method, source_corpus, base_text`), one row per
(source word, target word), `method="door43"`.

Why it matters: Door43's 10 aligned languages include five we have NO gold for at all (guj, kan, mar,
npi, ory) and second, multi-word-aware human sources for arb/ben/hin/spa/tel — and they are exactly the
postpositional/Indic languages where the multi-word gap is largest (plan §8.H1: 47.9% of Hindi content
tokens get a multi-word span from a human; our gram-align gives 19.5%).

Greek NT only in this first pass (Hebrew rows use unfoldingWord's morpheme-prefix strong coding —
`c:H1961`, `d:H8199` — a crosswalk to MACULA's own split prefix tokens is a separate follow-up, see
door43_map.py's docstring). Positions: Clear's `target_id` ends in the 1-based index into
`pos_score.clear_tokens(verse_text)`; we hold each `\\w` word's verse-local index from
`door43_align.usj_verses_for_book` and map it onto the clear-token sequence of the SAME verse text
(the text `door43_align.write_edition` writes as this language's edition) with the same greedy
letter-containment `pos_score.map_positions` uses, so both sides index one string. `source_id` order
(`n<ref><seq:03d>`) is the span OPEN order — monotonic in Greek text position (door43_align.py's own
`seq` comment) — which is what `load_gold` counts k-th occurrences over.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from lexeme_aligner import door43_align as da
from lexeme_aligner.config import RESOURCES
from lexeme_aligner.door43_map import normalize_greek_strong
from lexeme_aligner.pos_score import _letters, clear_tokens
from lexeme_aligner.refs import BOOK_NUMBERS, encode


def word_positions_to_clear(words: list[str], text: str) -> list[int | None]:
    """For each `\\w` word (verse order) the 0-based index of the clear token whose letters contain it,
    scanning forward — None when a word can't be placed (never guessed)."""
    toks = clear_tokens(text)
    out: list[int | None] = []
    j = 0
    for w in words:
        letters = _letters(w)
        placed = None
        k = j
        while k < len(toks):
            if letters and letters in _letters(toks[k]):
                placed = k
                j = k + 1
                break
            k += 1
        out.append(placed)
    return out


def rows_for_book(iso: str, book: str, spans_by_verse: dict, texts: dict, words: dict) -> tuple[list[dict], dict]:
    """Gold rows for one Greek-NT book. `words[(ch, v)]` is the verse's full `\\w` list (aligned or not)
    so `target_positions` index into it. Returns (rows, stats)."""
    repo = da.LANGUAGES[iso]["repo"]
    rows: list[dict] = []
    st: collections.Counter = collections.Counter()
    for (ch, v), spans in sorted(spans_by_verse.items()):
        text = texts.get((ch, v))
        all_words = words.get((ch, v), [])
        if not text or not all_words:
            st["verse_no_text"] += 1
            continue
        ref = encode(book, ch, v)
        ref8 = f"{ref:08d}"
        clear_idx = word_positions_to_clear(all_words, text)
        for s in spans:
            bare = normalize_greek_strong(s.get("strong"))
            if not bare:
                st["non_greek_or_null_strong"] += 1
                continue
            if not s["target_words"]:
                st["zero_target"] += 1
                continue
            sid = f"n{ref8}{int(s['seq']):03d}"
            for p, w in zip(s["target_positions"], s["target_words"]):
                ci = clear_idx[p] if p < len(clear_idx) else None
                if ci is None:
                    st["unplaced_word"] += 1
                    continue
                rows.append({"strong": bare, "lemma": s.get("lemma") or "", "surface": w, "ref": ref8,
                             "target_id": f"{ref8}{ci + 1:03d}", "source_id": sid, "method": "door43",
                             "source_corpus": "UGNT", "base_text": repo})
                st["rows"] += 1
    return rows, st


def build_gold(iso: str, books: list[str] | None = None, res_dir: Path = RESOURCES) -> dict:
    """Build/merge `<iso>.parquet`: existing rows of other methods are kept, prior `door43` rows replaced.
    Greek NT books only."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    books = books or [b for b in sorted(da.list_available_books(iso)) if BOOK_NUMBERS.get(b, 0) >= 40]
    rows: list[dict] = []
    stats: collections.Counter = collections.Counter()
    for book in books:
        spans, texts, words = da.parsed_book(iso, book)      # cached: write_edition parsed it already
        r, st = rows_for_book(iso, book, spans, texts, words)
        rows.extend(r)
        stats.update(st)
        stats["books"] += 1
    fp = res_dir / "strongs" / "attestations" / f"{iso}.parquet"
    fp.parent.mkdir(parents=True, exist_ok=True)
    cols = ["strong", "lemma", "surface", "ref", "target_id", "source_id", "method", "source_corpus", "base_text"]
    keep: list[dict] = []
    if fp.exists():
        keep = [r for r in pq.read_table(fp, columns=cols).to_pylist() if r["method"] != "door43"]
        stats["kept_other_method_rows"] = len(keep)
    table = pa.table({c: pa.array([r[c] for r in keep + rows], pa.string()) for c in cols})
    pq.write_table(table, fp, compression="zstd")
    stats["parquet"] = str(fp)
    return dict(stats)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", action="append", choices=sorted(da.LANGUAGES), help="default: all")
    ap.add_argument("--write-edition", action="store_true",
                    help="also write the language's own text as ingest-cache edition usj-<tag>/")
    a = ap.parse_args(argv)
    for iso in (a.iso or sorted(da.LANGUAGES)):
        if a.write_edition:
            nt = [b for b in sorted(da.list_available_books(iso)) if BOOK_NUMBERS.get(b, 0) >= 40]
            w = da.write_edition(iso, nt)
            print(f"[door43_gold] {iso}: edition usj-{da.LANGUAGES[iso]['tag']} — {len(w)} book(s), "
                  f"{sum(w.values())} verses", file=sys.stderr)
        print(json.dumps({"iso": iso, **build_gold(iso)}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
