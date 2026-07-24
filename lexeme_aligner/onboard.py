"""Single-language onboarding driver — ingest EVERY available edition of one language, align each,
and export one pooled `lexeme-alignments/iso=<iso>/` partition. Stops at export; publishing is a
separate, deliberate step (never run automatically here).

    python3 -m lexeme_aligner.onboard --iso ceb --lang-name Cebuano

Design (per project convention, see CLAUDE.local.md/DATA.md):
- **One language per invocation** — this is NOT a batch-all-2000 walker. Call it once per language
  you decide to bring in.
- **Whole Bible** — every edition is aligned with `run_pilot --all` (OT+NT in one pass; a missing
  testament for an NT-only or OT-only edition is skipped with a warning, not an error).
- **Pool ALL DISTINCT (not just fetchable) editions by default.** Absent an entry in
  `data/language_editions.json`, every fetchable edition the catalog knows about
  (`catalog_source.all_versions()`) is a candidate, but "distinct" isn't the same as "independent":
  same-text duplicates (`same_text_as` — the same translation hosted by two providers) are always
  merged, and — same rule applied by hand for `spa`/`por`/`eng`/`fra`, now the DEFAULT for every
  other language too — a pair classified `dialect_variant`/`orthography_convention`/`near_identical`
  (a near-duplicate REVISION of the same base translation, e.g. two hosted copies of the same Union
  Version) has one side dropped, deterministically, so it doesn't silently double-weight that
  translation family's readings in the pooled vote/share statistics (see `_drop_near_duplicates()`).
  Only genuinely `distinct_translation`/unclassified editions survive by default. A
  `data/language_editions.json` entry RESTRICTS to a hand-picked list instead (bypasses this
  filtering entirely — the list is taken as given) — see that file's `_doc` for the shape.
- **`data/language_exclusions.json` is checked first** — a fully-excluded language (`testament: all`)
  aborts immediately; a partially-excluded one (e.g. `heb`/`ot`) narrows the align scope to the
  remaining testament for every edition.
- **Stops at export.** No `--publish` is ever passed to `export_lex` — that stays a separate,
  deliberate command the user runs by hand once they've reviewed the result.
- **A pooled edition failing to ingest is logged and skipped, not fatal** — the rest of the language
  still onboards (e.g. a broken fileset on one DBT edition shouldn't sink five others that ingested
  fine). The PRIMARY edition failing DOES abort the language — there's no sensible anchor fallback.
- **`--lang-name` is a sanity check, not the source of truth.** Each adapter's pin now carries the
  *source's own* language name (PKF manifest's `nm`, helloAO's `languageEnglishName`, DBP's
  `language`) — that's what actually gets used, picked by source PRIORITY (pkf > helloao > dbt) when
  pooled editions disagree. If `--lang-name` was also given and it doesn't match (case-insensitive),
  a warning prints showing both, but the source-derived name still wins — never an error, and never
  the user-typed value overriding a name the source itself provided. `--lang-name` only supplies the
  final name outright when NO edition's pin carried one at all.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.catalog_source import PRIORITY, all_versions

_EXCLUSIONS = Path("data/language_exclusions.json")
_EDITIONS_CONFIG = Path("data/language_editions.json")


def _run(mod: str, *args: object, env: dict) -> None:
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    print(f"\n\033[1m▶ {mod}\033[0m {' '.join(map(str, args))}", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[onboard] stage '{mod}' failed (exit {e.returncode}) — aborting")


def _run_soft(mod: str, *args: object, env: dict) -> bool:
    """Like _run, but returns False instead of raising — for a POOLED (non-primary) edition's
    ingest, where one bad edition (a broken fileset, a transient 404, ...) shouldn't sink every
    other edition that already ingested fine."""
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    print(f"\n\033[1m▶ {mod}\033[0m {' '.join(map(str, args))}", file=sys.stderr)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"[onboard] WARNING: '{mod}' failed (exit {result.returncode}) for a pooled edition — "
              f"skipping it, continuing with the rest.", file=sys.stderr)
        return False
    return True


def allowed_testaments(iso: str, path: Path = _EXCLUSIONS) -> set[str]:
    """{'nt','ot'} minus whatever data/language_exclusions.json excludes for this iso. Empty ==
    fully excluded (caller should abort)."""
    allowed = {"nt", "ot"}
    if not path.exists():
        return allowed
    doc = json.loads(path.read_text(encoding="utf-8"))
    for rule in doc.get("exclude", []):
        if rule.get("iso") != iso:
            continue
        if rule.get("testament") == "all":
            return set()
        allowed.discard(rule.get("testament"))
    return allowed


def editions_for(iso: str, testaments: set[str], config_path: Path = _EDITIONS_CONFIG) -> list[dict]:
    """The list of {source, param, edition_code} to ingest for this language: either the
    data/language_editions.json restriction, or (default) every distinct fetchable edition the
    catalog knows about, pooled. Scoped to `testaments` (post-exclusion)."""
    if config_path.exists():
        doc = json.loads(config_path.read_text(encoding="utf-8"))
        entry = doc.get("editions", {}).get(iso)
        if entry:
            return entry["list"] if isinstance(entry, dict) else entry

    # NT and OT can disagree on whether an edition is a same-text GROUP MEMBER (`same_text_as` set)
    # or stands alone (`same_text_as` None) for the very same edition — live-verified on ind:
    # helloAO's ind_obo == pkf's INDPKF is flagged on NT but ind_obo looks standalone on OT. Deciding
    # canonicality from a single testament (whichever the set iterates first — non-deterministic,
    # str hash randomization) would let a real duplicate slip through as its own separate entry. So:
    # first pass, collect every raw record; an edition counts as a group MEMBER (deferred, not
    # canonical) if EITHER testament ever pointed it at something — a positive "this is a duplicate"
    # signal from one testament wins over silence on the other.
    records: list[dict] = []
    for testament in sorted(testaments):   # sorted: deterministic regardless of set iteration order
        for v in all_versions(iso, testament):
            if v["fetchable"]:
                records.append(v)
    points_at: dict[str, str] = {}   # own_key -> same_text_as, first non-None across either testament
    for v in records:
        own_key = f"{v['source']}:{v['edition_code']}"
        if v.get("same_text_as") is not None:
            points_at.setdefault(own_key, v["same_text_as"])

    # Second pass: a grouped record's `same_text_as` points at another SOURCE's id — only the
    # canonical record itself carries a source/param pair that's actually consistent with its own
    # key. Inserting a non-canonical record first would wrongly pair its OWN param (a helloAO
    # translation id) with the CANONICAL key's source (pkf) — e.g. calling cdn_source --iso ind_obo,
    # which doesn't exist on PKF. So: canonical records populate `seen` first; non-canonical ones
    # only fill a gap if the group's canonical record never showed up in either testament.
    seen: dict[str, dict] = {}
    deferred: dict[str, dict] = {}
    # key -> every (likely, closest_key) seen across BOTH testaments — NT and OT can disagree (e.g.
    # NT says distinct_translation, OT says dialect_variant, for the very same edition pair; live-
    # verified on ind's ind_ayt/INDASV) — _drop_near_duplicates() treats a pair as redundant if
    # EITHER testament flagged it (caution: a real similarity showed up somewhere, even if not
    # corroborated on both sides).
    classification: dict[str, list[tuple[str, str]]] = {}
    for v in records:
        own_key = f"{v['source']}:{v['edition_code']}"
        if own_key not in points_at:
            seen.setdefault(own_key, {"source": v["source"], "param": v["param"],
                                      "edition_code": v["edition_code"]})
        else:
            deferred.setdefault(points_at[own_key], v)
        if v.get("likely"):
            classification.setdefault(own_key, []).append((v["likely"], v.get("closest")))
    for key, v in deferred.items():
        if key not in seen:
            src, code = key.split(":", 1)
            seen[key] = {"source": src, "param": v["param"] or code, "edition_code": code}
    return _drop_near_duplicates(seen, classification)


_REDUNDANT_LIKELY = {"dialect_variant", "orthography_convention", "near_identical"}


def _drop_near_duplicates(seen: dict[str, dict], classification: dict[str, list[tuple[str, str]]]) -> list[dict]:
    """Default auto-pooling keeps every distinct FETCHABLE edition, but distinct isn't the same as
    independent — the catalog also classifies near-duplicate REVISIONS of the same base translation
    (`dialect_variant`/`orthography_convention`/`near_identical`, e.g. two hosted copies of the same
    Union Version, or a spelling-modernized revision) as separate editions since they're not
    literally the same text. Pooling both silently double-weights that one translation family's
    readings against genuinely independent ones in the vote/share statistics. Drop one side of each
    such pair — deterministic (sorted key order), so re-runs are stable; a pair only drops if BOTH
    ends are present in `seen` (an edition classified against something we don't have, e.g. a dead
    edition removed from the catalog, is never dropped on that basis alone).

    When a pair spans sources, prefer keeping the NON-dbt side. DBT has repeatedly shown data-quality
    issues this session that pkf/helloAO haven't (dead bible_ids — FRALSG, BENBIB; video-only
    editions masquerading as text bibles — PTGLPF, MALBIB; wrong fileset types). Live case that
    proved this matters: mal's `MALBIB` (video-only, dead) is the catalog's own designated "closest"
    reference for `helloao:mal_bib` (genuinely fetchable) — blindly keeping whichever side the
    classification data happened to point at would keep the dead one and drop the working one."""
    dropped: set[str] = set()
    for key in sorted(classification):
        if key in dropped or key not in seen:
            continue
        # any testament flagging this key redundant against a closest we actually have is enough —
        # first such verdict (sorted testament order already applied upstream) wins.
        verdict = next(((l, c) for l, c in classification[key]
                        if l in _REDUNDANT_LIKELY and c in seen and c not in dropped), None)
        if verdict is None:
            continue
        _, closest = verdict
        key_is_dbt, closest_is_dbt = seen[key]["source"] == "dbt", seen[closest]["source"] == "dbt"
        if key_is_dbt and not closest_is_dbt:
            dropped.add(key)
        elif closest_is_dbt and not key_is_dbt:
            dropped.add(closest)
        else:
            dropped.add(key)   # same source class either way — deterministic default: drop `key`
    return [v for k, v in seen.items() if k not in dropped]


def _tag(iso: str, edition_code: str, is_primary: bool) -> str:
    """Stable per-edition tag: the PRIMARY edition (the list's first entry — also `export_lex`'s
    partition name) always gets the bare `iso`; every other edition is tagged from its OWN edition
    code, not its position in the list. This matters: edition_code (a helloAO translation id or DBT
    bible_id) is a stable, globally-unique identifier, so adding/removing/reordering editions in
    `data/language_editions.json` never reassigns another edition's tag to a DIFFERENT edition's
    cached data — only the changed edition's own ingest+align needs to (re)run; the final
    `export_lex --pool` step just re-aggregates whichever tags are currently in play. An index-based
    scheme (spa_1, spa_2, ...) would NOT have this property — inserting/removing an entry mid-list
    would shift every later index onto someone else's cache."""
    if is_primary:
        return iso
    slug = "".join(c if c.isalnum() else "_" for c in edition_code.lower())
    return slug if slug != iso else f"{iso}_2"   # extremely unlikely collision, but stay safe


def _pin_path(tag: str) -> Path:
    return Path("data/pins") / f"{tag}.json"


def derive_lang_name(sources_by_tag: dict[str, str], pins: dict[str, Path]) -> str | None:
    """Read back each edition's pin and pick the language name by source PRIORITY (pkf > helloao >
    dbt) — the first source (in that order) whose pin actually carried a name wins, regardless of
    ingest order. None if no pin carried one at all."""
    by_source: dict[str, str] = {}
    for tag, src in sources_by_tag.items():
        pin_fp = pins[tag]
        if src in by_source or not pin_fp.exists():
            continue
        name = json.loads(pin_fp.read_text(encoding="utf-8")).get("language_name")
        if name:
            by_source[src] = name
    for src in PRIORITY:
        if src in by_source:
            return by_source[src]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--spine-db", type=Path, default=None)
    ap.add_argument("--skip-ingest", action="store_true", help="USJ already present for every edition")
    ap.add_argument("--exclusions", type=Path, default=_EXCLUSIONS)
    ap.add_argument("--editions-config", type=Path, default=_EDITIONS_CONFIG)
    args = ap.parse_args()

    testaments = allowed_testaments(args.iso, args.exclusions)
    if not testaments:
        raise SystemExit(f"[onboard] '{args.iso}' is fully excluded — see {args.exclusions}")
    scope_flag = "--all" if testaments == {"nt", "ot"} else f"--{next(iter(testaments))}"

    editions = editions_for(args.iso, testaments, args.editions_config)
    if not editions:
        raise SystemExit(f"[onboard] no fetchable edition found for '{args.iso}' "
                          f"(testaments={sorted(testaments)}) in the catalog")

    env = dict(os.environ)
    if args.spine_db:
        env["ALIGNER_SPINE_DB"] = str(args.spine_db)

    print(f"[onboard] '{args.iso}': {len(editions)} edition(s) to pool, scope={scope_flag} "
          f"({', '.join(e['edition_code'] for e in editions)})", file=sys.stderr)

    # pass 1: ingest every edition first (writes each pin) — the derived language name (below) needs
    # ALL pins read back before any alignment runs, since --lang-name feeds gloss's GlossPriors lookup.
    # The PRIMARY edition failing aborts the language (no sensible anchor fallback); a POOLED
    # edition failing is logged and skipped — the rest of the language still onboards.
    tags, pins, sources_by_tag, usj_dirs, skipped = [], {}, {}, {}, []
    for i, ed in enumerate(editions):
        is_primary = (i == 0)
        tag = _tag(args.iso, ed["edition_code"], is_primary=is_primary)
        usj = Path(f"data/usj-{tag}")
        pin = _pin_path(tag)

        if not args.skip_ingest:
            ingest_args = (["cdn_source", "--iso", ed["param"], "--to-usj", usj, "--pin", pin]
                            if ed["source"] == "pkf" else
                            ["helloao_source", "--translation", ed["param"], "--iso", tag,
                             "--to-usj", usj, "--pin", pin] if ed["source"] == "helloao" else
                            ["dbt_source", "--bible-id", ed["param"], "--iso", tag,
                             "--to-usj", usj, "--pin", pin] if ed["source"] == "dbt" else None)
            if ingest_args is None:
                raise SystemExit(f"[onboard] unknown source '{ed['source']}' for edition {ed}")
            if is_primary:
                _run(*ingest_args, env=env)   # raises + aborts on failure
            elif not _run_soft(*ingest_args, env=env):
                skipped.append(ed["edition_code"])
                continue

        tags.append(tag)
        sources_by_tag[tag] = ed["source"]
        pins[tag] = pin
        usj_dirs[tag] = usj

    if skipped:
        print(f"[onboard] skipped {len(skipped)} pooled edition(s) that failed to ingest: "
              f"{', '.join(skipped)}", file=sys.stderr)

    lang_name = derive_lang_name(sources_by_tag, pins)
    if lang_name and args.lang_name and lang_name.strip().lower() != args.lang_name.strip().lower():
        print(f"[onboard] WARNING: --lang-name '{args.lang_name}' doesn't match the source-derived "
              f"name '{lang_name}' — using the source-derived name.", file=sys.stderr)
    elif not lang_name:
        lang_name = args.lang_name
        if not lang_name:
            print("[onboard] WARNING: no edition's pin carried a language name, and --lang-name "
                  "wasn't given — the manifest's 'language' field will be null.", file=sys.stderr)

    # pass 2: align every edition now that lang_name is settled
    for tag in tags:
        _run("run_pilot", "--method", args.method, scope_flag, "--usj-dir", usj_dirs[tag], "--iso", tag,
             *(["--lang-name", lang_name] if lang_name else []), env=env)

    primary, pool = tags[0], tags[1:]
    export_args: list[object] = ["--iso", primary, "--method", args.method]
    if pool:
        export_args += ["--pool", ",".join(pool)]
    if lang_name:
        export_args += ["--lang-name", lang_name]
    _run("export_lex", *export_args, env=env)   # deliberately no --publish — see module docstring

    print(f"\n[onboard] ✓ '{args.iso}' exported ({len(tags)}/{len(editions)} edition(s) pooled"
          + (f", {len(skipped)} skipped" if skipped else "") + ") — "
          f"review lexeme-alignments/iso={primary}/ before publishing by hand.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
