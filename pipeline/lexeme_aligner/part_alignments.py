"""bcv-commons/part-alignments (format part-alignments/0.1): alignments keyed by word-parts ids, from a run on the PARTS spine.

Format: internal-docs/part-alignments-format-draft-2026-11-22.md (owner 2026-11-23: Q1-Q4 as proposed).

    _index/<BOOK>_parts.json                {"BOOK C:V": ["<part id>[+<part id>]", ...]}   every source unit, text order; once per book
    _index/_source.json                     format, the word-parts pin, the parts spine's content pin
    <i>/<iso>/<edition>/<BOOK>_<fp8>.json   ["srcOrd:span ...", ...]  one string per index verse, in index order
    <i>/<iso>/<edition>/<BOOK>_<fp8>.meta.json   compact's channels (method, conf, contested, rule; parts = target parts)
    <i>/<iso>/<edition>+llm+<name>/...      the same for an LLM (Jev) layer; `srcOrd:_` = checked, no rendering (Jev: none)
    manifest.json

`srcOrd` = 0-based position in the verse's list in `_index/<BOOK>_parts.json`, content and function units alike (compact counts content
words only). Span grammar, file names (`fp8` = the book's text fingerprint) and resolution rules are compact's: the main array is
compact_align.build_compact with every unit given an ordinal. A part id is the word-parts id (word id / MACULA id + part number); units
the spine merges (fused names, adjacent same-Strong's words) are their ids joined with `+`.

Run with ALIGNER_SPINE_DB = the parts spine (source_parts.py) on the align files of that run (not the word view in OUT/words).

    python -m lexeme_aligner.part_alignments --iso TAG --publish-iso ISO --usj-dir DIR --out-dir OUT --dest DIR [--book B ...]
        [--edition ID] [--llm-cell 'jevC.TAG.{BOOK}.v4.A.jev-0' --llm-name jev-v4]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

FORMAT = "part-alignments/0.1"
CHANNELS = ("method", "conf", "contested", "rule", "parts")
NONE = "_"


def part_ids(spine_db: Path, book: str) -> dict[str, str]:
    """{spine key: word-parts id} for one book."""
    con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
    try:
        return {k: p for k, p in con.execute("SELECT key, part_id FROM spine_words WHERE book = ?", (book,))}
    finally:
        con.close()


def book_index(heb, book: str, ids: dict[str, str]) -> dict[str, list[str]]:
    """{"BOOK C:V": [unit id, ...]}: every unit of each raw spine verse, in source order."""
    out = {}
    for ch in heb.chapters(book):
        for v in heb.verses(book, ch):
            out[f"{book} {ch}:{v}"] = ["+".join(ids.get(k, "") for k in (t.keys or [])) for t in heb.verse_tokens(book, ch, v)]
    return out


def none_marks(cell: str, book: str, out_dir: Path, heb, usj_dir: Path, remap) -> dict[str, list[int]]:
    """{"BOOK C:V" anchor: [srcOrd, ...]} of units Jev checked and found no rendering for (`llm_skipped` status `unrepresented`)."""
    from lexeme_aligner.align_files import tag_files
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, pooled_verse_groups
    from lexeme_aligner.usj_source import read_verse_ranges
    skipped: dict[tuple[int, int], set[int]] = {}
    for fp in tag_files(out_dir, "llm", cell):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    linked = {p["h_idx"] for p in r.get("pairs", ()) if p.get("t_idx")}
                    skipped[(r["chapter"], r["verse"])] = {s["h_idx"] for s in r.get("llm_skipped", ())
                                                          if s.get("status") == "unrepresented" and s.get("h_idx") not in linked}
    usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    ranges = read_verse_ranges(usj_path) if usj_path.exists() else {}
    out = {}
    for ch in heb.chapters(book):
        for anchor_v, _vs, _ve, _text, members in pooled_verse_groups(book, ch, heb, ranges, remap):
            sk = skipped.get((ch, anchor_v))
            if sk:
                units = [tok for orig_v, tok in members if orig_v == anchor_v]
                out[f"{book} {ch}:{anchor_v}"] = [o for o, tok in enumerate(units) if tok.idx in sk]
    return out


def _with_none(entry: str, ords: list[int]) -> str:
    have = {p.split(":", 1)[0] for p in entry.split()}
    parts = entry.split() + [f"{o}:{NONE}" for o in ords if str(o) not in have]
    return " ".join(sorted(parts, key=lambda p: int(p.split(":", 1)[0])))


def write_edition(tag: str, iso: str, usj_dir: Path, out_dir: Path, dest: Path, books: list[str], edition: str, methods,
                  spine_db: Path, heb=None, contest: dict | None = None, llm_cell: str | None = None) -> dict:
    """One edition folder (or LLM layer folder) under `dest`, plus the shared `_index` files it needs. Returns {book: file name}."""
    from lexeme_aligner import text_fingerprint as tf
    from lexeme_aligner.compact_align import build_compact
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    from lexeme_aligner.versification import remapper
    heb = heb or HebrewSource(spine_db=spine_db)
    remap = remapper(tag, str(usj_dir))
    by_ref, side = build_compact(tag, usj_dir, heb, out_dir, books, methods, contest, cross_edition_iso=iso, all_units=True)
    idx_dir = dest / "_index"
    idx_dir.mkdir(parents=True, exist_ok=True)
    edir = dest / iso[0] / iso / edition
    written = {}
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not usj_path.exists():
            continue
        index = book_index(heb, book, part_ids(spine_db, book))
        ifp = idx_dir / f"{book}_parts.json"
        if ifp.exists():
            if json.loads(ifp.read_text(encoding="utf-8")) != index:
                raise SystemExit(f"part_alignments: {ifp} was built from another parts spine; rebuild the index (remove it) first")
        else:
            ifp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        refs = list(index)
        marks = none_marks(llm_cell.format(BOOK=book), book, out_dir, heb, usj_dir, remap) if llm_cell else {}
        array = [_with_none(by_ref.get(r, ""), marks.get(r, [])) if marks.get(r) else by_ref.get(r, "") for r in refs]
        if not any(array):
            continue
        digest = tf.file_hash(tf.usj_fingerprints(usj_path)[0])
        edir.mkdir(parents=True, exist_ok=True)
        name = f"{book}_{digest}.json"
        (edir / name).write_text(json.dumps(array, ensure_ascii=False) + "\n", encoding="utf-8")
        meta = {c: [side[c].get(r, "") for r in refs] for c in CHANNELS}
        meta = {c: v for c, v in meta.items() if any(v)}
        if meta:
            (edir / f"{book}_{digest}.meta.json").write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
        written[book] = name
    return written


def source_record(spine_db: Path) -> dict:
    con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM spine_meta"))
    finally:
        con.close()
    return {"format": FORMAT, "word_parts": json.loads(meta.get("source_parts", "null")),
            "parts_spine": {"base": meta.get("source_parts_base"), "base_content_sha256": meta.get("source_parts_base_content_sha256"),
                            "units": int(meta["words"]) if meta.get("words") else None}}


def update_manifest(dest: Path, iso: str, edition: str, entry: dict, spine_db: Path) -> None:
    from lexeme_aligner.manifest_io import update_json
    from lexeme_aligner.usj_source import TOKENIZER_VERSION
    (dest / "_index").mkdir(parents=True, exist_ok=True)
    (dest / "_index" / "_source.json").write_text(json.dumps(source_record(spine_db), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def mutate(doc):
        doc["format"] = FORMAT
        doc["layout"] = "<iso[0]>/<iso>/<edition>[+llm+<name>]/<BOOK>_<fp8>.json (+ .meta.json); _index/<BOOK>_parts.json"
        doc.setdefault("languages", {}).setdefault(iso, {"editions": {}}).setdefault("editions", {})[edition] = {
            **entry, "tokenizer_version": TOKENIZER_VERSION}
    update_json(dest / "manifest.json", mutate)


def main(argv=None) -> int:
    from lexeme_aligner.compact_align import ALL_BOOKS, METHODS, load_contest_rule
    from lexeme_aligner.config import SPINE_DB
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="the chain's edition tag (names the align files)")
    ap.add_argument("--publish-iso", required=True)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True, help="the parts run's align files")
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--edition", default=None, help="edition id (default: the tag)")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default="spanext," + ",".join(METHODS))
    ap.add_argument("--llm-cell", default=None, help="write the LLM layer from these cells instead (may hold {BOOK})")
    ap.add_argument("--llm-name", default="jev-v4")
    a = ap.parse_args(argv)
    books = [b.upper() for b in a.book] if a.book else [b for b in ALL_BOOKS if any(a.usj_dir.glob(f"*-{b}.json"))]
    edition = a.edition or a.iso
    if a.llm_cell:
        lid = f"{edition}+llm+{a.llm_name}"
        files = {}
        for b in books:                                   # one cell per book
            files.update(write_edition(a.iso, a.publish_iso, a.usj_dir, a.out_dir, a.dest, [b], lid, [f"llm:{a.llm_cell.format(BOOK=b)}"],
                                       SPINE_DB, llm_cell=a.llm_cell))
        entry = {"tag": a.iso, "source": "llm", "name": a.llm_name, "cell": a.llm_cell, "books": files}
        update_manifest(a.dest, a.publish_iso, lid, entry, SPINE_DB)
    else:
        methods = [m for m in a.methods.split(",") if m]
        files = write_edition(a.iso, a.publish_iso, a.usj_dir, a.out_dir, a.dest, books, edition, methods, SPINE_DB,
                              contest=load_contest_rule())
        update_manifest(a.dest, a.publish_iso, edition, {"tag": a.iso, "methods": methods, "books": files}, SPINE_DB)
    print(json.dumps({"edition": edition, "books": len(files)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
