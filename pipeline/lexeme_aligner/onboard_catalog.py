"""Full-catalog onboarding driver — walks EVERY language in the cdn.bibel.wiki cross-source catalog
(~1,876 languages across PKF/helloAO/DBT, see `catalog_source.py`), not a hand-curated spec list like
`onboard_batch.py` needs. Reuses `onboard_batch.py`'s exact per-language logic (`run_one`,
`already_exported`) and `onboard.py`'s exact edition-selection logic (`editions_for`, which already
respects `data/language_editions.json` where a language has one, else pools every distinct,
deduplicated edition the catalog knows about) — this script only supplies the language LIST, nothing
about how a language is onboarded changes.

Two-pass split by design (DBT is slower and less reliable than PKF/helloAO — verified live: a DBT
fetch can hang 60s+ before a clean timeout, vs PKF/helloAO's consistent sub-second-to-few-second
fetches):
  --include-dbt (default OFF) — a language is SKIPPED ENTIRELY (not partially onboarded with just its
  non-DBT editions) if ANY edition `editions_for()` would select for it is DBT-sourced. Run this pass
  first ("run #2") — fast, PKF/helloAO only.
  --include-dbt (ON) — no DBT filtering; run this AFTER the above ("run #3") — picks up exactly the
  languages the first pass deferred (everything else is already `already_exported()`, skipped
  automatically), pooling in their DBT edition(s) alongside any PKF/helloAO ones. Slower, unavoidably
  (real network calls to Faith Comes By Hearing's own API per book).

Fully resumable, same discipline as onboard_batch.py: a language counts as done once its
lexeme-alignments partition exists on disk — killing this mid-run and re-launching with the same flags
picks up exactly where it left off, no separate state file to go stale.

    python3 -m lexeme_aligner.onboard_catalog --dry-run                  # non-DBT plan (run #2)
    python3 -m lexeme_aligner.onboard_catalog                            # run #2, for real
    python3 -m lexeme_aligner.onboard_catalog --include-dbt --dry-run    # run #3 plan
    python3 -m lexeme_aligner.onboard_catalog --include-dbt              # run #3, for real
"""
from __future__ import annotations

import argparse
import sys

from lexeme_aligner.catalog_source import load as load_catalog
from lexeme_aligner.onboard import _EDITIONS_CONFIG, _EXCLUSIONS, allowed_testaments, editions_for
from lexeme_aligner.onboard_batch import already_exported, run_one


def all_catalog_isos() -> list[str]:
    """Every distinct iso the catalog knows about, across all testaments/sources."""
    index, _ = load_catalog()
    return sorted({e[0] for e in index["entries"]})


def build_plan(include_dbt: bool, exclusions=_EXCLUSIONS, editions_config=_EDITIONS_CONFIG) -> list[dict]:
    """[{"iso": iso}, ...] — every catalog language not yet onboarded, not fully excluded, with at
    least one edition, and (unless include_dbt) with NO DBT-sourced edition in its selected set."""
    plan = []
    for iso in all_catalog_isos():
        if already_exported(iso):
            continue
        testaments = allowed_testaments(iso, exclusions)
        if not testaments:
            continue
        eds = editions_for(iso, testaments, editions_config)
        if not eds:
            continue
        if not include_dbt and any(e["source"] == "dbt" for e in eds):
            continue
        plan.append({"iso": iso})
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-dbt", action="store_true",
                    help="include languages needing a DBT-sourced edition (run #3); default excludes "
                         "them entirely (run #2)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, don't run anything")
    ap.add_argument("--full", action="store_true",
                    help="run full_chain.py (ingest+eflomal+gloss+gapfill+export+aligned_mwe+"
                         "senses_attested+compact-alignments) instead of onboard.py's ingest+eflomal-only")
    args = ap.parse_args()

    plan = build_plan(args.include_dbt)
    print(f"[onboard_catalog] {len(plan)} language(s) to onboard (include_dbt={args.include_dbt})",
          file=sys.stderr)

    if args.dry_run:
        for lang in plan:
            print(f"  {lang['iso']}", file=sys.stderr)
        return 0

    results: dict[str, tuple[bool, str]] = {}
    for lang in plan:
        ok, msg = run_one(lang, full=args.full)
        results[lang["iso"]] = (ok, msg)
        print(f"  {'✓' if ok else '✗'} {lang['iso']:8} {msg}", file=sys.stderr)

    succeeded = [iso for iso, (ok, _) in results.items() if ok]
    failed = [iso for iso, (ok, _) in results.items() if not ok]
    print(f"\n[onboard_catalog] {len(succeeded)}/{len(plan)} succeeded"
          + (f" — FAILED: {failed}" if failed else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
