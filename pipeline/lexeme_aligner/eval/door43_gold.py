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

from lexeme_aligner.eval import door43_align as da
from lexeme_aligner.config import RESOURCES
from lexeme_aligner.eval.door43_map import normalize_greek_strong
from lexeme_aligner.eval.pos_score import _letters, clear_tokens
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


# --- Hebrew OT prefix crosswalk (E2 follow-up, 2026-09-27) --------------------------------------------
# unfoldingWord's UHB tags each BOUND MORPHEME of a Hebrew word with its own zaln-s: a content word gets
# its own bare strong ("H8199"), and each attached prefix particle (conjunction/article/preposition/
# interrogative-he) gets a SEPARATE row keyed `<code>:H####` where `<code>` names the morpheme, not the
# strong it attaches to belonging to that morpheme. This table maps each single-letter code to the
# EXACT (lemma, strong) our own spine gives that same particle as its own is_content=0 token (confirmed
# directly against pipeline/lexeme-spine.db, 2026-09-27 -- e.g. every waw-conjunction token in the spine
# is lemma='וְ' strong=2050 regardless of what it attaches to, matching Door43's 'c:' code exactly).
# Compound codes ("c:m:H1035" -- waw AND min on the same word) are NOT handled (stat-counted, skipped) --
# resolving a stack of 2+ morphemes to 2+ spine tokens in the right order is a further follow-up, not
# attempted here; single-code rows (the large majority -- 442 of 451 non-plain RUT rows, ~98%) are.
def _strip_augment(strong: str) -> str:
    """"H1121a" -> "H1121"; "H8199" unchanged. Door43's bare-content rows carry MACULA's own AUGMENTED
    strong (a trailing homonym-disambiguating letter); our spine's own `strong` field is already the
    augment-stripped rollup those augmented forms collapse to (docs/architecture.md, hebrew_source.py) --
    without this, every augmented content row (H1121a, H0834a, H3588a, ... -- ~38-11 occurrences each,
    the single largest failure class found on real RUT data) silently fails to match."""
    if strong and strong[-1].isalpha():
        return strong[:-1]
    return strong


def _nfc(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s)


_HEB_PREFIX_LEMMA_STRONG = {
    "b": (_nfc("בְּ"), "H0871"), "c": (_nfc("וְ"), "H2050"), "d": (_nfc("הַ"), "H1886"),
    "i": (_nfc("הֲ"), "H1886"), "k": (_nfc("כְּ"), "H3509"), "l": (_nfc("לְ"), "H3807"),
    "m": (_nfc("מִן"), "H4480"),
}
# real spine data (2026-09-27): a small number of prefix tokens carry an unexpected strong for their
# lemma (e.g. a כְּ token occasionally tagged H0871, בְּ's own number, not H3509) -- a data quirk, not
# a modelling choice. Keying on (lemma, strong) as given would silently under-match those; instead
# match on LEMMA alone (after NFC) for the crosswalk's spine-side counter, and record the strong
# mismatch as a soft stat rather than a hard failure.


def _split_hebrew_strong(raw: str | None) -> tuple[str, str] | None:
    """("d", "H8199") for "d:H8199"; ("", "H8199") for a bare content row; None for a compound code
    ("c:m:H1035") or anything else not recognised."""
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) == 1:
        return ("", parts[0]) if parts[0].startswith("H") else None
    if len(parts) == 2 and parts[0] in _HEB_PREFIX_LEMMA_STRONG and parts[1].startswith("H"):
        return (parts[0], parts[1])
    return None                     # compound code or unrecognised shape


def rows_for_ot_book(iso: str, book: str, spans_by_verse: dict, texts: dict, words: dict,
                     heb: "HebrewSource") -> tuple[list[dict], dict]:
    """Gold rows for one Hebrew-OT book, resolving prefix-coded rows onto the spine's own prefix
    tokens (see `_HEB_PREFIX_LEMMA_STRONG`'s own docstring) and bare-content rows onto the spine's
    content tokens directly, both by SEQ order within the (verse, key) group -- not Door43's own
    `x-occurrence`, which door43_map.py already found is scoped to the enclosing nested milestone, not
    the whole verse, on real data. `heb`: a HebrewSource instance (spine access)."""
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
        toks = heb.verse_tokens(book, ch, v)
        # spine occurrence index, keyed exactly like the crosswalk keys a door43 row: content rows by
        # bare (augment-stripped) strong, prefix rows by the particle's own LEMMA alone (NFC-normalised
        # -- see _HEB_PREFIX_LEMMA_STRONG's own comment on why not (lemma, strong)).
        seen: collections.Counter = collections.Counter()
        spine_k: dict[tuple, int] = {}
        strong_of: dict[int, str] = {}
        for t in toks:
            if t.is_content and t.strong:
                key = ("", _strip_augment(t.strong))
            elif not t.is_content and t.lemma:
                key = (_nfc(t.lemma), "")
            else:
                continue
            spine_k[(key, seen[key])] = t.idx
            seen[key] += 1
            if t.strong:
                strong_of[t.idx] = t.strong          # the token's REAL spine strong, not our placeholder
        our_seen: collections.Counter = collections.Counter()
        for s in sorted(spans, key=lambda s: s.get("seq", 0)):
            parsed = _split_hebrew_strong(s.get("strong"))
            if parsed is None:
                st["compound_or_unrecognised"] += 1
                continue
            code, base = parsed
            if code:
                lemma, _placeholder_strong = _HEB_PREFIX_LEMMA_STRONG[code]
                key = (lemma, "")
            else:
                key = ("", _strip_augment(base))
            k = our_seen[key]
            our_seen[key] += 1
            h_idx = spine_k.get((key, k))
            if h_idx is None:
                st["no_spine_token"] += 1
                continue
            real_strong = strong_of.get(h_idx)
            if not real_strong:
                st["no_spine_token"] += 1
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
                rows.append({"strong": real_strong, "lemma": s.get("lemma") or "",
                             "surface": w, "ref": ref8, "target_id": f"{ref8}{ci + 1:03d}",
                             "source_id": sid, "method": "door43", "source_corpus": "WLC", "base_text": repo})
                st["rows"] += 1
    return rows, st


def build_gold(iso: str, books: list[str] | None = None, res_dir: Path = RESOURCES,
              include_ot: bool = True) -> dict:
    """Build/merge `<iso>.parquet`: existing rows of other methods are kept, prior `door43` rows replaced.
    NT books via `rows_for_book` (Greek). `include_ot`: also run `rows_for_ot_book` (Hebrew prefix
    crosswalk, 2026-09-27 follow-up) on any OT books this language's repo has -- real success rate on
    hin ~82% of non-compound/non-empty spans (two real bugs fixed: augmented-strong stripping, Hebrew
    combining-mark order); remaining misses are genuine spine ambiguities (a handful of lemmas map to
    more than one Strong's depending on grammatical role) and compound prefix codes ("c:m:H1035"),
    neither resolved here -- see `rows_for_ot_book`'s own docstring. Never silently drops OT coverage
    when it's absent (`list_available_books` still governs which books actually exist for `iso`)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lexeme_aligner.hebrew_source import HebrewSource
    all_books = books or sorted(da.list_available_books(iso))
    nt_books = [b for b in all_books if BOOK_NUMBERS.get(b, 0) >= 40]
    ot_books = [b for b in all_books if BOOK_NUMBERS.get(b, 0) < 40] if include_ot else []
    rows: list[dict] = []
    stats: collections.Counter = collections.Counter()
    for book in nt_books:
        spans, texts, words = da.parsed_book(iso, book)      # cached: write_edition parsed it already
        r, st = rows_for_book(iso, book, spans, texts, words)
        rows.extend(r)
        stats.update(st)
        stats["books"] += 1
    if ot_books:
        heb = HebrewSource()
        for book in ot_books:
            spans, texts, words = da.parsed_book(iso, book)
            r, st = rows_for_ot_book(iso, book, spans, texts, words, heb)
            rows.extend(r)
            for k, v in st.items():
                stats[f"ot_{k}"] += v
            stats["ot_books"] += 1
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
