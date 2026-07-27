"""Full 9-step per-language pipeline chain: ingest, eflomal, export(eflomal-only), gloss, gapfill,
export(final union), aligned_mwe, senses_attested, compact-alignments. Every step writes LOCAL files
only (a few KB-MB each) — HF publish is a separate, deliberate step, never run automatically here (see
`export_lex --publish-all` / the Makefile's `publish`/`publish-all`/`publish-span-profile` targets).

Steps 1-3 are `onboard.py`, unmodified — this script shells out to it, then RE-DERIVES the exact same
tags via the same `editions_for()`/`_tag()` functions (deterministic: same inputs, same output) so
steps 4-9 operate on the identical edition set onboard.py just ingested and aligned. No duplicated
edition-discovery logic, no risk of drift between the two.

  1. ingest        (onboard.py)         fetch source text -> pipeline/work/ingest-cache/usj-<tag>
  2. eflomal align  (onboard.py)         base statistical pass
  3. export         (onboard.py)         eflomal-only -> publish/lexeme-alignments/iso=<iso>/ (LOCAL)
                                          — gloss's bootstrap priors read exactly this file next
  4. gloss align                         second pass, bootstrapped from step 3's local export
  5. gapfill                             fills eflomal+gloss coverage gaps
  6. export (final)                      re-aggregates eflomal+gloss+gapfill -> same partition
  7. aligned_mwe                         multi-word expressions (primary edition tag only)
  8. senses_attested                     OT/Hebrew sense attestation (degrades to a no-op off-OT)
  9. compact-alignments                  per-book, content-addressed (primary edition tag only)

Steps 4-9 are individually best-effort (a failure prints a warning and the chain continues) EXCEPT
step 6's final export, which is load-bearing for everything published downstream.

    python3 -m lexeme_aligner.full_chain --iso ceb --lang-name Cebuano
    python3 -m lexeme_aligner.full_chain --iso ceb --skip-ingest        # re-run the chain on cached text
    python3 -m lexeme_aligner.full_chain --iso ceb --clean-out          # + delete its out/ jsonl once done
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.onboard import _EDITIONS_CONFIG, _EXCLUSIONS, _tag, allowed_testaments, editions_for

_METHODS = "eflomal,gloss,gapfill"


def _run(mod: str, *args: object, env: dict, soft: bool = False) -> bool:
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    print(f"\n\033[1m▶ {mod}\033[0m {' '.join(map(str, args))}", file=sys.stderr)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        if soft:
            print(f"[full_chain] WARNING: '{mod}' failed (exit {result.returncode}) — continuing",
                  file=sys.stderr)
            return False
        raise SystemExit(f"[full_chain] stage '{mod}' failed (exit {result.returncode}) — aborting")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--spine-db", type=Path, default=None)
    ap.add_argument("--skip-ingest", action="store_true", help="USJ already present for every edition")
    ap.add_argument("--exclusions", type=Path, default=_EXCLUSIONS)
    ap.add_argument("--editions-config", type=Path, default=_EDITIONS_CONFIG)
    ap.add_argument("--clean-out", action="store_true",
                    help="delete this language's out/ raw jsonl once every step succeeds (opt-in — "
                         "leave off if you might want to re-derive aligned_mwe/senses_attested/"
                         "compact-alignments differently later without a full re-align)")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.spine_db:
        env["ALIGNER_SPINE_DB"] = str(args.spine_db)

    # steps 1-3: ingest + eflomal + eflomal-only export (onboard.py, unmodified/proven)
    onboard_args: list[object] = ["--iso", args.iso, "--method", "eflomal"]
    if args.lang_name:
        onboard_args += ["--lang-name", args.lang_name]
    if args.skip_ingest:
        onboard_args += ["--skip-ingest"]
    onboard_args += ["--exclusions", args.exclusions, "--editions-config", args.editions_config]
    if args.spine_db:
        onboard_args += ["--spine-db", args.spine_db]
    _run("onboard", *onboard_args, env=env)

    # re-derive the SAME tags onboard.py just used
    testaments = allowed_testaments(args.iso, args.exclusions)
    scope_flag = "--all" if testaments == {"nt", "ot"} else f"--{next(iter(testaments))}"
    editions = editions_for(args.iso, testaments, args.editions_config)
    tags = [_tag(args.iso, ed["edition_code"], is_primary=(i == 0)) for i, ed in enumerate(editions)]
    tag_source = {tag: ed["source"] for tag, ed in zip(tags, editions)}   # for --clean-out's DBT exception
    usj_dirs = {tag: Path(f"pipeline/work/ingest-cache/usj-{tag}") for tag in tags}
    tags = [t for t in tags if usj_dirs[t].exists()]   # a pooled edition onboard.py skipped has no usj dir
    if not tags:
        raise SystemExit(f"[full_chain] '{args.iso}': no tag survived ingest — see onboard's own output above")
    primary, pool = tags[0], tags[1:]

    # the language name onboard.py itself settled on (source-derived, priority pkf>helloao>dbt) — read
    # back from what step 3 just wrote, rather than re-deriving independently and risking drift
    lex_manifest_fp = Path("publish/lexeme-alignments/manifest.json")
    lang_name = args.lang_name
    if lex_manifest_fp.exists():
        entry = json.loads(lex_manifest_fp.read_text(encoding="utf-8")).get("languages", {}).get(args.iso, {})
        lang_name = entry.get("language") or lang_name

    # step 4: gloss (bootstraps from step 3's eflomal-only export, just written by onboard.py) —
    # --publish-iso is essential here: the bootstrap priors + #3 stopword filter must read/cache
    # against the BARE iso's published data (iso=<args.iso>/), not this tag's own jsonl key
    for tag in tags:
        _run("run_pilot", "--method", "gloss", scope_flag, "--usj-dir", usj_dirs[tag], "--iso", tag,
             "--publish-iso", args.iso,
             *(["--lang-name", lang_name] if lang_name else []), env=env, soft=True)

    # step 5: gapfill (needs eflomal+gloss jsonl; fills coverage gaps)
    for tag in tags:
        _run("gapfill", "--iso", tag, "--usj-dir", usj_dirs[tag], scope_flag,
             "--methods", "eflomal,gloss", env=env, soft=True)

    # step 6: final export — union of all three methods, same pooling onboard.py used for step 3
    export_args: list[object] = ["--iso", primary, "--publish-iso", args.iso, "--methods", _METHODS]
    if pool:
        export_args += ["--pool", ",".join(pool)]
    if lang_name:
        export_args += ["--lang-name", lang_name]
    _run("export_lex", *export_args, env=env)

    # step 7: aligned_mwe (primary tag only — this dataset's schema has no per-edition column)
    _run("export_mwe", "--iso", primary, "--publish-iso", args.iso, "--method", _METHODS,
         *(["--lang-name", lang_name] if lang_name else []), env=env, soft=True)

    # step 8: senses_attested (OT-only; degrades to a no-op print if this language has no OT/no senses)
    senses_args: list[object] = ["--iso", primary, "--publish-iso", args.iso, "--method", _METHODS]
    if pool:
        senses_args += ["--pool", ",".join(pool)]
    if lang_name:
        senses_args += ["--lang-name", lang_name]
    _run("senses_attested", *senses_args, env=env, soft=True)

    # step 9: compact-alignments (per-book, content-addressed; primary tag only; local write, no HF push)
    _run("compact_align", "--iso", primary, "--publish-iso", args.iso, "--usj-dir", usj_dirs[primary],
         "--methods", _METHODS, env=env, soft=True)

    print(f"\n[full_chain] ✓ '{args.iso}' — full 9-step chain complete "
          f"({len(tags)} edition(s): {', '.join(tags)})", file=sys.stderr)

    if args.clean_out:
        import shutil
        from lexeme_aligner.config import OUT
        removed = 0
        for tag in tags:
            for fp in Path(OUT).glob(f"align_*_{tag}_*.jsonl"):
                fp.unlink()
                removed += 1
        print(f"[full_chain] --clean-out: removed {removed} raw jsonl file(s) for {', '.join(tags)}",
              file=sys.stderr)

        # the ingested target text (usj-<tag>) is ALSO transient — EXCEPT for DBT-sourced editions,
        # whose fetch is slow/rate-limited (Faith Comes By Hearing's API), so re-fetching on a future
        # re-run would be genuinely costly. PKF/helloAO re-fetch is cheap, so their usj cache is safe
        # to drop once this language's chain (which is the only thing that still reads it) is done.
        kept_dbt = [t for t in tags if tag_source.get(t) == "dbt"]
        cleaned_usj = []
        for tag in tags:
            if tag_source.get(tag) == "dbt":
                continue
            if usj_dirs[tag].exists():
                shutil.rmtree(usj_dirs[tag], ignore_errors=True)
                cleaned_usj.append(tag)
        if cleaned_usj:
            print(f"[full_chain] --clean-out: removed usj cache for non-DBT tag(s): "
                  f"{', '.join(cleaned_usj)}", file=sys.stderr)
        if kept_dbt:
            print(f"[full_chain] --clean-out: KEPT usj cache for DBT-sourced tag(s) (slow to "
                  f"re-fetch): {', '.join(kept_dbt)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
