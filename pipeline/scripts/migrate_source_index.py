"""Bring compact-alignments files built on the OLD spine onto the CURRENT spine's srcOrd numbering.

Situation (2026-09-29, see lexeme_aligner/source_index.py): the published `_index/<BOOK>_lexemes.json` was
derived from a spine that gained ONE content token in each of ten verses on 2026-09-23. Alignment files
written before that date count srcOrd on the old lists; files written after it count on the new ones. After
`source_index --refresh` the index describes the NEW numbering, so every OLD file's entries in those ten
verses must move up by one after the insertion point (the inserted token is simply unaligned there).

Everything this needs is derived from the old published index vs the current spine — it must run BEFORE
`--refresh` (afterwards there is no difference left to derive, so a re-run is a no-op). A file is migrated
only if ALL hold: (1) its mtime is older than the spine file's (it was built on the old spine), (2) none of
its entries in an affected verse carries an ordinal that only the NEW numbering allows, (3) every ordinal-
bearing field parses. Files this script writes get a new mtime, so a second run skips them. Originals go to
--backup-root before anything is replaced; a file modified while the script runs (the batch writing) is
skipped, not overwritten.

Fields shifted: the main array and `.extra.json` (srcOrd:span), and in `.meta.json` the SPARSE `contested`,
`bonus` and `rule` arrays. `method`/`conf` are dense per aligned token and never carry a srcOrd.

    python3 pipeline/scripts/migrate_source_index.py            # dry run
    python3 pipeline/scripts/migrate_source_index.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lexeme_aligner import source_index as si  # noqa: E402
from lexeme_aligner.config import SPINE_DB  # noqa: E402

META_ORDINAL_FIELDS = ("contested", "bonus", "rule")


def plan_inserts(heb, index_root: Path) -> dict[str, dict]:
    """{book: {'keys': [verse refs], 'inserts': {verse index: (position, old_len)}}} for every book whose
    published index differs from the spine. Aborts on any difference that is not a single inserted lexeme."""
    from lexeme_aligner.compact_align import ALL_BOOKS
    plan = {}
    for book in ALL_BOOKS:
        fp = index_root / f"{book}_lexemes.json"
        published = json.loads(fp.read_text(encoding="utf-8"))
        cmp = si.compare_book(si._lexemes(heb, book), published)
        if not cmp["keys_equal"]:
            raise SystemExit(f"{book}: verse list differs — not a single-insert case, refusing")
        if not cmp["differing"]:
            continue
        keys = list(published)
        bad = [r for r in cmp["differing"] if r not in cmp["inserts"]]
        if bad:
            raise SystemExit(f"{book}: {bad[:3]} changed by more than one inserted lexeme — refusing")
        plan[book] = {"keys": keys, "inserts": {keys.index(r): (pos, len(published[r]))
                                                for r, (pos, _lx) in cmp["inserts"].items()}}
    return plan


def _load(fp: Path):
    return json.loads(fp.read_text(encoding="utf-8"))


def _dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def migrate_group(main_fp: Path, inserts: dict[int, tuple[int, int]], spine_mtime: float
                  ) -> tuple[str, dict[Path, bytes]]:
    """(status, {path: new bytes}) for one main file and its sidecars. status is 'migrate', or the reason
    it was skipped: 'newer-than-spine', 'new-numbering-evidence', 'unparseable', 'short'."""
    if main_fp.stat().st_mtime >= spine_mtime:
        return "newer-than-spine", {}
    main = _load(main_fp)
    stem = main_fp.name[:-len(".json")]
    extra_fp, meta_fp = main_fp.with_name(stem + ".extra.json"), main_fp.with_name(stem + ".meta.json")
    extra = _load(extra_fp) if extra_fp.exists() else None
    meta = _load(meta_fp) if meta_fp.exists() else None
    if max(inserts) >= len(main):
        return "short", {}
    try:
        for i, (pos, old_len) in inserts.items():
            for arr in (main, extra):
                if arr and arr[i] and max(si.entry_ordinals(arr[i])) >= old_len:
                    return "new-numbering-evidence", {}
        new_main, new_extra = list(main), (list(extra) if extra is not None else None)
        new_meta = json.loads(json.dumps(meta)) if meta is not None else None
        for i, (pos, _old_len) in inserts.items():
            new_main[i] = si.shift_entry(main[i], pos) if main[i] else main[i]
            if new_extra is not None and new_extra[i]:
                new_extra[i] = si.shift_entry(new_extra[i], pos)
            if new_meta is not None:
                for field in META_ORDINAL_FIELDS:
                    col = new_meta.get(field)
                    if col and i < len(col) and col[i]:
                        col[i] = si.shift_entry(col[i], pos)
    except ValueError:
        return "unparseable", {}
    out = {main_fp: _dump(new_main)}
    if new_extra is not None:
        out[extra_fp] = _dump(new_extra)
    if new_meta is not None:
        out[meta_fp] = _dump(new_meta)
    return "migrate", out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--publish-root", type=Path, default=Path("publish/compact-alignments"))
    ap.add_argument("--spine-db", type=Path, default=SPINE_DB)
    ap.add_argument("--backup-root", type=Path, default=Path("pipeline/work/index-migration-backup"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = HebrewSource()
    index_root = a.publish_root / "_index"
    plan = plan_inserts(heb, index_root)
    if not plan:
        print("[migrate] the published index already matches the spine — nothing to do", file=sys.stderr)
        return 0
    spine_mtime = a.spine_db.stat().st_mtime
    print(f"[migrate] {sum(len(p['inserts']) for p in plan.values())} verse(s) in {len(plan)} book(s) "
          f"gained a token; spine built {spine_mtime:.0f}", file=sys.stderr)
    tally: collections.Counter = collections.Counter()
    for book, p in sorted(plan.items()):
        for main_fp in sorted(a.publish_root.glob(f"*/*/*/{book}_*.json")):
            if main_fp.name.endswith((".meta.json", ".extra.json")):
                continue
            status, writes = migrate_group(main_fp, p["inserts"], spine_mtime)
            tally[status] += 1
            if status != "migrate" or not a.apply:
                continue
            mt_before = {fp: fp.stat().st_mtime_ns for fp in writes}
            for fp, data in writes.items():
                if fp.stat().st_mtime_ns != mt_before[fp]:
                    tally["raced"] += 1
                    break
            else:
                for fp, data in writes.items():
                    bak = a.backup_root / fp.relative_to(a.publish_root)
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    if not bak.exists():
                        shutil.copy2(fp, bak)
                for fp, data in writes.items():
                    if fp.stat().st_mtime_ns != mt_before[fp]:      # re-check right before replacing
                        tally["raced"] += 1
                        break
                    tmp = fp.with_name(f".{fp.name}.tmp{os.getpid()}")
                    tmp.write_bytes(data)
                    os.replace(tmp, fp)
                else:
                    tally["written_groups"] += 1
    for k, v in sorted(tally.items()):
        print(f"[migrate] {k}: {v}", file=sys.stderr)
    if not a.apply:
        print("[migrate] dry run — nothing written; pass --apply", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
