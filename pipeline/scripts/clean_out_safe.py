"""Safely clean out/ raw jsonl — but ONLY for a tag whose LANGUAGE is confirmed fully consumed by
EVERY dataset that needs it: lexeme-alignments, aligned_mwe, AND compact-alignments (all three, by
design — a language whose aligned_mwe/compact-alignments step failed or hasn't run yet must NOT be
cleaned, even if lexeme-alignments alone looks done). senses_attested is deliberately NOT required —
it's legitimately absent for NT-only languages, so its absence must never block cleanup.

This is the corrected version of the ad hoc single-dataset check used earlier in the project's history
(which only looked at lexeme-alignments and, as a result, cleaned aligned_mwe/senses_attested/
compact-alignments out from under themselves before they'd ever run).

Tag->iso resolution is layered (same approach used for the 2026-07 stopwords/morph dedup): manifest
base_texts first, then config/legacy_bare_iso_tags.json (the 199 originally-grandfathered languages),
then a 3-letter-prefix heuristic — because many tags in out/ are ORPHANED leftovers from superseded
onboarding attempts (the language now publishes under a DIFFERENT, newer tag). An orphaned tag's jsonl
is safe to delete once its LANGUAGE (via this resolution) is fully covered, even though that exact tag
was never itself the one referenced.

    python3 pipeline/scripts/clean_out_safe.py            # dry run (default)
    python3 pipeline/scripts/clean_out_safe.py --delete   # actually delete
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT

_LEGACY_TAGS_PATH = Path("config/legacy_bare_iso_tags.json")


def _manifest_tags(root: str) -> dict[str, set[str]]:
    """iso -> set of base_texts/tags this dataset's manifest says it already consumed."""
    fp = Path(root) / "manifest.json"
    if not fp.exists():
        return {}
    doc = json.loads(fp.read_text(encoding="utf-8"))
    out = {}
    for iso, entry in doc.get("languages", {}).items():
        bts = entry.get("base_texts")
        out[iso] = set(bts) if bts else {iso}
    return out


def _build_tag_to_iso(lex: dict[str, set[str]], all_isos: set[str]) -> dict[str, str]:
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
    for iso in all_isos:
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
    mwe = _manifest_tags("publish/aligned_mwe")
    compact_fp = Path("publish/compact-alignments/manifest.json")
    compact_isos = set(json.loads(compact_fp.read_text(encoding="utf-8")).get("languages", {})) \
        if compact_fp.exists() else set()

    out_dir = Path(OUT)
    tag_files: dict[str, list[Path]] = {}
    for fp in out_dir.glob("align_*.jsonl"):
        m = re.match(r"^align_[a-z]+_(.+)_[A-Z0-9]+\.jsonl$", fp.name)
        if m:
            tag_files.setdefault(m.group(1), []).append(fp)

    tag_to_iso = _build_tag_to_iso(lex, set(lex))

    safe, unsafe = [], []
    for tag, files in tag_files.items():
        iso = _resolve(tag, tag_to_iso)
        if iso is None:
            unsafe.append((tag, len(files), "no resolvable language (bare-iso not found anywhere)"))
            continue
        missing = []
        if iso not in mwe:
            missing.append("aligned_mwe")
        if iso not in compact_isos:
            missing.append("compact-alignments")
        if missing:
            unsafe.append((tag, len(files), f"missing from: {', '.join(missing)}"))
        else:
            safe.append((tag, len(files)))

    total_safe_files = sum(n for _, n in safe)
    print(f"[clean_out_safe] {len(tag_files)} tag(s) with raw jsonl in {OUT}")
    print(f"  safe to clean:   {len(safe)} tag(s), {total_safe_files} file(s)")
    print(f"  NOT safe yet:    {len(unsafe)} tag(s)")
    for tag, n, why in sorted(unsafe)[:20]:
        print(f"    {tag:20} {n:4} file(s) — {why}")
    if len(unsafe) > 20:
        print(f"    ... and {len(unsafe) - 20} more")

    if not args.delete:
        print("\n[clean_out_safe] dry run — nothing deleted. Re-run with --delete to actually remove "
              "the safe set.")
        return 0

    removed = 0
    for tag, _ in safe:
        for fp in tag_files[tag]:
            fp.unlink()
            removed += 1
    print(f"\n[clean_out_safe] deleted {removed} file(s) across {len(safe)} tag(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
