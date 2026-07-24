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


# A tag's USJ dir is `usj-<tag>` for every language EXCEPT these — pre-onboard.py legacy ingests whose
# directory name doesn't match the tag used in their align_*_<tag>_*.jsonl output. hau: onboarded from
# eBible's OHCB (Hausa Contemporary Bible) before onboard.editions_for()'s helloAO discovery existed;
# its data lives in data/usj-hau-ohcb, not data/usj-hau (which doesn't exist).
USJ_DIR_OVERRIDES = {"hau": "usj-hau-ohcb"}


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
    ap.add_argument("--usj-root", type=Path, default=Path("data"))
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
        tags = [t for t, _ in discover_tags(iso)]   # re-derive; base_texts are edition labels, not tags
        primary, secondary = tags[0], tags[1:]
        cmd = [sys.executable, "-m", "lexeme_aligner.export_lex", "--iso", primary]
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
