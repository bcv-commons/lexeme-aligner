"""Retroactively remove leftover PKF+USFM ingest scratch (pipeline/work/out/usfm-<X>/ and
pipeline/work/ingest-cache/pkf-pool/<X>/) accumulated before cdn_source.py started cleaning up after
itself. Both are pure CDN-ingest intermediates — safe to remove once the language they belong to has
confirmed PUBLISHED data in lexeme-alignments (the only thing that still needs the USJ text they
produced; the text itself lives on in pipeline/work/ingest-cache/usj-<tag>/, untouched by this script).

Resolution reuses the same layered tag->iso mapping as clean_out_safe.py (manifest base_texts, then
config/legacy_bare_iso_tags.json, then a 3-letter-prefix heuristic) since these scratch dirs are keyed
on the CDN edition PARAM (e.g. "ind_ags"), which doesn't always match the aligner's own tag naming.

    python3 pipeline/scripts/clean_ingest_scratch.py            # dry run (default)
    python3 pipeline/scripts/clean_ingest_scratch.py --delete   # actually delete
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT

_LEGACY_TAGS_PATH = Path("config/legacy_bare_iso_tags.json")
_USFM_ROOT = Path("pipeline/work/out")
_POOL_ROOT = Path("pipeline/work/ingest-cache/pkf-pool")


def _manifest_tags(root: str) -> dict[str, set[str]]:
    fp = Path(root) / "manifest.json"
    if not fp.exists():
        return {}
    doc = json.loads(fp.read_text(encoding="utf-8"))
    return {iso: (set(e["base_texts"]) if e.get("base_texts") else {iso})
            for iso, e in doc.get("languages", {}).items()}


def _build_tag_to_iso(lex: dict[str, set[str]]) -> dict[str, str]:
    tag_to_iso: dict[str, str] = {}
    for iso, tags in lex.items():
        for t in tags:
            tag_to_iso.setdefault(t.lower(), iso)
        tag_to_iso.setdefault(iso, iso)
    if _LEGACY_TAGS_PATH.exists():
        legacy = json.loads(_LEGACY_TAGS_PATH.read_text(encoding="utf-8")).get("isos", {})
        for iso, edcode in legacy.items():
            tag_to_iso.setdefault(edcode.lower(), iso)
            tag_to_iso.setdefault(iso, iso)
    return tag_to_iso


def _resolve(tag: str, tag_to_iso: dict[str, str]) -> str | None:
    if tag.lower() in tag_to_iso:
        return tag_to_iso[tag.lower()]
    m = re.match(r"^([a-z]{3})", tag.lower())
    if m and m.group(1) in tag_to_iso.values():
        return m.group(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true", help="actually delete — default is a dry run")
    args = ap.parse_args()

    lex = _manifest_tags(str(LEX_ROOT))
    tag_to_iso = _build_tag_to_iso(lex)
    published_isos = set(lex)

    usfm_dirs = {p.name[len("usfm-"):]: p for p in _USFM_ROOT.glob("usfm-*") if p.is_dir()}
    pool_dirs = {p.name: p for p in _POOL_ROOT.glob("*") if p.is_dir()} if _POOL_ROOT.exists() else {}

    all_x = sorted(set(usfm_dirs) | set(pool_dirs))
    safe, unsafe = [], []
    for x in all_x:
        iso = _resolve(x, tag_to_iso)
        if iso is None:
            unsafe.append((x, "no resolvable language"))
        elif iso not in published_isos:
            unsafe.append((x, f"resolved to '{iso}' but no lexeme-alignments entry"))
        else:
            safe.append((x, iso))

    def _size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    safe_bytes = sum(_size(usfm_dirs[x]) if x in usfm_dirs else 0 for x, _ in safe) \
        + sum(_size(pool_dirs[x]) if x in pool_dirs else 0 for x, _ in safe)

    print(f"[clean_ingest_scratch] {len(all_x)} distinct ingest ID(s) with leftover scratch "
          f"({len(usfm_dirs)} usfm-*/ dirs, {len(pool_dirs)} pkf-pool/*/ dirs)")
    print(f"  safe to clean:   {len(safe)} ID(s), {safe_bytes/1e9:.2f} GB")
    print(f"  NOT safe yet:    {len(unsafe)} ID(s)")
    for x, why in sorted(unsafe)[:20]:
        print(f"    {x:20} — {why}")
    if len(unsafe) > 20:
        print(f"    ... and {len(unsafe) - 20} more")

    if not args.delete:
        print("\n[clean_ingest_scratch] dry run — nothing deleted. Re-run with --delete to actually "
              "remove the safe set.")
        return 0

    removed = 0
    for x, _ in safe:
        if x in usfm_dirs:
            shutil.rmtree(usfm_dirs[x], ignore_errors=True)
            removed += 1
        if x in pool_dirs:
            shutil.rmtree(pool_dirs[x], ignore_errors=True)
            removed += 1
    print(f"\n[clean_ingest_scratch] deleted {removed} director(y/ies) across {len(safe)} ID(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
