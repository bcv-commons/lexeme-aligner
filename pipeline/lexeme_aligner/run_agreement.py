"""Agreement between repeated chain runs as a confidence channel (2026-10-30, make-tanakh's request 4).

The chain is not deterministic (eflomal samples), so two runs on the same text differ. make-tanakh measured on Kuni Ruth that the links
both of two runs make match the published layer at precision 0.706, the links only one run makes at 0.232. `align-text --runs N` runs the
chain N times; this step writes, beside run 1's compact files, how many of the N runs made each of run 1's links:

    <BOOK>_<fp>.meta.json  "runs": ["322", "13", ...]   one digit per aligned entry of the main string, same order (like `conf`):
                                                        the number of runs (run 1 included, so 1..N, capped at 9) whose compact
                                                        string has exactly that entry (same source ordinal, same target span)
    manifest.json          editions[<edition>]["runs"] = N

Files of the same book are matched by name (same text -> same fingerprint name), verses by position (same _index).

    python -m lexeme_aligner.run_agreement --main OUT/compact --runs OUT/runs/run2/compact [OUT/runs/run3/compact ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def counts(main: list[str], others: list[list[str]]) -> list[str]:
    """Per verse of `main`: one digit per entry, 1 + the number of `others` (same verse) with the identical entry. Pure."""
    out = []
    for i, verse in enumerate(main):
        sets = [set(o[i].split()) if i < len(o) else set() for o in others]
        out.append("".join(str(min(9, 1 + sum(e in s for s in sets))) for e in verse.split()))
    return out


def _book_files(root: Path) -> dict[str, Path]:
    """{relative path: file} of every compact book file (not the sidecars) under a compact root."""
    return {str(p.relative_to(root)): p for p in root.glob("?/*/*/*.json")
            if not p.name.endswith((".meta.json", ".extra.json"))}


def _write(fp: Path, data) -> None:
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, fp)


def annotate(main_root: Path, run_roots: list[Path]) -> dict:
    """Add the `runs` channel to every book of `main_root` and `runs` to its manifest editions. Returns a summary: entries per count."""
    mains = _book_files(main_root)
    others = [_book_files(r) for r in run_roots]
    hist: dict[str, int] = {}
    missing = 0
    for rel, fp in sorted(mains.items()):
        main = json.loads(fp.read_text(encoding="utf-8"))
        lists = []
        for o in others:
            if rel in o:
                lists.append(json.loads(o[rel].read_text(encoding="utf-8")))
            else:
                missing += 1
                lists.append([])
        runs = counts(main, lists)
        for v in runs:
            for c in v:
                hist[c] = hist.get(c, 0) + 1
        meta_fp = fp.with_name(fp.name.removesuffix(".json") + ".meta.json")
        meta = json.loads(meta_fp.read_text(encoding="utf-8")) if meta_fp.exists() else {}
        meta["runs"] = runs
        _write(meta_fp, meta)
    man_fp = main_root / "manifest.json"
    if man_fp.exists():
        man = json.loads(man_fp.read_text(encoding="utf-8"))
        for lang in man.get("languages", {}).values():
            for ed in lang.get("editions", {}).values():
                ed["runs"] = 1 + len(run_roots)
        _write(man_fp, man)
    total = sum(hist.values())
    return {"books": len(mains), "runs": 1 + len(run_roots), "entries": total, "by_count": dict(sorted(hist.items())),
            "missing_book_files": missing, "all_runs_share": round(hist.get(str(min(9, 1 + len(run_roots))), 0) / total, 4) if total else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lexeme_aligner.run_agreement", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main", type=Path, required=True, help="run 1's compact root (gets the channel)")
    ap.add_argument("--runs", type=Path, nargs="+", required=True, help="the other runs' compact roots")
    a = ap.parse_args(argv)
    rep = annotate(a.main, a.runs)
    print(json.dumps(rep), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
