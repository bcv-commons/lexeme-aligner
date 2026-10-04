"""`lexeme-aligner` — the single front door to the pipeline (2026-10-02).

A thin dispatcher: every subcommand maps onto an entry point that already exists and is already tested, runs it as a subprocess, and passes every
remaining argument through unchanged. Nothing here re-implements a step, so the chain's behaviour is exactly what `full_chain` etc. do today.

    lexeme-aligner run tgl [--skip-ingest] [--clean-out ...]          one language, the 9-step chain  (= full_chain --iso tgl --clean-out)
    lexeme-aligner batch --catalog [--include-dbt]                    onboard every catalog language not yet done (= onboard_catalog --full)
    lexeme-aligner batch --list spec.json [--force]                   a hand-curated language list              (= onboard_batch)
    lexeme-aligner batch --all|--isos a,b|--stale-before DATE [--workers N --nice N --skip-ingest --fresh --retry-failed]
                                                                      resumable chain over many languages (= lexeme_aligner.batch)
    lexeme-aligner grammar derive|article-bound|gram-struct|check|all|regate   grammar facts; `regate` lists languages to re-run after a rebuild
    lexeme-aligner publish [--iso a,b | --ready-file F] [--push]      safe partial publish (= scripts/publish_safe.py; dry run unless --push)
    lexeme-aligner status                                             coverage report                          (= scripts/status.py)
    lexeme-aligner eval ...                                           positional gold scoring                   (= pos_score)
    lexeme-aligner text-strip ...                                     bracket/paren evidence report            (= scripts/text_strip_candidates.py)

`python -m lexeme_aligner ...` is the same thing. Run `lexeme-aligner <subcommand> --help` for that entry point's own options.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "pipeline" / "scripts"
_PY = sys.executable


def _module(name: str, *args: str) -> list[str]:
    return [_PY, "-m", f"lexeme_aligner.{name}", *args]


def _script(name: str, *args: str) -> list[str]:
    return [_PY, str(SCRIPTS / name), *args]


def _take(args: list[str], flag: str) -> tuple[bool, list[str]]:
    """(flag present, args without it) — for the few flags the CLI itself interprets."""
    return (flag in args), [a for a in args if a != flag]


def plan(argv: list[str]) -> list[list[str]]:
    """The commands (in order) a CLI invocation runs. Pure — the unit tests call this."""
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(__doc__)
    sub, rest = argv[0], argv[1:]
    if sub == "run":
        if not rest or rest[0].startswith("-"):
            raise SystemExit("usage: lexeme-aligner run ISO [--skip-ingest] [--lang-name NAME] [--no-clean-out]")
        iso, opts = rest[0], rest[1:]
        no_clean, opts = _take(opts, "--no-clean-out")
        return [_module("full_chain", "--iso", iso, *([] if no_clean else ["--clean-out"]), *opts)]
    if sub == "batch":
        catalog, rest = _take(rest, "--catalog")
        include_dbt, rest = _take(rest, "--include-dbt")
        every, rest = _take(rest, "--all")
        if catalog:
            return [_module("onboard_catalog", "--full", *(["--include-dbt"] if include_dbt else []), *rest)]
        if "--list" in rest:
            k = rest.index("--list")
            spec, rest = rest[k + 1], rest[:k] + rest[k + 2:]
            force, rest = _take(rest, "--force")
            return [_module("onboard_batch", "--spec", spec, "--full", "--clean-out", *(["--force"] if force else []), *rest)]
        if every:
            return [_module("batch", "--all", *rest)]
        if any(r in rest for r in ("--isos", "--stale-before")):
            return [_module("batch", *rest)]
        raise SystemExit("usage: lexeme-aligner batch (--catalog [--include-dbt] | --list FILE [--force] | --all | --isos a,b | --stale-before DATE) "
                         "[--workers N --nice N --skip-ingest --fresh --retry-failed --dry-run ...]")
    if sub == "grammar":
        what, rest = (rest[0], rest[1:]) if rest else ("", [])
        steps = {"regate": [_module("regate", *rest)],
                 "derive": [_module("derive_typology", "--build", *rest)],
                 "article-bound": [_module("article_bound", "--build", *rest)],
                 "gram-struct": [_module("gram_struct", "--build", *rest)],
                 "check": [_module("derive_typology", "--check-known-answers", *rest)]}
        if what == "all":     # order matters: derive_typology reads article_bound, gram_struct reads both
            return [_module("article_bound", "--build"), _module("derive_typology", "--build"), _module("gram_struct", "--build")]
        if what in steps:
            return steps[what]
        raise SystemExit("usage: lexeme-aligner grammar (derive | article-bound | gram-struct | check | regate | all) [options]")
    if sub == "publish":
        return [_script("publish_safe.py", *rest)]
    if sub == "status":
        return [_script("status.py", *rest)]
    if sub == "text-strip":
        return [_script("text_strip_candidates.py", *rest)]
    if sub == "eval":
        return [_module("eval.pos_score", *rest)]
    raise SystemExit(f"unknown subcommand '{sub}'. Subcommands: run, batch, grammar, publish, status, eval, text-strip")


def _exec(cmd: list[str]) -> int:
    return subprocess.call(cmd, cwd=str(REPO))


def main(argv: list[str] | None = None) -> int:
    for cmd in plan(list(sys.argv[1:] if argv is None else argv)):
        rc = _exec(cmd)
        if rc != 0:
            return rc
    return 0
