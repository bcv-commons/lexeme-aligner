"""Publish config/constituent_order/ (the CC0 constituent-order profiles, tracked in git, card = its README.md) to Hugging Face.

    .venv/bin/python pipeline/scripts/tools/publish_constituent_order.py           # checks + dry run
    .venv/bin/python pipeline/scripts/tools/publish_constituent_order.py --push --create

Checks that stop a push: a profile whose `label_scheme` is not `macula_phrase_role` (the BHSA-keyed profiles were retired 2026-10-04),
an unparseable file, a README without a cc0-1.0 licence line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))
ROOT = REPO / "config/constituent_order"
REPO_ID = "bcv-commons/constituent-order-profile"


def check(root: Path = ROOT) -> tuple[list[str], list[str]]:
    files, problems = ["README.md"], []
    if "license: cc0-1.0" not in (root / "README.md").read_text(encoding="utf-8"):
        problems.append("README.md: no cc0-1.0 licence line")
    for fp in sorted(root.glob("*.json")):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except ValueError:
            problems.append(f"{fp.name}: not JSON")
            continue
        if doc.get("label_scheme") != "macula_phrase_role":
            problems.append(f"{fp.name}: label_scheme {doc.get('label_scheme')!r}")
        files.append(fp.name)
    return files, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true"); ap.add_argument("--create", action="store_true")
    a = ap.parse_args(argv)
    files, problems = check()
    print(json.dumps({"files": len(files), "problems": problems[:20]}, indent=1))
    if problems:
        return 1
    from lexeme_aligner.hf_bulk_publish import publish_chunked
    publish_chunked(ROOT, REPO_ID, files, create=a.create, dry_run=not a.push, label="constituent-order-profile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
