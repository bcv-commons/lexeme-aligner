"""Cross-edition gapfill walkthrough — runs `gapfill.py`'s cross_edition tier across every tag of
every currently-PUBLISHED language, skipping any tag that already has it, then re-exports whichever
languages actually changed. Never publishes (same as onboard.py/onboard_batch.py) — that stays a
separate, deliberate step.

Discovery: every language in lexeme-alignments/manifest.json is a candidate. For each, its pooled
edition list (and thus every tag) is reconstructed with the SAME onboard.editions_for()/_tag() logic
onboarding itself uses — so this can never drift out of sync with how a language was actually built,
same discipline as reverse_align_check.py sharing run_pilot.pooled_verse_groups() with build_corpus().

Staleness detection (NOT just "does gapfill output already exist") — a tag needs a re-run if ANY of:
  1. it has no gapfill output at all, or none of it carries a `"prior": "cross_edition"` pair (covers
     a run from before this tier existed in the code, whatever the timestamps say);
  2. its own `align_eflomal_<tag>` input is NEWER than its `align_gapfill_<tag>` output (the alignment
     it depends on changed since);
  3. the PUBLISHED `lexeme-alignments/iso=<primary>/data.parquet` — the actual cross-edition vocab
     source — is NEWER than this tag's gapfill output. This is the case a naive "has it run at all"
     check misses: a tag's cross-edition fill quality depends on how many sibling editions are
     currently pooled, so if the pool grew (a new edition added, or an existing one improved) AFTER
     this tag last ran gapfill, its fills reflect a smaller/older vocabulary than what's available
     now, even though it technically "already has" cross_edition data.

    python3 -m lexeme_aligner.gapfill_batch --dry-run
    python3 -m lexeme_aligner.gapfill_batch
    python3 -m lexeme_aligner.gapfill_batch --iso ind,por --force
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import LEX_ROOT, OUT
from lexeme_aligner.onboard import _tag, allowed_testaments, editions_for


def _mtime(fps: list[Path]) -> float | None:
    return max((fp.stat().st_mtime for fp in fps), default=None)


def has_cross_edition_gapfill(tag: str, out_dir: Path = OUT) -> bool:
    """True if align_gapfill_<tag>_*.jsonl already has at least one prior='cross_edition' pair."""
    for fp in tag_files(out_dir, "gapfill", tag):
        for line in fp.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if any(p.get("prior") == "cross_edition" for p in rec["pairs"]):
                return True
    return False


def has_eflomal(tag: str, out_dir: Path = OUT) -> bool:
    return bool(tag_files(out_dir, "eflomal", tag))


def needs_run(tag: str, primary_iso: str, out_dir: Path = OUT, lex_root: Path = LEX_ROOT) -> tuple[bool, str]:
    """(needs_run, reason) — see module docstring for the three staleness conditions."""
    gapfill_mtime = _mtime(tag_files(out_dir, "gapfill", tag))
    if gapfill_mtime is None or not has_cross_edition_gapfill(tag, out_dir):
        return True, "no cross_edition data yet"
    eflomal_mtime = _mtime(tag_files(out_dir, "eflomal", tag))
    if eflomal_mtime and eflomal_mtime > gapfill_mtime:
        return True, "eflomal re-run since last gapfill"
    parquet = lex_root / f"iso={primary_iso}" / "data.parquet"
    if parquet.exists() and parquet.stat().st_mtime > gapfill_mtime:
        return True, "published pool vocab updated since last gapfill (e.g. a sibling edition changed)"
    return False, "up to date"


def discover_tags(iso: str) -> list[tuple[str, bool]]:
    """[(tag, is_primary), ...] for a published language, reconstructed with the SAME logic
    onboarding used — see module docstring."""
    testaments = allowed_testaments(iso)
    if not testaments:
        return []
    eds = editions_for(iso, testaments)
    return [(_tag(iso, e["edition_code"], is_primary=(i == 0)), i == 0) for i, e in enumerate(eds)]


_LEGACY_TAGS_PATH = Path("config/legacy_bare_iso_tags.json")
# Manual overrides for tags whose USJ dir doesn't even match their OWN iso — pre-onboard.py legacy
# ingests (predating onboard.editions_for()'s discovery). hau: onboarded from eBible's OHCB (Hausa
# Contemporary Bible); its data lives in pipeline/work/ingest-cache/usj-hau-ohcb, not pipeline/work/ingest-cache/usj-hau (doesn't exist) or
# pipeline/work/ingest-cache/usj-hau_bib (hau's new edition_code-derived tag — still wrong, the real dir has its own name).
_USJ_DIR_MANUAL_OVERRIDES = {"hau_bib": "usj-hau-ohcb"}


def _usj_dir_overrides() -> dict[str, str]:
    """{tag: usj-dir-name} for every tag that was renamed off the bare-iso scheme (2026-07-25 —
    see data/legacy_bare_iso_tags.json) — their USJ text corpora were never renamed/moved, so the new
    edition_code-derived tag needs to keep pointing at the OLD `usj-<iso>` directory. Config/mapping
    update only, not a file move — reuses the same mechanism already built for hau's pre-existing case."""
    overrides = dict(_USJ_DIR_MANUAL_OVERRIDES)
    if _LEGACY_TAGS_PATH.exists():
        legacy = json.loads(_LEGACY_TAGS_PATH.read_text(encoding="utf-8"))["isos"]
        for iso, edition_code in legacy.items():
            # same slugify _tag() itself now does unconditionally (is_primary stopped affecting its
            # output on 2026-07-25 — see _tag()'s docstring). Inlined rather than calling _tag()
            # anyway, since this loop is slugifying a RECORDED historical edition_code from the
            # legacy table, not a live catalog lookup — no _tag() call to make either way.
            new_tag = "".join(c if c.isalnum() else "_" for c in edition_code.lower())
            if new_tag not in overrides:            # manual overrides win (hau's real dir isn't usj-hau_bib)
                overrides[new_tag] = f"usj-{iso}"
    return overrides


USJ_DIR_OVERRIDES = _usj_dir_overrides()


def _run(cmd: list, label: str) -> bool:
    print(f"  ▶ {label}", file=sys.stderr)
    result = subprocess.run(cmd)
    return result.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default=None, help="comma-separated isos to restrict to (default: every published language)")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, don't run anything")
    ap.add_argument("--force", action="store_true", help="re-run even tags that already have cross_edition fills")
    ap.add_argument("--manifest", type=Path, default=LEX_ROOT / "manifest.json")
    ap.add_argument("--usj-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    all_isos = sorted(manifest["languages"])
    isos = [i.strip() for i in args.iso.split(",")] if args.iso else all_isos

    print(f"[gapfill_batch] {len(isos)} language(s) to check", file=sys.stderr)

    plan: dict[str, list[tuple[str, bool, str]]] = {}   # iso -> [(tag, do_run, reason), ...]
    for iso in isos:
        tags = discover_tags(iso)
        todo = []
        for tag, is_primary in tags:
            if not has_eflomal(tag):
                continue   # edition never actually ingested/aligned (e.g. a known-dead one) — nothing to gapfill
            if args.force:
                do_run, reason = True, "--force"
            else:
                do_run, reason = needs_run(tag, iso)
            todo.append((tag, do_run, reason))
        if todo:
            plan[iso] = todo

    total_tags = sum(len(v) for v in plan.values())
    total_needing_run = sum(1 for v in plan.values() for _, do_run, _ in v if do_run)
    print(f"[gapfill_batch] {total_tags} tag(s) across {len(plan)} language(s), "
          f"{total_needing_run} need a run", file=sys.stderr)

    if args.dry_run:
        for iso, todo in plan.items():
            for tag, do_run, reason in todo:
                mark = "RUN" if do_run else "skip"
                print(f"  {iso:<8} {tag:<12} {mark:<5} ({reason})", file=sys.stderr)
        return 0

    for iso, todo in plan.items():
        for tag, do_run, reason in todo:
            if not do_run:
                continue
            usj_dir = args.usj_root / USJ_DIR_OVERRIDES.get(tag, f"usj-{tag}")
            _run([sys.executable, "-m", "lexeme_aligner.gapfill", "--iso", tag, "--all",
                 "--usj-dir", str(usj_dir), "--methods", "eflomal,gloss",
                 "--cross-edition-iso", iso], f"{iso}/{tag}")

    # Decide who needs (re-)export from disk state, NOT from "did gapfill run in this invocation" —
    # so a resumed run after a crash still exports languages whose gapfill is already fresh but whose
    # published parquet predates it (e.g. this exact recovery case).
    changed_isos = []
    for iso, todo in plan.items():
        tags = [t for t, _, _ in todo]
        gapfill_mtime = _mtime([fp for tag in tags for fp in tag_files(OUT, "gapfill", tag)])
        parquet = LEX_ROOT / f"iso={iso}" / "data.parquet"
        if gapfill_mtime and (not parquet.exists() or parquet.stat().st_mtime < gapfill_mtime):
            changed_isos.append(iso)

    print(f"\n[gapfill_batch] {len(changed_isos)} language(s) changed — re-exporting", file=sys.stderr)
    for iso in changed_isos:
        entry = manifest["languages"][iso]
        # re-derive, keeping only tags with actual align data — the catalog's ordering can list an
        # edition that was never ingested FIRST (live case: arb's catalog now leads with ARBASV, but
        # the ingested/aligned edition is arb_vdv), and exporting with a data-less --iso reads nothing.
        tags = [t for t, _ in discover_tags(iso) if has_eflomal(t)]
        if not tags:
            print(f"  !! {iso}: no data-bearing tags, skipping export", file=sys.stderr)
            continue
        primary, secondary = tags[0], tags[1:]
        cmd = [sys.executable, "-m", "lexeme_aligner.export_lex", "--iso", primary, "--publish-iso", iso]
        if secondary:
            cmd += ["--pool", ",".join(secondary)]
        lang_name = entry.get("language")
        if lang_name:
            cmd += ["--lang-name", lang_name]
        ok = _run(cmd, f"export {iso}")
        if ok:
            # the fresh parquet this export just wrote is BY DEFINITION in sync with every tag's
            # gapfill output that fed it — touch them forward so the next dry-run doesn't see the
            # export it itself just triggered as "pool updated since last gapfill" and loop forever.
            for tag in tags:
                for fp in tag_files(OUT, "gapfill", tag):
                    fp.touch()

    print(f"\n[gapfill_batch] done — {len(changed_isos)} language(s) re-exported locally. "
          f"Nothing published; that's still a separate, deliberate step.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
