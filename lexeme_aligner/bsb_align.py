"""Extract source(heb/grc)<->target(eng) occurrence alignment from BSB-publishing/bsb-data-output's
`base/display/` — the Berean Standard Bible publisher's own Strong's-tagged word spans. Same
"occurrence layer" concept as gbt_align.py, but structurally simpler: BSB tags each SOURCE word with
exactly one Strong's code and pairs it with a target-language SPAN (which can be phrase-length text
or, when the source word has no separate English rendering, an explicit `{"elided": true}` marker on
an empty-text span) — no id-gap addressing scheme, no offset heuristic. Both `heb` (or `grc`) and
`eng` arrays are shipped in the SAME per-verse structure, each tagged with Strong's in verse-reading
order.

Matching: source and target word ORDER can differ (Hebrew VSO vs English SVO etc.), so pairing is by
Strong's code, FIFO within each verse — the Nth source occurrence of a given Strong's is matched to
the Nth target span tagged with that same Strong's. A source word whose Strong's has no more
available target spans is checked for ABSORPTION into its immediate predecessor (same convention as
gbt_align.py's many:1 case) before being marked `dropped`.

Classification:
  - 1:1      — source word matched to a target span with real (possibly multi-word) text.
  - elided   — source word matched to a target span with NO independently visible rendering.
               BSB marks this TWO ways, both routed here: (a) the explicit `elided: true` flag on
               an empty-text span (e.g. H853, the Hebrew direct-object marker — no content
               anywhere to merge into); (b) the literal placeholder text "vvv" (verified not a
               data artifact — the only non-English token among the 20 most frequent short
               lowercase target texts corpus-wide, 4,785+ occurrences, always immediately adjacent
               to a real content span) marking a word whose content IS folded into a nearby entry's
               combined rendering (negation particles like μή/οὐ baked into "not killed"/"I do not
               know"). Unlike gbt's ambiguous suffix-addressed nulls, both are a PROFESSIONALLY
               PUBLISHED, finished translation's own explicit convention — not incompleteness.
  - many:1   — a source word's Strong's has no unclaimed target span left, but the immediately
               preceding source word already consumed a real (non-elided) span for the SAME
               Strong's — absorbed into that predecessor's group. NOTE: BSB does not always merge
               construct-state compound names this way (Sela Hammahlekoth's two Hebrew words each
               keep their own Strong's-tagged span in BSB — one happens to be the "vvv" placeholder,
               the other "Sela-hammahlekoth" — rather than one combined span like gbt's "the Rock of
               Escape"); this kind is genuinely rarer here than in gbt's data.
  - dropped  — a source word's Strong's has no target span at all, and no absorbable predecessor.

`verse_ref` matches lexeme_aligner.refs.encode() exactly (book code from the containing directory,
chapter from the filename, verse from the per-chapter dict's own key).

    python3 -m lexeme_aligner.bsb_align
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.refs import BOOK_NUMBERS, encode

_DATA_DIR = Path("data/bsb/display")
_OUT_PATH = Path("resources/occurrence_align/bsb_eng.jsonl")


def extract_verse(source_arr: list, target_arr: list) -> list[dict]:
    """One verse: source_arr = [[text, strong], ...] (BSB's `heb`/`grc`); target_arr = [[text,
    strong], [text, strong, {"elided": true}], [text, None], ...] (BSB's `eng`, includes untagged
    whitespace/punctuation spans with strong=None — skipped here, they carry no occurrence link)."""
    queues: dict[str, collections.deque] = collections.defaultdict(collections.deque)
    t_idx = 0
    for item in target_arr:
        text, strong = item[0], item[1]
        if strong is None:
            t_idx += 1
            continue
        # BSB marks "no independently visible rendering" TWO ways: the explicit `elided:true` flag
        # (empty text — e.g. H853, the Hebrew direct-object marker, has no content ANYWHERE to
        # merge into) and the literal placeholder text "VVV" (content exists but is folded into a
        # NEARBY real entry's combined rendering — e.g. a negation particle μή/οὐ whose semantics
        # are baked into "not killed"/"I do not know" on an adjacent Strong's-tagged span; verified
        # not a data artifact — "vvv" is the only non-English token among the 20 most frequent
        # short lowercase target texts in the whole corpus, 4,785+ occurrences, always immediately
        # adjacent to a real content entry). Both route to the same `elided` classification below.
        elided = (len(item) >= 3 and bool(item[2].get("elided"))) or text.strip().lower() == "vvv"
        queues[strong].append((t_idx, text, elided))
        t_idx += 1

    groups: list[dict] = []
    for h_idx, (h_text, h_strong) in enumerate(source_arr):
        if h_strong and queues.get(h_strong):
            e_idx, e_text, elided = queues[h_strong].popleft()
            groups.append({"source_ids": [h_idx], "source_text": [h_text], "source_strong": [h_strong],
                           "target_ids": [e_idx], "target_gloss": [e_text], "elided": elided})
        else:
            prev = groups[-1] if groups else None
            if (prev and prev["source_strong"][-1] == h_strong and not prev["elided"]
                    and prev["target_gloss"] and prev["target_gloss"][-1]):
                prev["source_ids"].append(h_idx)
                prev["source_text"].append(h_text)
                prev["source_strong"].append(h_strong)
            else:
                groups.append({"source_ids": [h_idx], "source_text": [h_text], "source_strong": [h_strong],
                               "target_ids": [], "target_gloss": [], "elided": False})

    rows = []
    for g in groups:
        kind = ("dropped" if not g["target_ids"]
                else "elided" if g["elided"]
                else "many:1" if len(g["source_ids"]) > 1
                else "1:1")
        rows.append({"source_ids": g["source_ids"], "source_text": g["source_text"],
                     "source_strong": g["source_strong"], "target_ids": g["target_ids"],
                     "target_gloss": g["target_gloss"], "kind": kind})
    return rows


def extract_book(book_code: str, chapters: dict[int, dict]) -> list[dict]:
    rows = []
    for chapter, data in sorted(chapters.items()):
        source_key = "heb" if "heb" in data else "grk"
        source_by_verse = data[source_key]
        target_by_verse = data["eng"]
        for verse_str, source_arr in sorted(source_by_verse.items(), key=lambda kv: int(kv[0])):
            verse = int(verse_str)
            target_arr = target_by_verse.get(verse_str, [])
            for row in extract_verse(source_arr, target_arr):
                row["verse_ref"] = encode(book_code, chapter, verse)
                rows.append(row)
    return rows


def extract_all(data_dir: Path = _DATA_DIR) -> list[dict]:
    if not data_dir.exists():
        raise SystemExit(f"[bsb_align] missing {data_dir} — run `python3 -m lexeme_aligner.bsb_fetch` first "
                          f"(see PROVENANCE.txt — data/bsb/ is out-of-band, gitignored)")
    rows = []
    for book_dir in sorted(data_dir.iterdir()):
        if not book_dir.is_dir():
            continue
        book_code = book_dir.name
        if book_code not in BOOK_NUMBERS:
            print(f"[bsb_align] skipping unknown book dir {book_code}", file=sys.stderr)
            continue
        chapters: dict[int, dict] = {}
        for fp in sorted(book_dir.glob(f"{book_code}*.json")):
            chapter = int(fp.stem[len(book_code):])
            chapters[chapter] = json.loads(fp.read_text(encoding="utf-8"))
        for row in extract_book(book_code, chapters):
            row["lang"] = "eng"
            row["source"] = "bsb"
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=_DATA_DIR)
    ap.add_argument("--out", type=Path, default=_OUT_PATH)
    args = ap.parse_args()

    rows = extract_all(args.data_dir)
    kind_counts = collections.Counter(r["kind"] for r in rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_source_words = sum(len(r["source_ids"]) for r in rows)
    print(f"[bsb_align] {len(rows)} groups, {n_source_words} source words -> {args.out}\n"
          f"  kind breakdown: {dict(kind_counts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
