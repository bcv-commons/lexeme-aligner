"""Stage (and, with --push, publish) the gram-struct dataset: per-language grammar fact sheets, licence partitions as directories.

    .venv/bin/python pipeline/scripts/tools/stage_gram_struct.py            # stage into publish/gram-struct + checks; nothing pushed
    .venv/bin/python pipeline/scripts/tools/stage_gram_struct.py --push     # needs hf auth; repo bcv-commons/gram-struct

Staged: README.md (the dataset card, from gram_struct._README), _coverage.json, external/ (CC-BY-4.0), imputed/ (CC-BY-SA-4.0),
derived/ + measured/ (CC0), kin/ (CC-BY-4.0) and the merged <iso>.json files (mixed; the card says so). NOT staged: derived_input/
(working input, re-derivable), article_bound.json (its content is already in derived/), any other file.

Checks that stop a push: a key that is a BHSA-derived field name anywhere in a staged file (phrase_id, function, rela, bhsa_*), a
derived/ file whose constituent profile is not MACULA phrase-role keyed, a merged file whose iso has no partition file, an empty partition.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))
SRC = REPO / "config/gram_struct"
DEST = REPO / "publish/gram-struct"
REPO_ID = "bcv-commons/gram-struct"
PARTS = ("external", "imputed", "derived", "measured", "kin")
BHSA_KEYS = {"phrase_id", "rela", "bhsa_function", "bhsa_rela"}


def _bad_keys(obj, path="") -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BHSA_KEYS or k.startswith("bhsa"):
                out.append(f"{path}/{k}")
            out += _bad_keys(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _bad_keys(v, f"{path}[{i}]")
    return out


def stage(src: Path = SRC, dest: Path = DEST) -> dict:
    from lexeme_aligner.gram_struct import _README
    problems, counts = [], {}
    if dest.exists():
        for p in dest.iterdir():
            if p.name != ".publish_state.json":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "README.md").write_text(_README, encoding="utf-8")
    shutil.copy2(src / "_coverage.json", dest / "_coverage.json")
    for part in PARTS:
        files = sorted((src / part).glob("*.json"))
        counts[part] = len(files)
        if not files:
            problems.append(f"{part}/ is empty")
        (dest / part).mkdir()
        for fp in files:
            doc = json.loads(fp.read_text(encoding="utf-8"))
            bad = _bad_keys(doc)
            if bad:
                problems.append(f"{part}/{fp.name}: BHSA-looking keys {bad[:3]}")
            co = doc.get("constituent_order") if part == "derived" else None
            if co and co.get("label_scheme") not in (None, "macula_phrase_role") and "pair_order_kept" in co:
                if co.get("label_scheme") != "macula_phrase_role":
                    problems.append(f"derived/{fp.name}: constituent profile label_scheme {co.get('label_scheme')!r}")
            shutil.copy2(fp, dest / part / fp.name)
    merged = sorted(p for p in src.glob("*.json") if not p.name.startswith("_") and p.stem != "article_bound")
    counts["merged"] = len(merged)
    for fp in merged:
        if not any((src / part / fp.name).exists() for part in PARTS):
            problems.append(f"{fp.name}: merged file without any partition file")
        bad = _bad_keys(json.loads(fp.read_text(encoding="utf-8")))
        if bad:
            problems.append(f"{fp.name}: BHSA-looking keys {bad[:3]}")
        shutil.copy2(fp, dest / fp.name)
    return {"counts": counts, "problems": problems,
            "files": sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file() and p.name != ".publish_state.json")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--create", action="store_true", help="create the HF repo if it does not exist (with --push)")
    a = ap.parse_args(argv)
    res = stage()
    print(json.dumps({"counts": res["counts"], "files": len(res["files"]), "problems": res["problems"][:20]}, indent=1))
    if res["problems"]:
        print("[gram-struct] NOT publishable until the problems above are fixed", file=sys.stderr)
        return 1
    from lexeme_aligner.hf_bulk_publish import publish_chunked
    publish_chunked(DEST, REPO_ID, res["files"], create=a.create, dry_run=not a.push, label="gram-struct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
