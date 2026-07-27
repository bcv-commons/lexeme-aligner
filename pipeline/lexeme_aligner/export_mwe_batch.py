"""aligned_mwe walkthrough — runs `export_mwe.py` for every currently-published language's PRIMARY
edition tag (this dataset's schema has no per-edition column, unlike compact-alignments — one MWE
partition per LANGUAGE, matching lexeme-alignments' pooling). Reuses `gapfill_batch.discover_tags()`/
`has_eflomal()` so this can never drift out of sync with how a language was actually onboarded — same
discipline as `compact_align_batch.py`.

Only needs whatever alignment jsonl is ALREADY in out/ (method="all" unions eflomal/gloss/gapfill,
whichever are present) — no re-ingest, no re-align. Useful as a fast first pass to unlock `out/`
cleanup for a language before its gloss/gapfill steps have even run.

    python3 -m lexeme_aligner.export_mwe_batch --dry-run
    python3 -m lexeme_aligner.export_mwe_batch
    python3 -m lexeme_aligner.export_mwe_batch --iso ind,por
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT
from lexeme_aligner.gapfill_batch import discover_tags, has_eflomal


def _run(cmd: list, label: str) -> bool:
    print(f"  ▶ {label}", file=sys.stderr)
    result = subprocess.run(cmd)
    return result.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-separated isos to restrict to (default: every published language)")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, don't run anything")
    ap.add_argument("--manifest", type=Path, default=LEX_ROOT / "manifest.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--root", type=Path, default=Path("publish/aligned_mwe"))
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    all_isos = manifest["languages"]
    isos = [i.strip() for i in args.iso.split(",")] if args.iso else sorted(all_isos)

    print(f"[export_mwe_batch] {len(isos)} language(s) to check", file=sys.stderr)

    plan: dict[str, str] = {}   # iso -> primary tag
    for iso in isos:
        tags = discover_tags(iso)
        if not tags:
            continue
        primary = tags[0][0]
        if has_eflomal(primary, args.out):
            plan[iso] = primary

    print(f"[export_mwe_batch] {len(plan)} language(s) with alignment data to process", file=sys.stderr)

    if args.dry_run:
        for iso, tag in plan.items():
            print(f"  {iso:<8} {tag}", file=sys.stderr)
        return 0

    failed = []
    for iso, tag in plan.items():
        lang_name = all_isos.get(iso, {}).get("language")
        cmd = [sys.executable, "-m", "lexeme_aligner.export_mwe", "--iso", tag, "--publish-iso", iso,
              "--method", "all", "--out", str(args.out), "--root", str(args.root)]
        if lang_name:
            cmd += ["--lang-name", lang_name]
        if not _run(cmd, f"{iso}/{tag}"):
            failed.append(iso)

    print(f"\n[export_mwe_batch] done — {len(plan) - len(failed)}/{len(plan)} language(s) exported "
          f"locally under {args.root}.", file=sys.stderr)
    if failed:
        print(f"[export_mwe_batch] FAILED: {failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
