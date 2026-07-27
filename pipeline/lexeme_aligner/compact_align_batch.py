"""Compact-alignments walkthrough — runs `compact_align.py --publish` for EVERY edition (not just the
primary) of every currently-published language, skipping any tag with no alignment data at all. Same
discipline as `gapfill_batch.py`/`onboard_batch.py`: reuses `onboard.discover_tags()`/`USJ_DIR_OVERRIDES`
so this can never drift out of sync with how a language was actually onboarded.

Unlike `gapfill_batch.py` (one export per LANGUAGE, pooling editions), compact-alignments publishes one
tree per EDITION — `compact_align.py`'s own manifest merge (`update_manifest`) already handles multiple
editions per language, so this driver just needs to call it once per data-bearing tag.

    python3 -m lexeme_aligner.compact_align_batch --dry-run
    python3 -m lexeme_aligner.compact_align_batch
    python3 -m lexeme_aligner.compact_align_batch --iso ind,por

`--publish-hf <repo>` pushes the LOCAL tree to HF afterward (or standalone, with --local-only skipped) —
a SEPARATE, deliberate step, same as every other dataset here. Uses `hf_bulk_publish.publish_chunked`
(shared with any other dataset here that needs a many-file bulk push, not just export_lex.py's smaller
one-language-at-a-time case).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.gapfill_batch import USJ_DIR_OVERRIDES, discover_tags, has_eflomal
from lexeme_aligner.hf_bulk_publish import publish_chunked


def _run(cmd: list, label: str) -> bool:
    print(f"  ▶ {label}", file=sys.stderr)
    result = subprocess.run(cmd)
    return result.returncode == 0


def publish_to_hf(root: Path, repo_id: str, create: bool, dry_run: bool, chunk_size: int = 500) -> None:
    """Every publishable file under `root` (README.md, manifest.json, _index/*.json, and every
    <iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json — everything except the local `.publish_state.json`
    cache itself)."""
    all_files = sorted(
        str(fp.relative_to(root)) for fp in root.rglob("*.json")
    ) + (["README.md"] if (root / "README.md").exists() else [])
    publish_chunked(root, repo_id, all_files, create, dry_run, chunk_size, label="compact-alignments")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-separated isos to restrict to (default: every published language)")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, don't run anything")
    ap.add_argument("--manifest", type=Path, default=LEX_ROOT / "manifest.json")
    ap.add_argument("--usj-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--publish", type=Path, default=Path("publish/compact-alignments"))
    ap.add_argument("--methods", default="eflomal,gloss,gapfill")
    ap.add_argument("--local-only", action="store_true",
                    help="skip the HF push even if --publish-hf is given (generate locally only)")
    ap.add_argument("--skip-generate", action="store_true",
                    help="skip local (re-)generation, only push what's already under --publish to HF")
    ap.add_argument("--publish-hf", default=None,
                    help="HF dataset repo to push the local tree to, e.g. bcv-commons/compact-alignments "
                         "— a separate, deliberate step; omit to generate locally only")
    ap.add_argument("--create", action="store_true", help="create the HF dataset repo if missing")
    ap.add_argument("--chunk-size", type=int, default=500,
                    help="files per HF commit (HF's 128/hour commit-rate limit is PER COMMIT, not per file)")
    args = ap.parse_args()

    failed = []
    if not args.skip_generate:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        all_isos = sorted(manifest["languages"])
        isos = [i.strip() for i in args.iso.split(",")] if args.iso else all_isos

        print(f"[compact_align_batch] {len(isos)} language(s) to check", file=sys.stderr)

        plan: dict[str, list[str]] = {}   # iso -> [tag, ...] (every data-bearing edition)
        for iso in isos:
            tags = [t for t, _ in discover_tags(iso) if has_eflomal(t)]
            if tags:
                plan[iso] = tags

        total_tags = sum(len(v) for v in plan.values())
        print(f"[compact_align_batch] {total_tags} edition(s) across {len(plan)} language(s)", file=sys.stderr)

        if args.dry_run:
            for iso, tags in plan.items():
                for tag in tags:
                    usj_dir = args.usj_root / USJ_DIR_OVERRIDES.get(tag, f"usj-{tag}")
                    print(f"  {iso:<8} {tag:<12} usj_dir={usj_dir}", file=sys.stderr)
            return 0

        for iso, tags in plan.items():
            for tag in tags:
                usj_dir = args.usj_root / USJ_DIR_OVERRIDES.get(tag, f"usj-{tag}")
                ok = _run([sys.executable, "-m", "lexeme_aligner.compact_align", "--iso", tag,
                          "--publish-iso", iso, "--usj-dir", str(usj_dir), "--publish", str(args.publish),
                          "--methods", args.methods], f"{iso}/{tag}")
                if not ok:
                    failed.append(f"{iso}/{tag}")

        print(f"\n[compact_align_batch] done — {total_tags - len(failed)}/{total_tags} edition(s) "
              f"published locally under {args.publish}.", file=sys.stderr)
        if failed:
            print(f"[compact_align_batch] FAILED: {failed}", file=sys.stderr)

    if args.publish_hf and not args.local_only:
        publish_to_hf(args.publish, args.publish_hf, args.create, args.dry_run, args.chunk_size)
    elif not args.publish_hf:
        print("[compact_align_batch] nothing published to HF (no --publish-hf) — "
              "that's still a separate, deliberate step.", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
