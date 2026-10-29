"""Files in the three compact-alignments repos on HF that no current file stands for (2026-10-29).

A book file is named by the fingerprint of its edition's text, so a new text (or a new tokenizer: v3, Myanmar) gives a new name and the
old file stays on HF, unreachable: no client text has its fingerprint any more. The local tree publish/compact-alignments/<i>/<iso>/ is the
current set (publish_safe pushes every file there and its preflight allows exactly one file per book, named by the current text). An
orphan = a file on HF under a language folder that is not in the local tree. Top-level files and _index/ are never touched.

    .venv/bin/python pipeline/scripts/tools/compact_orphans.py                 list -> pipeline/work/logs/compact_orphans.json
    .venv/bin/python pipeline/scripts/tools/compact_orphans.py --delete        delete the listed orphans (commits of 3,000), then re-check
    ... --names fp8|old5|all     which orphans (default fp8): `fp8` = 8-hex fingerprint names (older texts, tokenizer v2); `old5` = the
                                 pre-2026-10-21 5-hex names, which the compact README promises to keep until 2026-12-20

Deleting is reversible: every HF commit keeps the files in the repo history.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))
from lexeme_aligner import compact_layers  # noqa: E402

LOCAL = REPO / "publish" / "compact-alignments"
REPOS = {"main": "bcv-commons/compact-alignments", "meta": "bcv-commons/compact-alignments-meta",
         "extra": "bcv-commons/compact-alignments-extra"}
OUT = REPO / "pipeline" / "work" / "logs" / "compact_orphans.json"


def is_language_file(path: str) -> bool:
    parts = path.split("/")
    return len(parts) >= 4 and len(parts[0]) == 1 and parts[1].startswith(parts[0])   # <i>/<iso>/<edition>/<file>


def name_kind(path: str) -> str:
    """`old5` for a pre-fingerprint name (<BOOK>_<5 hex>), else `fp8`."""
    h = path.rsplit("/", 1)[-1].split(".", 1)[0].rsplit("_", 1)[-1]
    return "old5" if len(h) == 5 else "fp8"


def orphans(hf_files: list[str], local: set[str], layer: str, names: str = "all") -> list[str]:
    """Pure: the HF files of `layer` under a language folder that the local tree does not have (`names`: fp8 / old5 / all)."""
    return sorted(f for f in hf_files if is_language_file(f) and compact_layers.layer_of(f) == layer and f not in local
                  and names in ("all", name_kind(f)))


def local_files() -> set[str]:
    return {str(p.relative_to(LOCAL)) for p in LOCAL.glob("?/*/*/*") if p.is_file()}


def survey(api, names: str = "all") -> dict:
    local = local_files()
    rep = {"local_files": len(local), "repos": {}}
    for layer, repo in REPOS.items():
        hf = api.list_repo_files(repo, repo_type="dataset")
        orph = orphans(hf, local, layer, names)
        by_ed = collections.Counter("/".join(f.split("/")[:3]) for f in orph)
        local_eds = {"/".join(f.split("/")[:3]) for f in local}
        rep["repos"][repo] = {"hf_files": len(hf), "orphans": len(orph), "orphan_editions": len(by_ed),
                              "editions_gone_locally": sorted(e for e in by_ed if e not in local_eds),
                              "top_editions": by_ed.most_common(15), "files": orph}
    return rep


def delete(api, repo: str, files: list[str], chunk: int = 3000) -> None:
    from huggingface_hub import CommitOperationDelete
    for i in range(0, len(files), chunk):
        part = files[i:i + chunk]
        for attempt in range(6):
            try:
                api.create_commit(repo, repo_type="dataset", operations=[CommitOperationDelete(f) for f in part],
                                  commit_message=f"Remove {len(part)} compact files no current text reaches (old fingerprints; "
                                                 "tokenizer v3 / changed texts)")
                break
            except Exception as e:                                       # rate limits / transient network
                print(f"[orphans] {repo} commit {i // chunk + 1}: {e}; retry", file=sys.stderr)
                time.sleep(30 * (attempt + 1))
        else:
            raise SystemExit(f"[orphans] {repo}: giving up at file {i}")
        print(f"[orphans] {repo}: deleted {min(i + chunk, len(files))}/{len(files)}", file=sys.stderr, flush=True)
        time.sleep(20)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--names", choices=["fp8", "old5", "all"], default="fp8")
    a = ap.parse_args()
    from huggingface_hub import HfApi
    api = HfApi()
    rep = survey(api, a.names)
    OUT.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for repo, r in rep["repos"].items():
        print(f"{repo}: {r['hf_files']} files on HF, {r['orphans']} orphans in {r['orphan_editions']} editions "
              f"({len(r['editions_gone_locally'])} editions no longer local)")
    if not a.delete:
        return 0
    for repo, r in rep["repos"].items():
        if r["files"]:
            delete(api, repo, r["files"])
    after = survey(api, a.names)
    left = {repo: r["orphans"] for repo, r in after["repos"].items()}
    print("[orphans] left after delete:", left)
    return 0 if not any(left.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
