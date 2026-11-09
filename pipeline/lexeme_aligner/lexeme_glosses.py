"""Commonest renderings per lexeme, one small file per language (2026-11-04, for make-tanakh-review's gloss line).

    publish/lexeme-alignments/iso=<iso>/top.json
    {"format": "lexeme-glosses/1", "language": "dan", "revision": "<lexeme-aligner commit>", "built": "YYYY-MM-DD",
     "sources": ["dan_det"], "lexemes": {"hbo:0430": [["gud", 1755], ["guds", 567], ...], ...}}

Counted once per source-word occurrence, from the chain's FINAL decision per word: the same resolution compact-alignments
publishes (compact_align._resolve: span extension first, then the eflomal/gloss contest rule, then gap fill), read from the
edition's align_<method>_<tag>_<BOOK>.jsonl(.gz). Function tokens (prefixes, the article, אֵת ...) are counted too: the aligners
link them although the compact main array leaves them out. A rendering = the linked target words, lowercased, in text order, one
space between (a multi-word rendering stays one form). All editions of the language in the compact-alignments manifest are pooled
(`sources`). Trim per lexeme: most frequent first, at most 8 forms, forms seen once or under 5% of the lexeme's count dropped, but
the commonest form is always kept. `function` lists the lexemes counted as function tokens (the aligner's `content: false`);
their forms are less reliable: a token with no word of its own in the translation (אֵת) gets linked to a neighbouring word.
Counts only: no positions, no verse text.

    python -m lexeme_aligner.lexeme_glosses --iso dan [--iso eng ...] | --all [--workers N]
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.compact_align import _resolve, edition_tags, load_contest_rule
from lexeme_aligner.config import OUT

REPO = Path(__file__).resolve().parents[2]
LEX_ROOT = REPO / "publish/lexeme-alignments"
COMPACT_MANIFEST = REPO / "publish/compact-alignments/manifest.json"
METHODS = ("spanext", "eflomal", "gloss", "gapfill")          # = full_chain._METHODS, the compact main array's priority
FORMAT = "lexeme-glosses/1"
MAX_FORMS, MIN_SHARE = 8, 0.05


def rendering(p: dict) -> str | None:
    """The linked target words of a pair, lowercased, in text order."""
    t = (p.get("target") or "").strip()
    return " ".join(t.lower().split()) or None


def count_edition(tag: str, out_dir: Path = OUT, contest: dict | None = None, function: set | None = None) -> collections.Counter:
    """Counter((lexeme, rendering)) over every source-word occurrence of one edition that the chain linked. Lexemes of function
    tokens are added to `function` when given."""
    contest = load_contest_rule() if contest is None else contest
    files: dict[str, dict[str, Path]] = collections.defaultdict(dict)
    for m in METHODS:
        for fp in tag_files(out_dir, m, tag):
            book = Path(str(fp)).name.split(".")[0].rsplit("_", 1)[-1]
            files[book][m] = fp
    out: collections.Counter = collections.Counter()
    for book, by_m in files.items():
        raw: dict[tuple[int, int, int], dict[str, dict]] = {}
        for m, fp in by_m.items():
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                for p in rec["pairs"]:
                    if p.get("t_idx") and p.get("lexeme"):
                        raw.setdefault((rec["chapter"], rec["verse"], p["h_idx"]), {})[m] = p
        for mp in raw.values():
            win, _alt = _resolve(mp, METHODS, contest)
            if win is not None and (r := rendering(win)):
                out[(win["lexeme"], r)] += 1
                if function is not None and win.get("content") is False:
                    function.add(win["lexeme"])
    return out


def trim(counts: collections.Counter) -> dict[str, list[list]]:
    """{lexeme: [[form, count], ...]} most frequent first (ties by form), trimmed as the module docstring says."""
    by_lex: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for (lex, form), n in counts.items():
        by_lex[lex][form] += n
    out = {}
    for lex in sorted(by_lex):
        c = by_lex[lex]
        tot = sum(c.values())
        ranked = sorted(c.items(), key=lambda x: (-x[1], x[0]))
        keep = [ranked[0]] + [(f, n) for f, n in ranked[1:] if n >= 2 and n / tot >= MIN_SHARE]
        out[lex] = [[f, n] for f, n in keep[:MAX_FORMS]]
    return out


def editions_of(iso: str) -> list[tuple[str, str]]:
    """[(published edition id, align tag)] of the language in the compact-alignments manifest (compact_align.edition_tags)."""
    return sorted(edition_tags(iso, COMPACT_MANIFEST).items())


def revision() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build(iso: str, out_dir: Path = OUT, root: Path = LEX_ROOT, rev: str | None = None) -> dict:
    """Write root/iso=<iso>/top.json; returns a summary. Editions with no align files are left out of `sources`."""
    contest = load_contest_rule()
    total: collections.Counter = collections.Counter()
    sources = []
    function: set[str] = set()
    for ed, tag in editions_of(iso):
        c = count_edition(tag, out_dir, contest, function)
        if c:
            total += c
            sources.append(ed)
    if not total:
        return {"iso": iso, "skipped": "no align files"}
    lexemes = trim(total)
    doc = {"format": FORMAT, "language": iso, "revision": rev or revision(), "built": datetime.date.today().isoformat(),
           "sources": sources, "function": sorted(function & set(lexemes)), "lexemes": lexemes}
    d = root / f"iso={iso}"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "top.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, d / "top.json")
    return {"iso": iso, "sources": len(sources), "lexemes": len(lexemes), "occurrences": sum(total.values()),
            "bytes": (d / "top.json").stat().st_size}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lexeme_aligner.lexeme_glosses", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--iso", action="append", help="language (repeatable)")
    sel.add_argument("--all", action="store_true", help="every language in the compact-alignments manifest")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", type=Path, default=OUT, help="where the align jsonl files are")
    a = ap.parse_args(argv)
    isos = a.iso or sorted(json.loads(COMPACT_MANIFEST.read_text(encoding="utf-8"))["languages"])
    rev = revision()
    if a.workers > 1:
        with ProcessPoolExecutor(a.workers) as ex:
            for rep in ex.map(build, isos, [a.out] * len(isos), [LEX_ROOT] * len(isos), [rev] * len(isos)):
                print(json.dumps(rep), flush=True)
    else:
        for iso in isos:
            print(json.dumps(build(iso, a.out, rev=rev)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
