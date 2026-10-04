"""Publish ONE language — now a thin wrapper over `publish_safe.py` (2026-10-02).

The previous version uploaded the LOCAL manifest.json whole, which describes hundreds of languages whose new data is not on Hugging Face yet, so a
single-language publish made the HF manifest claim things that are not there. `publish_safe.py` merges only the selected language into the manifest
that is on HF now, never deletes, runs a preflight, guards the schema, routes the compact sidecar layers to their own repos and verifies afterwards.

    python3 pipeline/scripts/publish_lang.py --iso tgl              # dry run (the safe default)
    python3 pipeline/scripts/publish_lang.py --iso tgl --push       # really publish
    python3 pipeline/scripts/publish_lang.py --iso tgl --push --include-mwe

Kept flags: `--iso`, `--skip DATASET[,..]` (by local dir name, e.g. aligned_mwe), `--dry-run` (now the default, accepted for old habits),
`--create` (accepted and ignored: the repos exist and the layer repos are created on demand).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SAFE = Path(__file__).resolve().parent / "publish_safe.py"
DEFAULT_DATASETS = ["lexeme-alignments", "senses_attested_ubs", "compact-alignments"]


def build_command(iso: str, push: bool, skip: list[str], include_mwe: bool) -> list[str]:
    datasets = [d for d in DEFAULT_DATASETS + (["aligned_mwe"] if include_mwe else []) if d not in skip]
    if not datasets:
        raise SystemExit("nothing left to publish after --skip")
    cmd = [sys.executable, str(SAFE), "--iso", iso, "--datasets", ",".join(datasets)]
    if include_mwe:
        cmd.append("--include-mwe")
    if push:
        cmd.append("--push")
    return cmd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--push", action="store_true", help="actually publish (default is a dry run)")
    ap.add_argument("--dry-run", action="store_true", help="accepted for old habits; a dry run is already the default")
    ap.add_argument("--create", action="store_true", help="accepted and ignored")
    ap.add_argument("--skip", default="", help="comma-separated datasets to leave out, by local dir name")
    ap.add_argument("--include-mwe", action="store_true", help="also publish aligned_mwe (its schema guard still applies)")
    a = ap.parse_args(argv)
    skip = [s.strip() for s in a.skip.split(",") if s.strip()]
    return subprocess.call(build_command(a.iso, a.push and not a.dry_run, skip, a.include_mwe))


if __name__ == "__main__":
    raise SystemExit(main())
