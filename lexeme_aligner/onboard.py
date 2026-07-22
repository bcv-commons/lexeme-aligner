"""Single-language onboarding driver — ingest EVERY available edition of one language, align each,
and export one pooled `lexeme-alignments/iso=<iso>/` partition. Stops at export; publishing is a
separate, deliberate step (never run automatically here).

    python3 -m lexeme_aligner.onboard --iso ceb --lang-name Cebuano

Design (per project convention, see CLAUDE.local.md/DATA.md):
- **One language per invocation** — this is NOT a batch-all-2000 walker. Call it once per language
  you decide to bring in.
- **Whole Bible** — every edition is aligned with `run_pilot --all` (OT+NT in one pass; a missing
  testament for an NT-only or OT-only edition is skipped with a warning, not an error).
- **Pool ALL available editions by default.** Absent an entry in `data/language_editions.json`, every
  distinct fetchable edition the catalog knows about (`catalog_source.all_versions()`, already
  de-duplicated by same-text grouping across pkf/helloAO/DBT) is ingested, aligned under its own
  scratch iso tag, and folded into one partition via `export_lex --pool`. A `data/language_editions.json`
  entry RESTRICTS to specific editions instead — see that file's `_doc` for the shape. (`eng`/`por`/`spa`
  are flagged for this kind of curation but not yet configured, pending the catalog producer's upcoming
  DBT-fingerprinting pass — until then their DBT-only editions carry no `likely` classification to
  curate against, so onboarding them today means pooling everything undifferentiated.)
- **`data/language_exclusions.json` is checked first** — a fully-excluded language (`testament: all`)
  aborts immediately; a partially-excluded one (e.g. `heb`/`ot`) narrows the align scope to the
  remaining testament for every edition.
- **Stops at export.** No `--publish` is ever passed to `export_lex` — that stays a separate,
  deliberate command the user runs by hand once they've reviewed the result.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.catalog_source import all_versions

_EXCLUSIONS = Path("data/language_exclusions.json")
_EDITIONS_CONFIG = Path("data/language_editions.json")


def _run(mod: str, *args: object, env: dict) -> None:
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    print(f"\n\033[1m▶ {mod}\033[0m {' '.join(map(str, args))}", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[onboard] stage '{mod}' failed (exit {e.returncode}) — aborting")


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

    seen: dict[str, dict] = {}
    for testament in testaments:
        for v in all_versions(iso, testament):
            if not v["fetchable"]:
                continue
            key = v.get("same_text_as") or f"{v['source']}:{v['edition_code']}"
            if key not in seen:
                src, code = key.split(":", 1)
                seen[key] = {"source": src, "param": v["param"] or code, "edition_code": code}
    return list(seen.values())


def _tag(iso: str, index: int) -> str:
    return iso if index == 0 else f"{iso}_{index}"


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

    tags = []
    for i, ed in enumerate(editions):
        tag = _tag(args.iso, i)
        tags.append(tag)
        usj = Path(f"data/usj-{tag}")

        if not args.skip_ingest:
            if ed["source"] == "pkf":
                _run("cdn_source", "--iso", ed["param"], "--to-usj", usj, env=env)
            elif ed["source"] == "helloao":
                _run("helloao_source", "--translation", ed["param"], "--iso", tag, "--to-usj", usj, env=env)
            elif ed["source"] == "dbt":
                _run("dbt_source", "--bible-id", ed["param"], "--iso", tag, "--to-usj", usj, env=env)
            else:
                raise SystemExit(f"[onboard] unknown source '{ed['source']}' for edition {ed}")

        _run("run_pilot", "--method", args.method, scope_flag, "--usj-dir", usj, "--iso", tag,
             *(["--lang-name", args.lang_name] if args.lang_name else []), env=env)

    primary, pool = tags[0], tags[1:]
    export_args: list[object] = ["--iso", primary, "--method", args.method]
    if pool:
        export_args += ["--pool", ",".join(pool)]
    if args.lang_name:
        export_args += ["--lang-name", args.lang_name]
    _run("export_lex", *export_args, env=env)   # deliberately no --publish — see module docstring

    print(f"\n[onboard] ✓ '{args.iso}' exported ({len(editions)} edition(s) pooled) — "
          f"review lexeme-alignments/iso={primary}/ before publishing by hand.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
