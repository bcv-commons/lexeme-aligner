"""Pairing audit: how the verse remap lands on one edition's OWN text.

For every spine verse of the chosen books the chain pairs the edition verse `remapper(...)` names. This tool checks that
mapping against the edition's real verse structure and reports, per book:

  * `no_text`   — spine verses whose mapped verse does NOT exist in the edition (the verse gets no target text). Superscriptions
                  an English-numbered edition leaves unnumbered (mapped to verse 0) are counted separately as `title`.
  * `untargeted` — edition verses no spine verse maps to (their text is never aligned). A one-to-two relation (Hebrew PSA 13:6 =
                  English 13:5-6, ISA 63:19) leaves exactly one such verse; anything more is a remap error.

A healthy edition has `no_text == 0` outside the verses the edition really lacks, and only the known one-to-two cases untargeted.
Written 2026-10-06 to check bcv-commons/bibles' rebuilt Synodal table; usable for any edition (`--tag`), any scheme.

    .venv/bin/python pipeline/scripts/tools/pairing_audit.py --tag rus_syn
    .venv/bin/python pipeline/scripts/tools/pairing_audit.py --tag bsb --books PSA JOB
    .venv/bin/python pipeline/scripts/tools/pairing_audit.py --tag rus_syn --table /path/rso-to-eng.json   # a CANDIDATE table
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))

from lexeme_aligner.hebrew_source import HebrewSource  # noqa: E402
from lexeme_aligner.run_pilot import OT_BOOKS, _BOOK_FILE_NUM, _range_coverage  # noqa: E402
from lexeme_aligner.usj_source import read_verse_ranges  # noqa: E402
from lexeme_aligner.versification import edition_scheme, remapper  # noqa: E402


def install_table(table: Path, label: str, multiverse: Path | None = None) -> Path:
    """Audit with a CANDIDATE scheme table instead of the vendored one: bcv-commons/bibles-style JSON ({"map": [{"s","t"}, ...]})
    or our TSV. Copies the vendored tables to a temp dir, replaces the one `label` uses, and points versification at it."""
    import shutil
    import tempfile

    import lexeme_aligner.versification as vf
    fname = vf._SCHEME_FILE.get(label)
    if not fname:
        raise SystemExit(f"scheme label {label!r} has no table file to replace")
    tmp = Path(tempfile.mkdtemp(prefix="pairing_audit_"))
    for f in vf._REG_DIR.glob("*.tsv"):
        shutil.copy(f, tmp / f.name)
    if table.suffix == ".json":
        import json
        rows = json.loads(table.read_text(encoding="utf-8"))["map"]
        (tmp / f"{fname}.tsv").write_text("# candidate\nsource_ref\tstandard_ref\taction\n" + "".join(
            f"{r['s']}\t{r['t']}\t{r.get('a', 'Renumber verse')}\n" for r in rows), encoding="utf-8")
    else:
        shutil.copy(table, tmp / f"{fname}.tsv")
    if multiverse:
        shutil.copy(multiverse, tmp / f"{fname}.multiverse.json")
    vf._REG_DIR = tmp
    return tmp


def audit_book(book: str, usj_dir: Path, heb: HebrewSource, remap) -> dict | None:
    fp = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    if not fp.exists():
        return None
    ranges = read_verse_ranges(fp, rules={})
    coverage = _range_coverage(ranges)
    anchors = {(c, coverage.get((c, v), (v, v))[0]) for (c, v) in ranges}
    targeted: set[tuple[int, int]] = set()
    no_text, title = [], []
    for ch in heb.chapters(book):
        for v in heb.verses(book, ch):
            _, tc, tv = remap(book, ch, v) if remap else (book, ch, v)
            if tv == 0:
                title.append(f"{book} {ch}:{v}")
            elif (tc, tv) in coverage or (tc, tv) in ranges:
                targeted.add((tc, coverage.get((tc, tv), (tv, tv))[0]))
            else:
                no_text.append((f"{book} {ch}:{v}", f"{tc}:{tv}"))
    untargeted = sorted(anchors - targeted)
    return {"no_text": no_text, "title": title, "untargeted": untargeted}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="edition ingest tag (pipeline/work/ingest-cache/usj-<tag>)")
    ap.add_argument("--books", nargs="*", default=OT_BOOKS)
    ap.add_argument("--examples", type=int, default=4)
    ap.add_argument("--multiverse", type=Path, default=None,
                    help="with --table: the scheme's multi-verse relations file (bcv-commons/bibles <scheme>-to-eng.multiverse.json)")
    ap.add_argument("--table", type=Path, default=None,
                    help="audit with this candidate scheme table (bibles JSON or TSV) instead of the vendored one")
    a = ap.parse_args(argv)
    usj = REPO / "pipeline/work/ingest-cache" / f"usj-{a.tag}"
    heb = HebrewSource()
    if a.table:
        from lexeme_aligner.versification import scheme_of
        install_table(a.table, scheme_of(a.tag, str(usj)), a.multiverse)
    remap = remapper(a.tag, str(usj))
    print(f"[pairing_audit] {a.tag}: scheme {edition_scheme(a.tag, str(usj))}, remap {'yes' if remap else 'none (identity)'}")
    tot = {"no_text": 0, "title": 0, "untargeted": 0}
    for book in a.books:
        r = audit_book(book, usj, heb, remap)
        if r is None:
            continue
        for k in tot:
            tot[k] += len(r[k])
        if r["no_text"] or r["untargeted"]:
            ex_n = [f"{s}->{t}" for s, t in r["no_text"][:a.examples]]
            ex_u = [f"{c}:{v}" for c, v in r["untargeted"][:a.examples]]
            print(f"  {book}: no_text {len(r['no_text'])} {ex_n} | untargeted {len(r['untargeted'])} {ex_u}")
    print(f"[pairing_audit] totals: no_text {tot['no_text']} | untargeted {tot['untargeted']} | title (unnumbered in this edition) {tot['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
