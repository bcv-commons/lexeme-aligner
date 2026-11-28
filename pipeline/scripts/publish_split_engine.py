"""Publish the split engine (pipeline/split_engine) to HF dataset bcv-commons/split-engine, under <version>/ and tagged v<version>.
Dry run unless --push. Refuses if SHA256SUMS does not match the files, or if the version folder is already on HF.

    .venv/bin/python pipeline/scripts/publish_split_engine.py [--push]"""
import argparse, hashlib, sys
from pathlib import Path

REPO_ID = "bcv-commons/split-engine"
ENGINE = Path(__file__).resolve().parents[2] / "pipeline/split_engine"
FILES = ("bcv_segment.py", "LICENSE", "README.md", "CHANGELOG.md", "VERSION", "SHA256SUMS")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    version = (ENGINE / "VERSION").read_text().strip()
    for line in (ENGINE / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split()
        if hashlib.sha256((ENGINE / name).read_bytes()).hexdigest() != sha:
            raise SystemExit(f"SHA256SUMS does not match {name}: refresh it first")
    from huggingface_hub import HfApi
    api = HfApi()
    exists = api.repo_exists(REPO_ID, repo_type="dataset")
    if exists and any(f.startswith(f"{version}/") for f in api.list_repo_files(REPO_ID, repo_type="dataset")):
        raise SystemExit(f"{REPO_ID} already has {version}/: bump VERSION for a change")
    print(f"{REPO_ID} ({'exists' if exists else 'new, public'}): {version}/ <- {', '.join(FILES)}; root README.md; tag v{version}")
    if not a.push:
        print("dry run — nothing pushed (add --push)")
        return 0
    if not exists:
        api.create_repo(REPO_ID, repo_type="dataset", private=False)
    from huggingface_hub import CommitOperationAdd
    ops = [CommitOperationAdd(f"{version}/{f}", str(ENGINE / f)) for f in FILES]
    ops.append(CommitOperationAdd("README.md", str(ENGINE / "README.md")))
    info = api.create_commit(REPO_ID, ops, repo_type="dataset", commit_message=f"split engine {version}")
    api.create_tag(REPO_ID, tag=f"v{version}", revision=info.oid, repo_type="dataset")
    got = set(api.list_repo_files(REPO_ID, repo_type="dataset", revision=f"v{version}"))
    missing = [f for f in FILES if f"{version}/{f}" not in got]
    print(f"pushed {info.oid[:8]}, tag v{version}" + (f"; MISSING {missing}" if missing else "; all files present"))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
