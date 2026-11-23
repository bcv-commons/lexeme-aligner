"""compact-alignments -> part-alignments at WORD grain: an edition aligned on whole source words, written in the parts format.

Owner 2026-11-23: compact-alignments is frozen and retired; every published output is linked to word parts. An edition that has not
been re-run on the parts route yet (the fleet re-run follows) still gets part-alignments files: each compact link goes onto the
CARRYING unit of its word (the stem, or the word's only unit), nothing onto endings / prefixes. The manifest entry says
`grain: "word"`: these links say which target words render the WHOLE source word, not which part renders what. A re-run on the
parts route replaces such an entry with a part-level one (no `grain`).

Exact by construction: folding the result with fold.py (either rule: only carrying units are linked) gives back the compact files,
byte for byte; every book is checked that way before it is written. One exception, counted (`stale extra`): an extra-layer entry for
a word the main array links. compact's rule is that the extra layer holds only words the main array leaves out; such an entry is left
over from an older run of the extra file (e.g. fra_ost 3JN 1:13), and it is dropped.

    python -m lexeme_aligner.pa_from_compact --compact publish/compact-alignments --dest publish/part-alignments [--iso fra] [--edition fra_ost]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lexeme_aligner import pa_fold

DENSE = pa_fold.DENSE
SPARSE = ("contested", "bonus", "rule", "parts")
GRAIN = "word"


def _remap_sparse(s: str, carrier: list[int]) -> str:
    out = []
    for e in s.split():
        o, _, rest = e.partition(":")
        out.append(f"{carrier[int(o)]}:{rest}")
    return " ".join(out)


def clean_extra(main: str, extra: str) -> tuple[str, int]:
    """compact's extra entry without the entries for words main links (stale, see the module docstring); (entry, n dropped)."""
    linked = {e.partition(":")[0] for e in main.split()}
    keep = [e for e in extra.split() if e.partition(":")[0] not in linked]
    return " ".join(keep), len(extra.split()) - len(keep)


def convert_verse(verse: dict, main: str, meta: dict[str, str], extra: str) -> tuple[str, dict[str, str], str]:
    """One verse: compact (content-token ordinals) -> part-alignments (unit ordinals, links on carrying units). `extra` clean."""
    carrier = [t[3] for t in verse["tokens"] if t[2]]           # compact srcOrd i -> the unit carrying the i-th content token
    pm = _remap_sparse(main, carrier)
    pmeta = {c: (v if c in DENSE else _remap_sparse(v, carrier)) for c, v in meta.items()}
    return pm, pmeta, _remap_sparse(extra, carrier)


def _load(fp: Path):
    return json.loads(fp.read_text(encoding="utf-8"))


def _book_index(dest: Path, book: str, heb_parts) -> dict:
    """The part-alignments index of a book: the existing `_index/<BOOK>_parts.json`, else built from the parts spine."""
    ifp = dest / "_index" / f"{book}_parts.json"
    if ifp.exists():
        return _load(ifp)
    from lexeme_aligner.part_alignments import book_index, part_ids
    index = book_index(heb_parts, book, part_ids(heb_parts.spine_db, book))
    ifp.parent.mkdir(parents=True, exist_ok=True)
    ifp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return index


def convert_edition(compact: Path, dest: Path, iso: str, edition: str, heb_parts, counts: dict | None = None) -> list[str]:
    """Every book of one compact edition folder -> the same relative folder under `dest`. Returns the books written.
    `counts` gets "stale extra" (entries dropped, see clean_extra)."""
    counts = counts if counts is not None else {}
    src = compact / iso[0] / iso / edition
    out = dest / iso[0] / iso / edition
    done = []
    for fp in sorted(src.glob("*.json")):
        name = fp.name
        if name.endswith((".meta.json", ".extra.json")) or name.startswith("_"):
            continue
        book = name.split("_", 1)[0]
        index = _book_index(dest, book, heb_parts)
        lex = _load(compact / "_index" / f"{book}_lexemes.json")
        if pa_fold.compact_index(index)["lexemes"] != lex:
            raise SystemExit(f"pa_from_compact: {book}: compact's _index and part-alignments' index disagree (rebuild the index)")
        stem = name[:-len(".json")]
        main = _load(fp)
        meta = _load(src / f"{stem}.meta.json") if (src / f"{stem}.meta.json").exists() else {}
        extra = _load(src / f"{stem}.extra.json") if (src / f"{stem}.extra.json").exists() else None
        if extra is not None:
            cleaned = [clean_extra(m, x) for m, x in zip(main, extra)]
            extra = [x for x, _ in cleaned]
            counts["stale extra"] = counts.get("stale extra", 0) + sum(k for _, k in cleaned)
        refs = list(index)
        pm, pmeta, px = [], {c: [] for c in meta}, []
        for i, ref in enumerate(refs):
            m, md, x = convert_verse(index[ref], main[i], {c: v[i] for c, v in meta.items()}, extra[i] if extra else "")
            pm.append(m)
            px.append(x)
            for c in pmeta:
                pmeta[c].append(md[c])
        back = pa_fold.fold_book(index, pm, pmeta, px if extra else None)
        if back[0] != main or {c: v for c, v in back[1].items()} != meta or (extra is not None and back[2] != extra):
            raise SystemExit(f"pa_from_compact: {iso}/{edition}/{name}: the fold of the result is not the compact file")
        out.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(json.dumps(pm, ensure_ascii=False) + "\n", encoding="utf-8")
        if meta:
            (out / f"{stem}.meta.json").write_text(json.dumps(pmeta, ensure_ascii=False) + "\n", encoding="utf-8")
        if extra is not None and any(px):
            (out / f"{stem}.extra.json").write_text(json.dumps(px, ensure_ascii=False) + "\n", encoding="utf-8")
        done.append(book)
    if (src / "_fingerprints.txt").exists() and done:
        (out / "_fingerprints.txt").write_bytes((src / "_fingerprints.txt").read_bytes())
    return done


def main(argv=None) -> int:
    from lexeme_aligner.config import PARTS_SPINE_DB
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.part_alignments import update_manifest
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compact", type=Path, required=True)
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--iso", action="append", help="only these languages (default: every one in compact's manifest)")
    ap.add_argument("--edition", action="append", help="only these edition ids")
    ap.add_argument("--parts-spine", type=Path, default=PARTS_SPINE_DB)
    ap.add_argument("--replace-parts", action="store_true",
                    help="also overwrite editions part-alignments already holds at part grain (default: leave them)")
    a = ap.parse_args(argv)
    man = _load(a.compact / "manifest.json")
    have = _load(a.dest / "manifest.json")["languages"] if (a.dest / "manifest.json").exists() else {}
    heb = HebrewSource(spine_db=a.parts_spine)
    heb.spine_db = a.parts_spine
    n = {"editions": 0, "books": 0, "skipped (part grain already)": 0, "skipped (no books)": 0, "stale extra": 0}
    for iso in sorted(a.iso or man["languages"]):
        for ed, entry in sorted(man["languages"][iso]["editions"].items()):
            if a.edition and ed not in a.edition:
                continue
            old = have.get(iso, {}).get("editions", {}).get(ed)
            if old and old.get("grain") != GRAIN and not a.replace_parts:
                n["skipped (part grain already)"] += 1
                continue
            books = convert_edition(a.compact, a.dest, iso, ed, heb, n)
            if not books:
                n["skipped (no books)"] += 1
                continue
            keep = {k: v for k, v in entry.items() if k not in ("derived_from", "fold_rule", "books")}
            update_manifest(a.dest, iso, ed, {**keep, "books": books, "grain": GRAIN, "from": "compact-alignments"}, a.parts_spine)
            n["editions"] += 1
            n["books"] += len(books)
            print(f"[pa_from_compact] {iso}/{ed}: {len(books)} books", file=sys.stderr, flush=True)
    print(json.dumps(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
