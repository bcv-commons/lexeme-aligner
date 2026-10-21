"""Delete the OLDER book versions of compact-alignments on Hugging Face (owner decision 2026-10-12, plan
internal-docs/compact-text-fingerprint-plan-2026-10-12.md section 6): files <BOOK>_<5 hex>.* whose name is not the legacy name of a
CURRENT version (rename_map.tsv action rename/current). Their text is gone, so no fingerprint can reach them. Every path is first
written with its upload date to pipeline/work/fp-migration/hf_older_versions_log.tsv. The old names of the current versions are NOT
touched (kept until the transition ends). Dry run unless --push; one commit per --chunk paths, in all three repos.

    .venv/bin/python pipeline/scripts/tools/hf_delete_older_versions.py [--push]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORK = REPO / "pipeline/work/fp-migration"
REPOS = ("bcv-commons/compact-alignments", "bcv-commons/compact-alignments-meta", "bcv-commons/compact-alignments-extra")
NAME = re.compile(r"^(.+)/([0-9A-Z]{3})_([0-9a-f]{5})\.(json|meta\.json|extra\.json)$")


def main() -> int:
    from huggingface_hub import CommitOperationDelete, HfApi
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--chunk", type=int, default=3000)
    a = ap.parse_args()
    current = {(r["dir"], r["book"], r["old"]) for r in csv.DictReader(open(WORK / "rename_map.tsv", encoding="utf-8"), delimiter="\t")
               if r["tree"] == "compact" and r["action"] in ("rename", "current")}
    api = HfApi()
    log = open(WORK / "hf_older_versions_log.tsv", "a", encoding="utf-8")
    for repo in REPOS:
        old = [f for f in api.list_repo_files(repo, repo_type="dataset")
               if (m := NAME.match(f)) and (m[1], m[2], m[3]) not in current]
        dates = {}
        for k in range(0, len(old), 100):
            for info in api.get_paths_info(repo, old[k:k + 100], repo_type="dataset", expand=True):
                lc = getattr(info, "last_commit", None)
                dates[info.path] = str(getattr(lc, "date", "")) if lc else ""
        for f in old:
            log.write(f"{time.strftime('%Y-%m-%d')}\t{repo}\t{f}\t{dates.get(f, '')}\t{'deleted' if a.push else 'dry-run'}\n")
        log.flush()
        print(f"[delete] {repo}: {len(old)} older version(s){'' if a.push else ' (dry run)'}", file=sys.stderr)
        if a.push:
            for k in range(0, len(old), a.chunk):
                api.create_commit(repo, repo_type="dataset", operations=[CommitOperationDelete(p) for p in old[k:k + a.chunk]],
                                  commit_message=f"Delete {len(old[k:k + a.chunk])} older book versions (text fingerprint switch 2026-10-21)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
