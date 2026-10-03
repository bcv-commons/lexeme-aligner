"""Re-gating: which languages must be re-run after the grammar facts changed (2026-10-02).

`span_extension` (and the gap-fill / fertility gates) read the merged per-language grammar verdicts in `config/gram_struct/<iso>.json`. When a rebuild
changes a verdict that one of those mechanisms consumes, every language whose verdict changed has to be re-run to pick it up. Until now that diff
lived inside a one-off script; this is the reusable version.

    lexeme-aligner grammar regate --snapshot                    # BEFORE a rebuild: copy config/gram_struct (minus derived_input) to pipeline/work/backup-gram-struct-<ts>
    lexeme-aligner grammar all                                  # rebuild the grammar facts
    lexeme-aligner grammar regate --before <snapshot dir>       # AFTER: write the changed languages to pipeline/work/logs/regate_list.json
    lexeme-aligner batch --list pipeline/work/logs/regate_list.json --skip-ingest --workers 2 --nice 10

A slot counts as changed when its value differs, including None <-> a direction (a language that gained or lost a verdict). Nothing is run here.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GRAM_STRUCT = REPO / "config" / "gram_struct"
DEFAULT_OUT = REPO / "pipeline/work/logs/regate_list.json"
# the merged keys a downstream mechanism actually reads (span_extension / gapfill / fertility gates)
CONSUMED = ("adposition", "possessor", "article", "article_bound", "subject_verb", "object_verb")


def directions(gs_dir: Path, slots: tuple[str, ...] = CONSUMED) -> dict[str, dict[str, object]]:
    """{iso: {slot: direction-or-bound}} from the merged per-language files (partition sub-folders and `_*`/article_bound files are not languages)."""
    out: dict[str, dict[str, object]] = {}
    for fp in Path(gs_dir).glob("*.json"):
        if fp.name.startswith("_") or fp.stem == "article_bound":
            continue
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out[fp.stem] = {k: (v.get("direction", v.get("bound")) if isinstance(v, dict) else v) for k, v in doc.items() if k in slots}
    return out


def changed(before: dict[str, dict], after: dict[str, dict], slots: tuple[str, ...] = CONSUMED) -> dict[str, dict[str, list]]:
    """{iso: {slot: [old, new]}} for every language in `after` whose value for a consumed slot differs from `before`."""
    diff: dict[str, dict[str, list]] = {}
    for iso, now in after.items():
        was = before.get(iso, {})
        d = {k: [was.get(k), now.get(k)] for k in slots if was.get(k) != now.get(k)}
        if d:
            diff[iso] = d
    return diff


def snapshot(gs_dir: Path = GRAM_STRUCT, dest_root: Path = REPO / "pipeline/work") -> Path:
    dest = Path(dest_root) / f"backup-gram-struct-{time.strftime('%Y%m%d%H%M')}"
    shutil.copytree(gs_dir, dest, ignore=shutil.ignore_patterns("derived_input"))
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", action="store_true", help="copy the current grammar facts aside (run BEFORE a rebuild) and print the folder")
    ap.add_argument("--before", type=Path, help="a snapshot folder: diff it against the current config/gram_struct")
    ap.add_argument("--after", type=Path, default=GRAM_STRUCT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if a.snapshot:
        print(snapshot(a.after))
        return 0
    if not a.before:
        ap.error("pass --snapshot (before a rebuild) or --before <snapshot dir> (after it)")
    diff = changed(directions(a.before), directions(a.after))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(sorted(diff)), encoding="utf-8")
    a.out.with_suffix(".detail.json").write_text(json.dumps(diff, indent=1, sort_keys=True), encoding="utf-8")
    per_slot: dict[str, int] = {}
    for d in diff.values():
        for k in d:
            per_slot[k] = per_slot.get(k, 0) + 1
    print(f"[regate] {len(diff)} language(s) changed a consumed slot {per_slot} -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
