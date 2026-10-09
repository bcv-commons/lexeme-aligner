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
  4b. span extension                     widen a name/noun eflomal+gloss span onto an adjacent,
                                          unclaimed case-marker/article — see span_extension.py's own
                                          docstring for the measured gain (hin/arb/eng gold, this
                                          session) and why it's narrowly gated, not a blind rule
  5. gapfill                             fills eflomal+gloss(+spanext) coverage gaps
  5b. residual align                     2nd eflomal pass on the remainder -> compact's opt-in layer
  6. export (final)                      re-aggregates eflomal+gloss+spanext+gapfill -> same partition
  7. aligned_mwe                         multi-word expressions (EVERY edition pooled, rows tagged by base_text)
  8. senses_attested                     OT/Hebrew sense attestation keyed on UBS sense ids (degrades to a no-op off-OT); the legacy BHSA-numbered scheme is retired
  9. compact-alignments                  per-book, content-addressed (one run per EDITION, all of them) —
                                          MAIN array includes spanext (compact_align.py's _resolve()
                                          fixed 2026-09-23 to let it win unconditionally, never
                                          relitigated by the eflomal/gloss contest rule)
                                          + <BOOK>_<hash>.extra.json — the opt-in residual layer
                                          (spanext does NOT belong in this layer — tried it, doesn't
                                          work: build_layer drops any entry the base array already
                                          covers, which is EVERY spanext entry by construction, since
                                          spanext only ever widens something already covered)

Steps 4-9 are individually best-effort (a failure prints a warning and the chain continues) EXCEPT
step 6's final export, which is load-bearing for everything published downstream. Step 4b is also
best-effort and silently no-ops for any language without Grambank coverage or without a phase-1-
flagged anomaly — see span_extension.py.

    python3 -m lexeme_aligner.full_chain --iso ceb --lang-name Cebuano
    python3 -m lexeme_aligner.full_chain --iso ceb --skip-ingest        # re-run the chain on cached text
    python3 -m lexeme_aligner.full_chain --iso ceb --clean-out          # + gzip its out/ jsonl once done
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.onboard import _EDITIONS_CONFIG, _EXCLUSIONS, _tag, allowed_testaments, editions_for

# spanext listed FIRST — export_lex/export_mwe are pure additive unions (every method's own rows kept,
# nothing overwritten) so order barely matters there; compact_align's _resolve() has spanext win any
# position it touches unconditionally regardless of list order (fixed 2026-09-23), but listing it first
# here keeps this one constant honest about priority for anything that DOES read it positionally.
_METHODS = "spanext,eflomal,gloss,gapfill"


def _has_text(usj_dir: Path) -> bool:
    """The edition's ingested text exists: the folder holds at least one book file. An empty folder (a Digital Bible Platform fileset that is audio-only or
    returned no text, e.g. WLOWTG) used to pass a plain `.exists()` check, got a compact run and left a 0-book edition in the manifest that blocked publishing."""
    return usj_dir.is_dir() and any(usj_dir.glob("*.json"))


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
    ap.add_argument("--editions", default=None,
                    help="comma-separated edition TAGS: run the per-edition steps (ingest, eflomal, gloss, spanext, gapfill, "
                         "residual, compact) only for these; the pooled steps still fold in every edition's existing files. "
                         "For a pilot or a targeted re-run. Default: all editions.")
    ap.add_argument("--no-ledger", action="store_true",
                    help="do not write this language's pipeline_decisions entry at the end (default: write it)")
    ap.add_argument("--clean-out", action="store_true",
                    help="gzip-compress (NOT delete — see the clean-out block below for why) this "
                         "language's out/ raw jsonl once every step succeeds")
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
    if args.editions:
        onboard_args += ["--editions", args.editions]
    if args.spine_db:
        onboard_args += ["--spine-db", args.spine_db]
    _run("onboard", *onboard_args, env=env)

    # re-derive the SAME tags onboard.py just used
    testaments = allowed_testaments(args.iso, args.exclusions)
    scope_flag = "--all" if testaments == {"nt", "ot"} else f"--{next(iter(testaments))}"
    editions = editions_for(args.iso, testaments, args.editions_config)
    tags = [_tag(args.iso, ed["edition_code"], is_primary=(i == 0)) for i, ed in enumerate(editions)]
    stat_of = {t: bool(ed.get("statistics", True)) for t, ed in zip(tags, editions)}     # statistics pool membership (onboard.editions_for)
    usj_dirs = {tag: Path(f"pipeline/work/ingest-cache/usj-{tag}") for tag in tags}
    all_tags = list(tags)
    tags = [t for t in tags if _has_text(usj_dirs[t])]   # a pooled edition onboard.py skipped has no usj dir; an edition whose fetch returned nothing has an EMPTY one
    if not tags:
        raise SystemExit(f"[full_chain] '{args.iso}': no tag survived ingest — see onboard's own output above")
    # There is NO privileged edition. `primary` is only the first tag in pool order (source priority pkf>helloao>dbt),
    # needed because export_lex/senses_attested take one `--iso` argument plus a `--pool` list; every step below
    # that produces a per-edition artifact (compact-alignments) runs once per tag, and every pooled dataset
    # (lexeme-alignments, aligned_mwe, senses_attested) folds ALL tags in, tagging rows by base_text.
    # Every edition (`tags`) is aligned and gets compact files. The pooled datasets and the votes use the STATISTICS set only: an edition
    # that is a spelling variant / near copy of one already in would count as a second independent witness (onboard.editions_for).
    stat_tags = [t for t in tags if stat_of.get(t, True)] or tags[:1]
    primary, pool = stat_tags[0], stat_tags[1:]
    only = {t.strip() for t in args.editions.split(",") if t.strip()} if args.editions else None
    run_tags = [t for t in tags if only is None or t in only]      # per-edition steps; pooled steps keep `tags`
    if not run_tags:
        if only & set(all_tags):    # in the pool but no text came through (audio/video-only fileset): nothing to align
            print(f"[full_chain] --editions {sorted(only)}: in the pool but ingested no text — nothing to do", file=sys.stderr)
            return 0
        raise SystemExit(f"[full_chain] --editions {sorted(only)}: none of them is in the pool {tags}")

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
    for tag in run_tags:
        _run("run_pilot", "--method", "gloss", scope_flag, "--usj-dir", usj_dirs[tag], "--iso", tag,
             "--publish-iso", args.iso,
             *(["--lang-name", lang_name] if lang_name else []), env=env, soft=True)

    # step 4b: span extension (see span_extension.py's own docstring) — reads eflomal+gloss's own
    # pairs, writes a SEPARATE align_spanext_<tag>_*.jsonl containing only the widened ones; never
    # touches the eflomal/gloss files themselves. Best-effort: no-ops cleanly (prints, exits 0) for a
    # language with no Grambank coverage or no phase-1-flagged anomaly, which is most languages.
    for tag in run_tags:
        _run("span_extension", "--iso", tag, "--publish-iso", args.iso, "--usj-dir", usj_dirs[tag],
             scope_flag, "--methods", "eflomal,gloss", env=env, soft=True)

    # step 5: gapfill (needs eflomal+gloss(+spanext) jsonl; fills coverage gaps) — --publish-iso is
    # essential here too, same reason as step 4's gloss call: the #3 stopword filter + #4 cross-edition
    # vocab must read/cache against the BARE iso's published data, not this tag's own key. spanext is
    # additive-only (a widened h_idx already in eflomal/gloss's own taken pool, never a new one), so
    # including it here just makes covered_h/taken_t correctly reflect the widened positions — safe by
    # construction (gapfill.load_covered unions t_idx across methods, never a first-wins overwrite).
    for tag in run_tags:
        _run("gapfill", "--iso", tag, "--publish-iso", args.iso, "--usj-dir", usj_dirs[tag], scope_flag,
             "--methods", "eflomal,gloss,spanext", env=env, soft=True)

    # step 5b: residual re-alignment — a second eflomal pass over only what eflomal+gloss(+spanext)
    # could not explain, with target stopwords/light renderings/already-taken positions stripped out.
    # It feeds compact-alignments' OPT-IN `.extra.json` layer only (step 9 emits it); no aggregated
    # dataset includes it, and the main compact array is unchanged by its presence.
    # Runs after gapfill because it stratifies against gap-fill's own fills (combine_with_gapfill).
    for tag in run_tags:
        _run("residual_align", "--iso", tag, "--publish-iso", args.iso, "--usj-dir", usj_dirs[tag],
             scope_flag, "--methods", "eflomal,gloss,spanext", env=env, soft=True)

    # step 6: final export — union of all three methods, same pooling onboard.py used for step 3
    export_args: list[object] = ["--iso", primary, "--publish-iso", args.iso, "--methods", _METHODS]
    if pool:
        export_args += ["--pool", ",".join(pool)]
    if lang_name:
        export_args += ["--lang-name", lang_name]
    _run("export_lex", *export_args, env=env)

    # step 7: aligned_mwe — every edition pooled, rows tagged by base_text (same shape as lexeme-alignments)
    _run("export_mwe", "--iso", primary, "--publish-iso", args.iso, "--method", _METHODS,
         *(["--pool", ",".join(pool)] if pool else []),
         *(["--lang-name", lang_name] if lang_name else []), env=env, soft=True)

    # step 8: senses_attested keyed on the UBS Dictionary of Biblical Hebrew's sense ids (OT-only; a no-op with a clear message off-OT or when
    # pipeline/ubs-senses.db has not been built). Separate CC BY-SA dataset root (publish/senses_attested; folder renamed from senses_attested_ubs 2026-10-05).
    # The LEGACY scheme (sense numbers from the BHSA-derived spine columns; CC BY-NC-SA, "superseded" on Hugging Face) is no longer produced by the
    # chain (2026-10-04, MACULA-only migration): in macula mode those numbers do not exist, and the dataset receives no further updates. It can still
    # be built explicitly with `senses_attested --scheme legacy` on a BHSA spine.
    senses_args: list[object] = ["--iso", primary, "--publish-iso", args.iso, "--method", _METHODS, "--scheme", "ubs"]
    if pool:
        senses_args += ["--pool", ",".join(pool)]
    if lang_name:
        senses_args += ["--lang-name", lang_name]
    _run("senses_attested", *senses_args, env=env, soft=True)

    # step 9: compact-alignments (per-book, content-addressed; ONE RUN PER EDITION — compact_align's manifest merge
    # keeps every edition under the language; local write, no HF push)
    # _METHODS (spanext included) for the MAIN array — safe now that compact_align.py's _resolve() lets
    # spanext win any position it touches unconditionally (fixed 2026-09-23; see that function's own
    # docstring). --layer-methods stays residual-only: tried adding spanext to the opt-in .extra.json
    # layer too and it's a dead end there — build_layer drops any entry the base array already covers,
    # which is every spanext entry by construction, so it can only ever land in the main array.
    for tag in run_tags:
        _run("compact_align", "--iso", tag, "--publish-iso", args.iso, "--usj-dir", usj_dirs[tag],
             "--methods", _METHODS, *(() if tag in stat_tags else ("--not-in-statistics-pool",)), env=env, soft=True)

    # step 9a: full alignments (fullalign_build.py) — reads the SAME align_*.jsonl compact_align just read and adds the full-align
    # channels (every row of every method, function words included) to compact's meta files, plus one layer per published gold source
    # (publish/full-alignments-manual[-sa]). A no-op with a message for an edition without a published gold layer, which is nearly all
    # of them for now (plan internal-docs/full-alignments-two-paths-plan-2026-10-09.md A1). Soft: a problem never stops the chain.
    for tag in run_tags:
        _run("fullalign_build", "--iso", tag, "--publish-iso", args.iso, "--usj-dir", usj_dirs[tag], env=env, soft=True)

    # step 9b: the language's pipeline_decisions ledger entry (config/pipeline_decisions.json) — always-on mechanisms, opt-in flags and the stemming
    # decision this run actually used. Before 2026-10-04 only the batch runner / ad-hoc scripts wrote it, so a chain run by hand left it stale. Best-effort;
    # the write is locked + atomic (several chains finish at once). Dataset-root copies are refreshed by publish_safe at publish time (--no-publish).
    if not args.no_ledger:
        _run("pipeline_decisions", *[x for t in tags for x in ("--tag", t)], "--publish-iso", args.iso,
             *[x for t in tags for x in ("--usj-dir", usj_dirs[t])], "--no-publish", env=env, soft=True)

    print(f"\n[full_chain] ✓ '{args.iso}' — full 9-step chain complete "
          f"({len(tags)} edition(s): {', '.join(tags)})", file=sys.stderr)

    if args.clean_out:
        import gzip
        import shutil
        from lexeme_aligner.align_files import tag_files_any_method
        from lexeme_aligner.config import OUT
        # tag_files_any_method (not a raw glob) deliberately, for the same reason align_files.py's
        # module docstring documents: a raw `align_*_{tag}_*.jsonl` glob also matches a SIBLING tag
        # that happens to start with `tag + "_"` (e.g. tag "ind" matching "ind_ayt"'s own files) — the
        # old delete-based version of this block had exactly that bug; fixed here as a byproduct of
        # switching to compression, since it's the same file-selection logic either way.
        #
        # COMPRESS rather than delete (2026-09-11): this used to unlink these files outright, on the
        # same "cheap to regenerate" theory the comment below already rejected for the ingested TEXT
        # back in 2026-07 — and it turned out just as wrong here. gapfill/compact-alignments/
        # aligned_mwe/senses_attested/export_lex all read straight from these jsonl; every retroactive
        # algorithm fix this project has shipped (bracket-stripping, the bonus channel, the tiered
        # cross-edition gapfill default) needed them again for languages already published, and
        # deleting them meant re-running eflomal+gloss from scratch for every one of those languages
        # just to re-run the one cheap step that actually changed. A full-Bible edition's raw jsonl
        # gzips to ~1/10th its ~190MB size (measured) — trivial to just keep. `tag_files`/
        # `tag_files_any_method` (align_files.py) already read `.jsonl.gz` transparently via
        # `AlignPath`, so nothing downstream needed to change to make this safe.
        compressed = 0
        for tag in tags:
            for fp in tag_files_any_method(Path(OUT), tag):
                if fp.suffix == ".gz":
                    continue                       # already compressed (e.g. a --skip-ingest re-run)
                with fp.open("rb") as src, gzip.open(fp.with_name(fp.name + ".gz"), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                fp.unlink()
                compressed += 1
        print(f"[full_chain] --clean-out: gzip-compressed {compressed} raw jsonl file(s) for "
              f"{', '.join(tags)}", file=sys.stderr)

        # the ingested target text (usj-<tag>) is intentionally KEPT for every source, not just DBT —
        # was previously deleted for PKF/helloAO on the theory that they're "cheap to refetch," but that
        # theory doesn't hold up at the scale this project actually operates at: a single retroactive
        # fix that needs to touch a few hundred already-processed languages turns "cheap per language"
        # into a genuinely costly bulk re-fetch (live case, 2026-07: fixing a stopword/morphology
        # caching bug needed re-fetching several languages' text purely because the cache was gone).
        # config/pins/<tag>.json already records a content sha256 for every fetch, which is exactly
        # what's needed to detect upstream drift on a deliberate future refresh pass WITHOUT needing to
        # keep blindly re-fetching in the meantime — see docs/refresh workflow (once built). Nothing
        # about this line is source-specific anymore; kept for a future eviction policy to hook into
        # (e.g. LRU by mtime) if disk pressure ever makes "keep everything forever" impractical.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
