"""Fetch a pinned snapshot of BSB-publishing/bsb-data-output's `base/display/` — the Berean Standard
Bible's own publisher-tagged English<->Strong's word spans (native span-tagging, not an id-gap
heuristic like globalbibletools/data; see gbt_align.py and internal-docs/gbt-alignment-handover.md).

Same discipline as gbt_fetch's sibling modules: pinned to a commit SHA, re-pinned deliberately — NOT
a live fetch of `main`. One GitHub API call (git tree, recursive) to list the ~1,190 per-chapter
files, then a plain `raw.githubusercontent.com` download per file (stdlib only, no auth needed for
either call at this volume).

  python -m lexeme_aligner.bsb_fetch
  BSB_DATA_OUTPUT_COMMIT=<sha> python -m lexeme_aligner.bsb_fetch   # override the pin for a bump
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

_DATA_DIR = Path("data/bsb")

# Pinned commit — re-verified fresh 2026-07-17 (the publisher's earlier ~5-month-stale build,
# flagged in internal-docs/gbt-alignment-handover.md, has since been refreshed: bsb_usj sha now
# d3a411d @ 2026-06-13, only ~1 month behind bsb2usfm's latest release).
BSB_DATA_OUTPUT_COMMIT = "a0bcfbbcfe217c66f31b1c886dd95c4424061e0e"
TREE_API = "https://api.github.com/repos/BSB-publishing/bsb-data-output/git/trees/{commit}?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/BSB-publishing/bsb-data-output/{commit}"


def fetch(commit: str | None = None, data_dir: Path = _DATA_DIR) -> Path:
    """Download base/display/*/*.json (per-chapter heb+eng Strong's-tagged spans) to data_dir.
    Idempotent — a `commit`-stamped marker file skips re-download if already present at that pin."""
    commit = commit or os.environ.get("BSB_DATA_OUTPUT_COMMIT") or BSB_DATA_OUTPUT_COMMIT
    marker = data_dir / ".commit"
    if data_dir.exists() and marker.exists() and marker.read_text().strip() == commit:
        print(f"[bsb_fetch] already fetched at {commit[:8]}, skipping", file=sys.stderr)
        return data_dir

    print(f"[bsb_fetch] listing base/display/ @ {commit[:8]} …", file=sys.stderr)
    with urllib.request.urlopen(TREE_API.format(commit=commit)) as resp:
        tree = json.loads(resp.read())
    if "tree" not in tree:
        raise SystemExit(f"[bsb_fetch] unexpected tree API response: {tree}")
    files = [t["path"] for t in tree["tree"]
             if t["path"].startswith("base/display/") and t["path"].endswith(".json")]
    if not files:
        raise SystemExit(f"[bsb_fetch] no base/display/*.json files found at {commit[:8]}")

    display_dir = data_dir / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(files, 1):
        rel = path[len("base/display/"):]
        out_fp = display_dir / rel
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        url = f"{RAW_BASE.format(commit=commit)}/{path}"
        urllib.request.urlretrieve(url, out_fp)
        if i % 200 == 0 or i == len(files):
            print(f"[bsb_fetch] {i}/{len(files)} files …", file=sys.stderr)

    marker.write_text(commit)
    print(f"[bsb_fetch] fetched {len(files)} chapter files -> {display_dir}", file=sys.stderr)
    return data_dir


if __name__ == "__main__":
    fetch()
