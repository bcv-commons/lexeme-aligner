"""Remove the editions moved out by drop_orphan_editions.py from the three compact-alignments repos on Hugging Face.

Reads the backup folder (<iso>/<edition>/<file>) that drop_orphan_editions.py --apply made, maps each file to its repo path
(<iso[0]>/<iso>/<edition>/<file>, same in main, -meta, -extra), and deletes ONLY paths that exist on HF AND whose edition is in the backup.
Also removes those editions from the manifest.json on HF (edited from HF's own copy, never the local one). Dry run unless --push.

    .venv/bin/python pipeline/scripts/tools/hf_remove_dropped_editions.py --backup pipeline/work/backup-orphan-editions-<ts>
    .venv/bin/python pipeline/scripts/tools/hf_remove_dropped_editions.py --backup ... --push
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

REPOS = {"main": "bcv-commons/compact-alignments", "meta": "bcv-commons/compact-alignments-meta", "extra": "bcv-commons/compact-alignments-extra"}
CHUNK = 1000


def layer_of(name: str) -> str:
    return "meta" if name.endswith(".meta.json") else "extra" if name.endswith(".extra.json") else "main"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_download
    api = HfApi()
    editions = sorted((p.parent.name, p.name) for p in a.backup.glob("*/*") if p.is_dir())
    want: dict[str, set[str]] = {k: set() for k in REPOS}
    for iso, ed in editions:
        for f in (a.backup / iso / ed).iterdir():
            want[layer_of(f.name)].add(f"{iso[0]}/{iso}/{ed}/{f.name}")
    print(f"{len(editions)} editions, {sum(map(len, want.values()))} files in the backup")
    todo: dict[str, list[str]] = {}
    for layer, repo in REPOS.items():
        have = set(api.list_repo_files(repo, repo_type="dataset"))
        todo[layer] = sorted(want[layer] & have)
        # anything under a dropped edition folder on HF that the backup does not have (newer or stray files) is listed, not deleted
        prefixes = {f"{iso[0]}/{iso}/{ed}/" for iso, ed in editions}
        extra = sorted(p for p in have if any(p.startswith(x) for x in prefixes) and p not in want[layer] and layer_of(p) == layer)
        print(f"  {repo}: {len(todo[layer])} to delete ({len(want[layer]) - len(todo[layer])} already gone)" + (f", {len(extra)} on HF but not in the backup (kept): {extra[:3]}" if extra else ""))
    man = json.loads(Path(hf_hub_download(REPOS["main"], "manifest.json", repo_type="dataset")).read_text(encoding="utf-8"))
    langs = man.get("languages", {})
    gone = [(i, e) for i, e in editions if e in langs.get(i, {}).get("editions", {})]
    print(f"  HF manifest: {len(gone)} of {len(editions)} editions listed there")
    if not a.push:
        print("dry run; --push deletes them")
        return 0
    for layer, repo in REPOS.items():
        paths = todo[layer]
        for i in range(0, len(paths), CHUNK):
            ops = [CommitOperationDelete(path_in_repo=p) for p in paths[i:i + CHUNK]]
            api.create_commit(repo, ops, repo_type="dataset", commit_message=f"Remove editions no longer in the alignment set ({i // CHUNK + 1}/{-(-len(paths) // CHUNK)})")
            print(f"  {repo}: deleted {min(i + CHUNK, len(paths))}/{len(paths)}")
    for i, e in gone:
        del langs[i]["editions"][e]
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "manifest.json"
        mp.write_text(json.dumps(man, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
        api.create_commit(REPOS["main"], [CommitOperationAdd("manifest.json", str(mp))], repo_type="dataset",
                          commit_message="Manifest: drop editions no longer in the alignment set")
    print("done; HF manifest edited. Re-run without --push to verify (everything should say 0 to delete).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
