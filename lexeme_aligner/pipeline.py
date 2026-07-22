"""End-to-end driver — one pin-driven command per language:

    ingest (PKF or helloAO → USJ)  →  align (eflomal)  →  export (Parquet + manifest)  [→ publish]

Three text sources behind the same USJ seam (pick with --source):
  - `pkf`     — cdn.bibel.wiki PKF (589 langs; Node edge via pkf2usfm/); language keyed by --iso.
  - `helloao` — bible.helloao.org JSON (~1,256 translations, pure Python); needs --translation <id>.
  - `dbt`     — Faith Comes By Hearing's DBP v4 API (~1,488 langs with text); needs --bible-id <id>
    and a BIBLE_API_KEY (see .env, dbt_source.py docstring).

Each stage is the existing module CLI run in sequence, so every stage stays the single source of
truth for its own args/behaviour; this driver just wires them and stops on the first failure.

    python3 -m lexeme_aligner.pipeline --iso ind --lang-name Indonesian                 # PKF, OT
    python3 -m lexeme_aligner.pipeline --source helloao --translation swe_fol --iso swe \
            --lang-name Swedish --book RUT
    python3 -m lexeme_aligner.pipeline --iso ind --skip-ingest --publish bcv-commons/lexeme-alignments

Notes:
- helloAO ingest is scoped to the align books (a `--book RUT` run fetches only RUT, not 66 books).
- `export_lex` aggregates *every* align_<method>_<iso>_*.jsonl in $ALIGNER_OUT — so keep the align
  scope consistent across a language (a full `--ot` run, not a stray `--book`) or clear the out dir.
- Re-ingesting overwrites <usj-dir>; for a language previously built with a different book-number
  scheme, remove the old dir first so stale filenames don't linger.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT


def _run(mod: str, *args: object, env: dict) -> None:
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    print(f"\n\033[1m▶ {mod}\033[0m {' '.join(map(str, args))}", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[pipeline] stage '{mod}' failed (exit {e.returncode}) — aborting")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--source", choices=["pkf", "helloao", "dbt", "auto"], default="pkf",
                    help="text source (default pkf). auto = resolve via catalog_source.py's "
                         "cdn.bibel.wiki cross-source index (pkf/helloAO/DBT coverage) and dispatch "
                         "to whichever it recommends.")
    ap.add_argument("--translation", default=None, help="helloAO translation id (required for --source helloao), e.g. swe_fol")
    ap.add_argument("--bible-id", default=None, help="DBP bible_id (required for --source dbt), e.g. SPARVC")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--method", default="eflomal")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--ot", action="store_true", help="align the 39 OT books (default)")
    scope.add_argument("--nt", action="store_true", help="align the 27 NT books")
    scope.add_argument("--all", action="store_true", dest="whole",
                       help="whole Bible — align OT then NT (separate spines) in one run")
    scope.add_argument("--book", action="append", help="align specific book(s); repeatable")
    ap.add_argument("--usj-dir", type=Path, default=None, help="default data/usj-<iso>")
    ap.add_argument("--converter", type=Path, default=Path("pkf2usfm/export_usfm.mjs"))
    ap.add_argument("--spine-db", type=Path, default=None, help="sets ALIGNER_SPINE_DB for align+export")
    ap.add_argument("--publish", metavar="REPO_ID", default=None, help="HF dataset repo to push to")
    ap.add_argument("--create", action="store_true", help="create the HF repo if missing (with --publish)")
    ap.add_argument("--skip-ingest", action="store_true", help="USJ already present in --usj-dir")
    args = ap.parse_args()

    usj = args.usj_dir or Path(f"data/usj-{args.iso}")
    env = dict(os.environ)
    if args.spine_db:
        env["ALIGNER_SPINE_DB"] = str(args.spine_db)

    # 1) ingest — PKF, helloAO, or DBT → pin → USJ (skip if the text is already local)
    source, translation, bible_id = args.source, args.translation, args.bible_id
    if source == "auto":
        from lexeme_aligner.catalog_source import resolve
        from lexeme_aligner.run_pilot import OT_BOOKS, NT_BOOKS
        # --book is mutually exclusive with --nt/--ot, so testament must come from the actual
        # requested books when --book is used, not just args.nt (a bare `args.nt` check here would
        # silently resolve an NT --book request against OT gold).
        if args.book:
            requested = {b.upper() for b in args.book}
            testament = "nt" if requested & set(NT_BOOKS) else "ot"
        else:
            testament = "nt" if args.nt else "ot"
        plan = resolve(args.iso, testament)
        if plan is None:
            raise SystemExit(f"[pipeline] --source auto: '{args.iso}' not found in the "
                             f"cdn.bibel.wiki catalog for testament={testament}")
        if not plan["fetchable"]:
            raise SystemExit(f"[pipeline] --source auto: {plan['note']}")
        source = plan["source"]
        if source == "helloao":
            translation = plan["param"]
        elif source == "dbt":
            bible_id = plan["param"]
        print(f"[pipeline] --source auto resolved '{args.iso}' -> {source}"
              + (f" (translation={translation})" if translation else "")
              + (f" (bible_id={bible_id})" if bible_id else ""),
              file=sys.stderr)

    if args.skip_ingest:
        print(f"[pipeline] skip ingest — using existing {usj}", file=sys.stderr)
    elif source == "pkf":
        _run("cdn_source", "--iso", args.iso, "--to-usj", usj, "--converter", args.converter, env=env)
    elif source == "dbt":
        if not bible_id:
            raise SystemExit("[pipeline] --source dbt requires --bible-id (e.g. SPARVC)")
        from lexeme_aligner.run_pilot import OT_BOOKS, NT_BOOKS
        ingest_books = (OT_BOOKS + NT_BOOKS if args.whole
                        else [b.upper() for b in args.book] if args.book
                        else NT_BOOKS if args.nt else OT_BOOKS)
        book_args = [a for b in ingest_books for a in ("--book", b)]
        _run("dbt_source", "--bible-id", bible_id, "--iso", args.iso,
             "--to-usj", usj, *book_args, env=env)
    else:  # helloao — needs a translation id; scope the fetch to the align books
        if not translation:
            raise SystemExit("[pipeline] --source helloao requires --translation (e.g. swe_fol)")
        from lexeme_aligner.run_pilot import OT_BOOKS, NT_BOOKS
        ingest_books = (OT_BOOKS + NT_BOOKS if args.whole
                        else [b.upper() for b in args.book] if args.book
                        else NT_BOOKS if args.nt else OT_BOOKS)
        book_args = [a for b in ingest_books for a in ("--book", b)]
        _run("helloao_source", "--translation", translation, "--iso", args.iso,
             "--to-usj", usj, *book_args, env=env)

    # 2) align — eflomal over the chosen scope (default OT). Whole-Bible = two passes: OT (Hebrew
    # spine) then NT (Greek spine); they can't share one eflomal run. export aggregates both.
    if args.whole:
        passes: list[list[object]] = [["--ot"], ["--nt"]]
    elif args.nt:
        passes = [["--nt"]]
    elif args.book:
        passes = [[a for b in args.book for a in ("--book", b)]]
    else:
        passes = [["--ot"]]
    for scope_args in passes:
        _run("run_pilot", "--method", args.method, *scope_args, "--usj-dir", usj, "--iso", args.iso,
             *(["--lang-name", args.lang_name] if args.lang_name else []), env=env)

    # 3) export (+ optional publish) — jsonl → partitioned Parquet + manifest
    export_args: list[object] = ["--iso", args.iso, "--method", args.method]
    if args.lang_name:
        export_args += ["--lang-name", args.lang_name]
    if args.publish:
        export_args += ["--publish", args.publish]
        if args.create:
            export_args += ["--create"]
    _run("export_lex", *export_args, env=env)

    man = LEX_ROOT / "manifest.json"
    if man.exists():
        e = json.loads(man.read_text(encoding="utf-8")).get("languages", {}).get(args.iso, {})
        sources = e.get("sources") or {}
        src = "; ".join(f"{bt}→{s.get('license_url', '?')}" for bt, s in sources.items()) or "?"
        print(f"\n[pipeline] ✓ {args.iso}: {e.get('rows')} rows · {e.get('surfaces')} surfaces · "
              f"{e.get('strongs')} Strong's · license→{src}"
              + (f" · published to {args.publish}" if args.publish else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
