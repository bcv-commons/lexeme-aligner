"""Batch onboarding driver — runs `onboard.py` once per language in a hand-authored JSON list.

This is NOT a full-catalog auto-walker (still not what onboard.py or this wraps is for) — the list
is exactly what you put in it, deliberately curated by the caller, same spirit as
data/language_editions.json.

    python3 -m lexeme_aligner.onboard_batch --spec data/onboard_batch_example.json
    python3 -m lexeme_aligner.onboard_batch --spec my_batch.json --dry-run

Spec shape:
    {"languages": ["hat", {"iso": "ceb", "lang_name": "Cebuano"}, {"iso": "swa", "method": "gloss"}]}
A bare string is iso-only (defaults apply). An object may override any onboard.py flag by name
(lang_name, method, spine_db, editions_config, exclusions) — dashes or underscores both work.

One language failing does NOT abort the batch — it's logged and the run continues; a pass/fail
summary prints at the end. `--dry-run` shows which editions each language would pool (and how many)
without fetching or aligning anything, so you can sanity-check cost before committing to a real run.
Never publishes — same as onboard.py, this only ever gets you to a reviewable export.

**Resumable by default.** A language counts as already done if `lexeme-alignments/iso=<iso>/
data.parquet` already exists — the artifact `export_lex` only writes once ingest+align+export ALL
succeeded, so this is a reliable "fully completed" signal straight from disk, not a log or a
separate state file (works the same whether the prior run finished, crashed, or was killed — no
bookkeeping to go stale). Killing a long batch mid-run and re-launching it with the SAME spec just
picks up where it left off: already-done languages are skipped instantly, the one that was mid-flight
when killed gets cleanly retried (dbt_source.py/helloao_source.py always fetch every requested book
fresh — an interrupted USJ dir is fully overwritten, not resumed-into, so partial state never mixes
with a retry). Pass `--force` to re-run everything regardless (e.g. after an edition config change).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT
from lexeme_aligner.onboard import allowed_testaments, editions_for


def already_exported(iso: str, root: Path = LEX_ROOT) -> bool:
    return (root / f"iso={iso}" / "data.parquet").exists()


def _normalize(entry: str | dict) -> dict:
    if isinstance(entry, str):
        return {"iso": entry}
    return {k.replace("-", "_"): v for k, v in entry.items()}


def load_spec(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [_normalize(e) for e in doc["languages"]]


def dry_run_plan(lang: dict, skip_existing: bool = True) -> dict:
    iso = lang["iso"]
    if skip_existing and already_exported(iso):
        return {"iso": iso, "status": "already-done", "editions": 0}
    exclusions = Path(lang.get("exclusions", "data/language_exclusions.json"))
    editions_config = Path(lang.get("editions_config", "data/language_editions.json"))
    testaments = allowed_testaments(iso, exclusions)
    if not testaments:
        return {"iso": iso, "status": "excluded", "editions": 0}
    eds = editions_for(iso, testaments, editions_config)
    return {"iso": iso, "status": "ok" if eds else "no-editions", "editions": len(eds),
             "testaments": sorted(testaments),
             "edition_codes": [e["edition_code"] for e in eds]}


def run_one(lang: dict) -> tuple[bool, str]:
    iso = lang["iso"]
    cmd = [sys.executable, "-m", "lexeme_aligner.onboard", "--iso", iso]
    flag_map = {"lang_name": "--lang-name", "method": "--method", "spine_db": "--spine-db",
                "editions_config": "--editions-config", "exclusions": "--exclusions",
                "skip_ingest": "--skip-ingest"}
    for key, flag in flag_map.items():
        if key not in lang:
            continue
        if key == "skip_ingest":
            if lang[key]:
                cmd.append(flag)
        else:
            cmd += [flag, str(lang[key])]

    print(f"\n{'=' * 70}\n[onboard_batch] ▶ {iso}\n{'=' * 70}", file=sys.stderr)
    t0 = time.monotonic()
    try:
        subprocess.run(cmd, check=True)
        return True, f"ok ({time.monotonic() - t0:.0f}s)"
    except subprocess.CalledProcessError as e:
        return False, f"FAILED (exit {e.returncode}, {time.monotonic() - t0:.0f}s)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true", help="show the editions plan per language, don't run anything")
    ap.add_argument("--force", action="store_true",
                    help="re-run EVERY language in the spec even if already exported")
    ap.add_argument("--force-iso", default=None,
                    help="comma-separated isos to re-run even if already exported, leaving skip-existing "
                         "on for everyone else in the spec — e.g. --force-iso amu,gor")
    args = ap.parse_args()
    skip_existing = not args.force
    force_isos = set(args.force_iso.split(",")) if args.force_iso else set()

    langs = load_spec(args.spec)
    print(f"[onboard_batch] {len(langs)} language(s) in {args.spec}"
          + ("" if args.force else " (skip-existing: on — already-exported languages are skipped)")
          + (f" (forcing: {', '.join(sorted(force_isos))})" if force_isos else ""),
          file=sys.stderr)

    if args.dry_run:
        total_editions, already_done = 0, 0
        for lang in langs:
            effective_skip = skip_existing and lang["iso"] not in force_isos
            plan = dry_run_plan(lang, effective_skip)
            total_editions += plan["editions"]
            already_done += plan["status"] == "already-done"
            print(f"  {plan['iso']:<8} {plan['status']:<14} {plan['editions']} edition(s) "
                  f"{plan.get('edition_codes', '')}")
        print(f"\n[onboard_batch] dry-run total: {total_editions} edition-ingests across "
              f"{len(langs)} language(s) ({already_done} already done, would be skipped)", file=sys.stderr)
        return 0

    results = []
    for lang in langs:
        iso = lang["iso"]
        effective_skip = skip_existing and iso not in force_isos
        if effective_skip and already_exported(iso):
            print(f"[onboard_batch] ⏭ {iso:<8} already exported — skipping", file=sys.stderr)
            results.append((iso, True, "skipped (already done)"))
            continue
        ok, note = run_one(lang)
        results.append((iso, ok, note))

    print(f"\n{'=' * 70}\n[onboard_batch] SUMMARY\n{'=' * 70}", file=sys.stderr)
    for iso, ok, note in results:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {iso:<8} {note}", file=sys.stderr)
    failed = [iso for iso, ok, _ in results if not ok]
    print(f"\n[onboard_batch] {len(results) - len(failed)}/{len(results)} succeeded"
          + (f" — FAILED: {', '.join(failed)}" if failed else ""), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
