"""Publish every currently-onboarded language's `lexeme-alignments` partition (+ fra/ind
`senses_attested` + the `cross_lang_prior` profile) to HF. Mirrors gapfill_batch.py's tag discovery
(onboard.editions_for()/_tag(), never hand-maintained) so the --pool argument for each language is
always derived the same way the local re-export/gapfill runs used.

Nothing here runs automatically — this is a batch DRIVER the user runs deliberately, same discipline
as onboard_batch.py/gapfill_batch.py: publishing is always an explicit, separate step.

    python3 scripts/publish_all.py --dry-run     # show the plan, push nothing
    python3 scripts/publish_all.py               # publish everything
    python3 scripts/publish_all.py --iso ind,por # scope to specific languages
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.gapfill_batch import discover_tags

SENSES_LANGS = {"fra": "French", "ind": "Indonesian"}   # only languages with senses_attested/iso=<x> published


def _run(cmd: list, label: str) -> bool:
    print(f"  ▶ {label}", file=sys.stderr)
    if not dry_run:
        result = subprocess.run(cmd)
        return result.returncode == 0
    print(f"    {' '.join(cmd)}", file=sys.stderr)
    return True


def main() -> int:
    global dry_run
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-separated isos to restrict to (default: every published language)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", type=Path, default=LEX_ROOT / "manifest.json")
    ap.add_argument("--skip-senses", action="store_true")
    ap.add_argument("--skip-cross-lang", action="store_true")
    args = ap.parse_args()
    dry_run = args.dry_run

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    all_isos = sorted(manifest["languages"])
    isos = [i.strip() for i in args.iso.split(",")] if args.iso else all_isos

    # Each export_lex.py invocation now bundles its changed files into ONE HF commit and skips
    # entirely when nothing changed (see export_lex.publish_to_hf) — but the Hub's commit-rate limit
    # (128/hour) can still be hit on a big batch. export_lex.py exits cleanly (short message, not a
    # giant traceback) when that happens; STOP the whole batch on the first failure rather than
    # burning through the rest of the languages against a quota that won't reset for up to an hour.
    # Safe to just re-run the same command later: .publish_state.json only records real successes,
    # so already-pushed languages are skipped fast and it resumes exactly where it stopped.
    print(f"[publish_all] {len(isos)} language(s) → bcv-commons/lexeme-alignments", file=sys.stderr)
    for iso in isos:
        entry = manifest["languages"][iso]
        tags = [t for t, _ in discover_tags(iso)]
        if not tags:
            print(f"  !! {iso}: no tags discovered, skipping", file=sys.stderr)
            continue
        primary, secondary = tags[0], tags[1:]
        lang_name = entry.get("language")
        cmd = [sys.executable, "-m", "lexeme_aligner.export_lex", "--iso", primary]
        if secondary:
            cmd += ["--pool", ",".join(secondary)]
        if lang_name:
            cmd += ["--lang-name", lang_name]
        cmd += ["--publish", "bcv-commons/lexeme-alignments", "--create"]
        if not _run(cmd, f"lexeme-alignments/{iso}"):
            print(f"\n[publish_all] STOPPED at {iso} — re-run the same command later to resume "
                  f"(already-pushed languages are cache-skipped, not redone)", file=sys.stderr)
            return 1

    if not args.skip_senses:
        print("\n[publish_all] senses_attested → bcv-commons/senses-attested", file=sys.stderr)
        for iso, name in SENSES_LANGS.items():
            if iso not in isos:
                continue
            cmd = [sys.executable, "-m", "lexeme_aligner.senses_attested", "--iso", iso,
                   "--lang-name", name, "--publish", "bcv-commons/senses-attested", "--create"]
            if not _run(cmd, f"senses-attested/{iso}"):
                print(f"\n[publish_all] STOPPED at senses-attested/{iso} — re-run later to resume",
                      file=sys.stderr)
                return 1

    if not args.skip_cross_lang:
        print("\n[publish_all] cross_lang_prior profile → bcv-commons/cross-lingual-span-profile", file=sys.stderr)
        cmd = [sys.executable, "-m", "lexeme_aligner.cross_lang_prior",
               "--out", "resources/cross_lang_prior/profile.json",
               "--publish", "bcv-commons/cross-lingual-span-profile", "--create"]
        if not _run(cmd, "cross-lingual-span-profile"):
            print("\n[publish_all] STOPPED at cross-lingual-span-profile — re-run later to resume",
                  file=sys.stderr)
            return 1

    print(f"\n[publish_all] {'dry-run done — nothing pushed' if dry_run else 'done'}", file=sys.stderr)
    return 0


dry_run = False

if __name__ == "__main__":
    raise SystemExit(main())
