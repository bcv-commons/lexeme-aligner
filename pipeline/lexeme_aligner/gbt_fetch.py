"""Fetch a pinned snapshot of globalbibletools/data (CC0-1.0) into pipeline/vendor/gbt/ — the shared
source both gbt_align.py (occurrence-alignment/benchmark-corroboration layer) and gbt_source.py
(gloss-priors for gloss_align.py) read from. One fetch, one pin, two consumers — see
internal-docs/gbt-alignment-handover.md for how this repo was found and why it's split that way.

Same discipline as bsb_fetch.py: pinned to a commit SHA, re-pinned deliberately — NOT a live fetch of
`main`. One GitHub API call (git tree, recursive) to list every `<lang>/<NN-Code>.json` file across
every language directory (not just the ones we currently use — same "whole snapshot" discipline as
bsb_fetch, so a later language add doesn't need a re-fetch), then a plain raw.githubusercontent.com
download per file (stdlib only, no auth needed at this volume: ~2,640 files / 40 language dirs as of
this pin).

  python -m lexeme_aligner.gbt_fetch
  GBT_DATA_COMMIT=<sha> python -m lexeme_aligner.gbt_fetch   # override the pin for a bump
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

_DATA_DIR = Path("pipeline/vendor/gbt")

# Pinned commit — re-pinned 2026-08-03 (this session's integration push; supersedes the
# f5af0eb8 @ 2026-07-12 pin the shoresh handover doc originally found this repo at).
GBT_DATA_COMMIT = "5b6a5d89917d04b59cf58fa0eed6c8731eceaa16"
TREE_API = "https://api.github.com/repos/globalbibletools/data/git/trees/{commit}?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/globalbibletools/data/{commit}"
_UA = "lexeme-aligner/0.1 (+https://github.com/bcv-commons/lexeme-aligner)"


def fetch(commit: str | None = None, data_dir: Path = _DATA_DIR) -> Path:
    """Download every `<lang>/<NN-Code>.json` file to data_dir/<lang>/<NN-Code>.json. Idempotent —
    a `commit`-stamped marker file skips re-download if already present at that pin."""
    commit = commit or os.environ.get("GBT_DATA_COMMIT") or GBT_DATA_COMMIT
    marker = data_dir / ".commit"
    if data_dir.exists() and marker.exists() and marker.read_text().strip() == commit:
        print(f"[gbt_fetch] already fetched at {commit[:8]}, skipping", file=sys.stderr)
        return data_dir

    print(f"[gbt_fetch] listing repo tree @ {commit[:8]} …", file=sys.stderr)
    req = urllib.request.Request(TREE_API.format(commit=commit), headers={"User-Agent": _UA})
    with urllib.request.urlopen(req) as resp:
        tree = json.loads(resp.read())
    if "tree" not in tree:
        raise SystemExit(f"[gbt_fetch] unexpected tree API response: {tree}")
    files = [t["path"] for t in tree["tree"] if t["path"].endswith(".json") and "/" in t["path"]]
    if not files:
        raise SystemExit(f"[gbt_fetch] no <lang>/<book>.json files found at {commit[:8]}")

    data_dir.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(files, 1):
        out_fp = data_dir / path
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        url = f"{RAW_BASE.format(commit=commit)}/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as r, out_fp.open("wb") as out:
            out.write(r.read())
        if i % 200 == 0 or i == len(files):
            print(f"[gbt_fetch] {i}/{len(files)} files …", file=sys.stderr)

    marker.write_text(commit)
    n_langs = len({p.split("/", 1)[0] for p in files})
    print(f"[gbt_fetch] fetched {len(files)} files across {n_langs} language dirs -> {data_dir}",
          file=sys.stderr)
    return data_dir


if __name__ == "__main__":
    fetch()
