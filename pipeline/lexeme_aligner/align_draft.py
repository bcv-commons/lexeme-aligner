"""Align a draft Bible book/testament/whole-Bible against an EXISTING language's published priors,
fully isolated from this repo's own shared work directory and published trees — the reusable core
behind `docs/generate-your-own-edition.md`'s hand-run recipe, now one call instead of four
CLI invocations a caller has to get exactly right themselves.

Runs the same chain `full_chain.py` runs for steps 4-6 (minus `export_lex`, which aggregates into
the shared `lexeme-alignments` dataset and has no business touching a private draft) plus step 9
(compact-alignments), each as a subprocess against a caller-chosen `--usj-dir` and a PRIVATE work
directory that never defaults to this repo's own `pipeline/work/out/` or `publish/compact-alignments/`
— every intermediate jsonl, the compact output, and its shared per-book lexeme index all live under
one `work_dir`, auto-created if not given.

eflomal/gloss/gapfill/residual are each best-effort (a failure is recorded as a warning, not fatal —
mirrors full_chain's own `soft=True` treatment of these same steps): a very short draft can still
produce a meaningful gloss-only alignment even if eflomal's own statistics are too sparse to fire.
Only compact_align itself is required to succeed, since it's the one step whose failure means there
is genuinely nothing to return.

    python3 -m lexeme_aligner.align_draft --usj-dir my_draft/usj --publish-iso swk --all
    python3 -m lexeme_aligner.align_draft --usj-dir my_draft/usj --publish-iso swk --book TIT --book PHM

Library use:
    from lexeme_aligner.align_draft import align_draft
    result = align_draft(Path("my_draft/usj"), "swk", scope="nt")
    result["report"]["coverage"]           # 0.0-1.0
    result["compact_dir"]                  # Path to the isolated compact-alignments-shaped output —
                                            # None if `cleanup=True` was requested (see below)

An auto-created `work_dir` is NOT deleted by default — same contract as `tempfile.mkdtemp`: the
caller owns it and gets real file paths back. Pass `cleanup=True` for a report-only, leave-nothing-
behind call (returns `compact_dir=None`/`books_written={}`; `report` is unaffected — it's a plain
dict, already fully read off disk before cleanup runs). An explicitly-passed `work_dir` is never
auto-deleted regardless of `cleanup` — it's assumed to be the caller's own, kept across calls on
purpose (e.g. re-aligning the same draft after a small text fix).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from lexeme_aligner.compact_align import edition_id
from lexeme_aligner.config import LEX_ROOT

_STEP_METHODS = "eflomal,gloss,gapfill"
_SOURCES_PATH = Path("config/sources.json")


def _run(mod: str, *args: object) -> tuple[bool, str]:
    """One subprocess step. Returns (ok, last stderr line) — never raises; the caller decides what a
    failure means for that particular step (see module docstring: only compact_align is fatal)."""
    cmd = [sys.executable, "-m", f"lexeme_aligner.{mod}", *map(str, args)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    last_line = ""
    if result.stderr.strip():
        last_line = result.stderr.strip().splitlines()[-1]
    return result.returncode == 0, last_line


def _scope_args(scope: str | None, books: list[str] | None) -> list[str]:
    if books:
        args: list[str] = []
        for b in books:
            args += ["--book", b]
        return args
    if scope in ("ot", "nt", "all"):
        return [f"--{scope}"]
    raise ValueError("pass either `books` (a list of book codes) or `scope` in {'ot','nt','all'}")


def check_priors_exist(publish_iso: str, lex_root: Path = LEX_ROOT) -> bool:
    """True if `publish_iso` already has published lexeme-alignments — the precondition that makes
    gloss's bootstrap priors meaningful rather than cold-start. Not enforced (a cold-start language
    still produces SOME alignment, just weaker gloss precision) — callers who want to fail fast on a
    genuinely new language should check this themselves; `align_draft` only warns."""
    manifest_fp = lex_root / "manifest.json"
    if not manifest_fp.exists():
        return False
    return publish_iso in json.loads(manifest_fp.read_text(encoding="utf-8")).get("languages", {})


def _build_report(compact_dir: Path, index_root: Path, books_written: dict[str, Path]) -> dict:
    """Coverage/confidence/gap summary computed purely from files `compact_align` just wrote — no
    extra pass against the spine needed. `gaps` lists (book, ref, lexeme) for every content lexeme
    that got no target-word slot at all — the "did you translate this?" signal a draft author cares
    about; capped at 200 entries (`gaps_truncated` says whether more exist) so a whole-Bible draft
    with poor coverage doesn't return an enormous payload by default."""
    total = aligned = hi_conf = contested = 0
    per_book: dict[str, dict] = {}
    gaps: list[list[str]] = []
    gaps_truncated = False

    for book, fp in sorted(books_written.items()):
        lexemes_fp = index_root / f"{book}_lexemes.json"
        if not lexemes_fp.exists():
            continue
        lexemes = json.loads(lexemes_fp.read_text(encoding="utf-8"))
        refs = list(lexemes.keys())
        array = json.loads(fp.read_text(encoding="utf-8"))
        meta_fp = fp.with_name(fp.name[:-len(".json")] + ".meta.json")
        meta = json.loads(meta_fp.read_text(encoding="utf-8")) if meta_fp.exists() else None

        b_total = b_aligned = b_hi = b_contested = 0
        for i, ref in enumerate(refs):
            n_content = len(lexemes[ref])
            if n_content == 0:
                continue
            b_total += n_content
            entry = array[i] if i < len(array) else ""
            aligned_ordinals: set[int] = set()
            if entry:
                for part in entry.split():
                    ordinal_str, _span = part.split(":", 1)
                    aligned_ordinals.add(int(ordinal_str))
            b_aligned += len(aligned_ordinals)
            if meta:
                conf_str = meta["conf"][i] if i < len(meta.get("conf", [])) else ""
                b_hi += sum(1 for c in conf_str if c.isdigit() and int(c) >= 2)
                contested_str = meta["contested"][i] if i < len(meta.get("contested", [])) else ""
                b_contested += len([p for p in contested_str.split() if p])
            for ordinal, lexeme in enumerate(lexemes[ref]):
                if ordinal not in aligned_ordinals:
                    if len(gaps) < 200:
                        gaps.append([book, ref, lexeme])
                    else:
                        gaps_truncated = True

        total += b_total
        aligned += b_aligned
        hi_conf += b_hi
        contested += b_contested
        per_book[book] = {
            "content_lexemes": b_total,
            "aligned": b_aligned,
            "coverage": round(b_aligned / b_total, 4) if b_total else None,
        }

    return {
        "content_lexemes": total,
        "aligned": aligned,
        "coverage": round(aligned / total, 4) if total else None,
        "hi_conf": hi_conf,
        "hi_conf_share_of_aligned": round(hi_conf / aligned, 4) if aligned else None,
        "contested_positions": contested,
        "per_book": per_book,
        "gaps": gaps,
        "gaps_truncated": gaps_truncated,
    }


def align_draft(
    usj_dir: Path,
    publish_iso: str,
    *,
    scope: str | None = "all",
    books: list[str] | None = None,
    tag: str | None = None,
    work_dir: Path | None = None,
    cleanup: bool = False,
    with_residual: bool = False,
    with_report: bool = True,
) -> dict:
    """Runs eflomal -> gloss -> gapfill [-> residual] -> compact_align against `usj_dir`, bootstrapping
    gloss from `publish_iso`'s already-published priors, entirely inside an isolated `work_dir` (a
    fresh temp directory if not given — see module docstring for the cleanup contract). Returns:

        {"tag": ..., "work_dir": Path, "compact_dir": Path | None, "index_dir": Path,
         "books_written": {book: Path}, "warnings": [...], "report": {...} | None}
    """
    usj_dir = Path(usj_dir)
    if not usj_dir.exists() or not any(usj_dir.glob("*.json")):
        raise FileNotFoundError(f"no USJ books found under {usj_dir}")

    scope_args = _scope_args(scope, books)
    caller_owns_work_dir = work_dir is not None
    work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="align_draft_"))
    out_dir = work_dir / "out"
    compact_dir = work_dir / "compact-alignments"
    index_dir = compact_dir / "_index"
    out_dir.mkdir(parents=True, exist_ok=True)

    tag = tag or f"{publish_iso}_draft_{uuid.uuid4().hex[:8]}"
    warnings: list[str] = []
    if not check_priors_exist(publish_iso):
        warnings.append(f"'{publish_iso}' has no published lexeme-alignments yet — gloss priors "
                         f"will be weak/absent (cold start), not this language's usual bootstrap")

    ok, msg = _run("run_pilot", "--method", "eflomal", *scope_args, "--usj-dir", usj_dir,
                   "--iso", tag, "--out", out_dir)
    if not ok:
        warnings.append(f"eflomal: {msg}")

    ok, msg = _run("run_pilot", "--method", "gloss", *scope_args, "--usj-dir", usj_dir,
                   "--iso", tag, "--publish-iso", publish_iso, "--out", out_dir)
    if not ok:
        warnings.append(f"gloss: {msg}")

    ok, msg = _run("gapfill", "--iso", tag, "--publish-iso", publish_iso, "--usj-dir", usj_dir,
                   *scope_args, "--methods", "eflomal,gloss", "--out", out_dir)
    if not ok:
        warnings.append(f"gapfill: {msg}")

    if with_residual:
        ok, msg = _run("residual_align", "--iso", tag, "--publish-iso", publish_iso,
                       "--usj-dir", usj_dir, *scope_args, "--methods", "eflomal,gloss",
                       "--out", out_dir)
        if not ok:
            warnings.append(f"residual: {msg}")

    ok, msg = _run("compact_align", "--iso", tag, "--publish-iso", publish_iso,
                   "--usj-dir", usj_dir, "--methods", _STEP_METHODS,
                   "--out-dir", out_dir, "--publish", compact_dir, "--index-root", index_dir)
    if not ok:
        if not caller_owns_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(f"compact_align failed — nothing to return ({msg})")

    # locate exactly the directory THIS call wrote — not "whatever's under publish_iso[0]/publish_iso",
    # which could hold other editions already if the caller passed a persistent `work_dir` and is
    # calling this repeatedly for the same publish_iso with different auto-generated tags
    sources = json.loads(_SOURCES_PATH.read_text(encoding="utf-8")) if _SOURCES_PATH.exists() else {}
    edition = edition_id(publish_iso, tag, sources)
    edition_dir = compact_dir / publish_iso[0] / publish_iso / edition
    if not edition_dir.exists():
        if not caller_owns_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("compact_align reported success but wrote no edition directory at "
                           f"{edition_dir} — check that --usj-dir actually contains the requested "
                           "book(s)")
    books_written = {fp.stem.rsplit("_", 1)[0]: fp
                     for fp in edition_dir.glob("*.json") if fp.name.count(".") == 1}

    report = _build_report(compact_dir, index_dir, books_written) if with_report else None

    result = {
        "tag": tag,
        "work_dir": work_dir,
        "compact_dir": edition_dir,
        "index_dir": index_dir,
        "books_written": books_written,
        "warnings": warnings,
        "report": report,
    }

    if cleanup and not caller_owns_work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
        result["compact_dir"] = None
        result["index_dir"] = None
        result["books_written"] = {}

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--publish-iso", required=True, help="the EXISTING language whose priors to bootstrap from")
    ap.add_argument("--ot", action="store_const", dest="scope", const="ot")
    ap.add_argument("--nt", action="store_const", dest="scope", const="nt")
    ap.add_argument("--all", action="store_const", dest="scope", const="all")
    ap.add_argument("--book", action="append", help="repeatable; overrides --ot/--nt/--all")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="default: a fresh temp dir, left on disk after (see --cleanup)")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the auto-created temp dir before exiting (report-only; no effect "
                         "with --work-dir, which is never auto-deleted)")
    ap.add_argument("--with-residual", action="store_true")
    ap.add_argument("--report-out", type=Path, default=None, help="write the JSON report here too")
    args = ap.parse_args()
    if not args.book and not args.scope:
        args.scope = "all"

    result = align_draft(args.usj_dir, args.publish_iso, scope=args.scope, books=args.book,
                         tag=args.tag, work_dir=args.work_dir, cleanup=args.cleanup,
                         with_residual=args.with_residual)

    for w in result["warnings"]:
        print(f"[align_draft] WARNING: {w}", file=sys.stderr)
    print(f"[align_draft] tag={result['tag']}  books={sorted(result['books_written'])}", file=sys.stderr)
    if result["report"]:
        r = result["report"]
        cov = f"{r['coverage']:.1%}" if r["coverage"] is not None else "n/a"
        hic = f"{r['hi_conf_share_of_aligned']:.1%}" if r["hi_conf_share_of_aligned"] is not None else "n/a"
        print(f"[align_draft] coverage {cov}  hi-conf {hic} of aligned  "
              f"contested {r['contested_positions']}  gaps {len(r['gaps'])}"
              f"{'+' if r['gaps_truncated'] else ''}", file=sys.stderr)
    if result["compact_dir"]:
        print(f"[align_draft] compact-alignment files at {result['compact_dir']}", file=sys.stderr)
    else:
        print("[align_draft] --cleanup was set — only the report was kept", file=sys.stderr)

    if args.report_out and result["report"]:
        args.report_out.write_text(json.dumps(result["report"], indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        print(f"[align_draft] report written to {args.report_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
