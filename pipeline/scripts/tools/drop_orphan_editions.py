"""Drop compact-alignments editions that are not in the current alignment set (manifest minus pool).

Moves each edition's folder (main + meta + extra) from publish/compact-alignments/<iso[0]>/<iso>/<edition>/ into a timestamped backup under
pipeline/work/ and removes its entry from publish/compact-alignments/manifest.json. LOCAL only: nothing on Hugging Face is touched.
Dry run unless --apply.

    .venv/bin/python pipeline/scripts/tools/drop_orphan_editions.py            # list what would move
    .venv/bin/python pipeline/scripts/tools/drop_orphan_editions.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))
from lexeme_aligner.manifest_io import update_json  # noqa: E402

ROOT = REPO / "publish/compact-alignments"

def orphans() -> list[tuple[str, str]]:
    """(language, edition id in the manifest) for every published edition that is NOT in the current alignment set (onboard.editions_for:
    every fetchable, distinct edition the catalog lists). The principle (internal-docs/repo-responsibilities.md): drop list = manifest
    minus pool. We keep nothing the catalog does not give us; same-text checks are information, not a condition."""
    from lexeme_aligner.onboard import _tag, editions_for
    slug = lambda x: "".join(c if c.isalnum() else "_" for c in x.lower())          # noqa: E731
    man = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8")).get("languages", {})
    out = []
    for iso, entry in sorted(man.items()):
        try:
            pool = {_tag(iso, e["edition_code"], False) for e in editions_for(iso, {"ot", "nt"})}
        except Exception as exc:                                                    # a language the catalog no longer resolves: leave it alone
            print(f"  {iso}: pool not resolvable ({exc}); skipped")
            continue
        for key, e in entry.get("editions", {}).items():
            if slug(e.get("tag") or key) not in pool:
                out.append((iso, key))
    return out




def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    bk = REPO / "pipeline/work" / ("backup-orphan-editions-" + time.strftime("%Y%m%d%H%M"))
    DROP = orphans()
    for iso, ed in DROP:
        p = ROOT / iso[0] / iso / ed
        n = sum(1 for _ in p.glob("*")) if p.is_dir() else 0
        print(f"  {iso}/{ed}: {n} files" if p.is_dir() else f"  {iso}/{ed}: folder not found")
        if a.apply and p.is_dir():
            (bk / iso).mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(bk / iso / ed))
    if not a.apply:
        print("dry run; --apply moves them and edits the manifest")
        return 0
    shutil.copy(ROOT / "manifest.json", bk / "manifest.json")

    drop_list = DROP

    def _m(doc):
        langs = doc.get("languages", doc)
        for iso, ed in drop_list:
            langs.get(iso, {}).get("editions", {}).pop(ed, None)
        return doc
    update_json(ROOT / "manifest.json", _m, default={})
    print(f"moved to {bk}; manifest entries removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
