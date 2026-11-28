"""Does an edition align better with its target words split into parts? A gold-free check per edition.

The chain can align a translation's words as PARTS (prefixes, stem, suffixes) from a split file (bcv-split words.parquet, or a
hebrew-nt-analysis words.parquet) pinned per edition in config/target_parts.json. Whether that helps differs by language
(2026-10: heb_scg clearly; Zinza a little; Kuni barely), so this check measures it on the edition's own text, no gold needed:

  run    align-text twice on the same text, whole words (OUT/words) and by parts (OUT/parts, --split), N runs each (eflomal is
         random). Both arms: whole source words (--no-source-parts: align-text cannot yet combine source parts with a split) and no
         pooled priors (--split turns the pool off, so the words arm does too: a fair pair, see the 2026-10-26 Wr lesson).
  score  per arm, averaged over runs, on the final links as compact resolves them (every method):
           content_stable   share of ALL source content tokens linked to the same target tokens in every run of the arm
           function_stable  the same for source function tokens
         and, reported only: content / function (share linked), stable (identical links / the arm's links), affix_own (parts arm:
         share of the split's affix parts a source token links by the part itself).
         Verdict (THRESHOLDS), from content_stable alone (parts - words):
           "split"   rises by >= CONTENT_GAIN;  "words"  rises by less than CONTENT_NOISE;  else "unclear".
         function_stable is reported, never a veto (owner, 2026-11-28): a split gives function words more places to link, and
         settling those links is the job of a later pass (Jev, or a free rule pass), not a reason to keep whole words.
         Writes OUT/split-check.json.
         Why these figures (2026-11-28, internal-docs/split-generator-plan-2026-11-28.md "Investigation"): over eight Arabic arms of
         known different quality scored against Clear gold, content_stable ranked the arms as gold F1 does (Spearman +0.98), the old
         headline "function linked" did not (-0.07: a placebo split with cuts at the wrong places raised it by 28 points), and the
         old "stable" (divided by the arm's own links) penalised a split for its extra function links. Thresholds fixed before the
         Finnish (HELFI) check, which is their held-out test; noise between two whole-word arms of the same text was 0.2 points.
  apply  copy a verdict into config/target_parts.json for an edition that already has an entry there: `chain` true (split) /
         false (words) + a `split_check` record. Never run by `run`/`score`: the production setting changes only when you ask.


    python -m lexeme_aligner.target_split_check run --usj DIR --split FILE --iso ISO --edition ED --out DIR [--runs 2] [--books MRK,ROM]
    python -m lexeme_aligner.target_split_check score --out DIR
    python -m lexeme_aligner.target_split_check apply --out DIR [--config config/target_parts.json]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULT = "split-check.json"
CONTENT_GAIN = 0.005       # "split" needs content_stable to rise by at least this (noise between two whole-word arms: 0.002)
CONTENT_NOISE = 0.002      # "words" when it rises by less than this
THRESHOLDS = {"rule": "content-stability-2026-11-28", "content_gain": CONTENT_GAIN, "content_noise": CONTENT_NOISE}
ARMS = ("words", "parts")


def verdict(words: dict, parts: dict) -> tuple[str, dict]:
    """("split" | "words" | "unclear", the deltas parts - words) from the two arms' averaged figures."""
    d = {k: round(parts[k] - words[k], 4) for k in ("content_stable", "function_stable", "content", "function", "stable") if k in words}
    if d["content_stable"] >= CONTENT_GAIN:
        return "split", d
    if d["content_stable"] < CONTENT_NOISE:
        return "words", d
    return "unclear", d


def _subset(usj: Path, books: list[str], dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for fp in usj.glob("*.json"):
        if fp.stem.split("-", 1)[-1] in books:
            shutil.copyfile(fp, dest / fp.name)
    for extra in ("_split.json",):
        if (usj / extra).exists():
            shutil.copyfile(usj / extra, dest / extra)
    if not any(dest.glob("*.json")):
        raise SystemExit(f"target_split_check: none of {','.join(books)} in {usj}")
    return dest


def run_arm(usj: Path, iso: str, edition: str, out: Path, runs: int, split: Path | None = None, data: Path | None = None) -> None:
    """One arm: align-text on `usj` into `out` (whole words, or by parts with `split`), `runs` runs, whole source words, no pool."""
    cmd = [sys.executable, "-m", "lexeme_aligner", "align-text", "--usj", str(usj), "--iso", iso, "--edition", edition,
           "--no-source-parts", "--no-pool", "--runs", str(runs), "--force", *(["--data", str(data)] if data else []),
           "--out", str(out), *(["--split", str(Path(split).resolve())] if split else [])]
    print(f"[split-check] {out.name}: {' '.join(cmd)}", file=sys.stderr, flush=True)
    if subprocess.run(cmd, cwd=REPO, env=os.environ.copy()).returncode != 0:
        raise SystemExit(f"target_split_check: align-text ({out.name}) failed; see {out / 'run.log'}")


def write_meta(out: Path, edition: str, iso: str, usj: Path, split: Path, books, runs: int) -> None:
    meta = {"edition": edition, "iso": iso, "usj": str(Path(usj).resolve()), "split": str(Path(split).resolve()), "books": books, "runs": runs}
    (out / "split-check-run.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


def run(args) -> int:
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    usj = args.usj.resolve()
    if args.books:
        usj = _subset(usj, [b.strip().upper() for b in args.books.split(",") if b.strip()], out / "text")
    for arm, split in (("words", None), ("parts", args.split)):
        run_arm(usj, args.iso, args.edition, out / arm, args.runs, split, args.data)
    write_meta(out, args.edition, args.iso, args.usj, args.split, args.books, args.runs)
    return score(argparse.Namespace(out=out))


def _final_links(edition: str, out_dir: Path, books: list[str], contest) -> dict:
    from lexeme_aligner.compact_align import METHODS, _merged_pairs
    links = {}
    for b in books:
        for (c, v), hs in _merged_pairs(edition, b, out_dir, METHODS, contest).items():
            for h, d in hs.items():
                links[(b, c, v, h)] = (tuple(d["t_idx"]), tuple(tuple(x) for x in (d.get("t_parts") or ())))
    return links


def _affixes(split: Path) -> set[tuple]:
    """{(book, chapter, verse, token, part)} of the split's affix parts (bcv-split slots; hebrew-nt-analysis has no slots: empty)."""
    import pyarrow.parquet as pq
    t = pq.read_table(split)
    if "slot" not in t.column_names:
        return set()
    return {(r["book"], r["chapter"], r["verse"], r["i"], r["part"]) for r in t.to_pylist()
            if r.get("slot") in ("prefix", "suffix", "infix")}


def measure(edition: str, usj: Path, run_dirs: list[Path], affixes: set | None = None) -> dict:
    """One arm's figures averaged over its runs (see the module docstring)."""
    from lexeme_aligner.compact_align import load_contest_rule
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    books = sorted({p.stem.split("-", 1)[1] for p in usj.glob("*.json") if p.stem[:2].isdigit()})
    recs = {(r.book, r.ch, r.v): r for r in build_corpus(books, usj, HebrewSource(), remapper(edition, str(usj)))}
    tot = collections.Counter("content" if t.is_content else "function" for r in recs.values() if r.toks for t in r.heb)
    contest = load_contest_rule()
    runs = [_final_links(edition, rd, books, contest) for rd in run_dirs]
    common = set.intersection(*({(k, v[0]) for k, v in ls.items()} for ls in runs)) if runs else set()
    acc = collections.defaultdict(list)
    same = collections.Counter()                         # source tokens linked to the same target tokens in every run
    for k, ts in common:
        rec = recs.get(k[:3])
        if rec is not None and k[3] < len(rec.heb):
            same["content" if rec.heb[k[3]].is_content else "function"] += 1
    for links in runs:
        c = collections.Counter()
        own = set()
        for (b, ch, v, h), (ts, parts) in links.items():
            rec = recs.get((b, ch, v))
            if rec is None or h >= len(rec.heb):
                continue
            c["content" if rec.heb[h].is_content else "function"] += 1
            c["stable"] += ((b, ch, v, h), ts) in common
            for i, pt in parts:
                if pt is not None:
                    own.add((b, rec.tref[0], rec.tref[1], i, pt))
        acc["content"].append(c["content"] / max(1, tot["content"]))
        acc["function"].append(c["function"] / max(1, tot["function"]))
        acc["stable"].append(c["stable"] / max(1, c["content"] + c["function"]))
        if affixes:
            acc["affix_own"].append(len(own & affixes) / len(affixes))
    return {k: round(sum(v) / len(v), 4) for k, v in acc.items()} | {
        "content_stable": round(same["content"] / max(1, tot["content"]), 4),
        "function_stable": round(same["function"] / max(1, tot["function"]), 4), "runs": len(runs)}


def _run_dirs(arm_dir: Path) -> list[Path]:
    return [arm_dir] + sorted((arm_dir / "runs").glob("run*")) if (arm_dir / "runs").is_dir() else [arm_dir]


def score(args) -> int:
    out = args.out.resolve()
    meta = json.loads((out / "split-check-run.json").read_text(encoding="utf-8"))
    rec = json.loads((out / "parts" / "align-text-run.json").read_text(encoding="utf-8"))
    usj = Path(rec["text"]["path"])
    figs = {"words": measure(meta["edition"], usj, _run_dirs(out / "words")),
            "parts": measure(meta["edition"], usj, _run_dirs(out / "parts"), _affixes(Path(meta["split"])))}
    v, d = verdict(figs["words"], figs["parts"])
    result = {**meta, "checked": dt.date.today().isoformat(), "figures": figs, "delta": d, "verdict": v, "thresholds": THRESHOLDS}
    (out / RESULT).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for arm in ARMS:
        f = figs[arm]
        print(f"{arm:5} ({f['runs']} runs): content stable {f['content_stable']:.1%}  function stable {f['function_stable']:.1%}  "
              f"| linked content {f['content']:.1%}  function {f['function']:.1%}  old stable {f['stable']:.1%}"
              + (f"  affix own {f['affix_own']:.1%}" if "affix_own" in f else ""))
    print(f"verdict: {v}  (parts - words: content stable {d['content_stable']:+.3f}, function stable {d['function_stable']:+.3f})")
    return 0


def apply(args) -> int:
    from lexeme_aligner.manifest_io import update_json
    res = json.loads((args.out.resolve() / RESULT).read_text(encoding="utf-8"))
    if res["verdict"] == "unclear":
        raise SystemExit(f"target_split_check: the verdict for {res['edition']} is unclear; decide by hand (figures in {args.out / RESULT})")
    cfg = Path(args.config)
    if res["edition"] not in json.loads(cfg.read_text(encoding="utf-8")):
        raise SystemExit(f"target_split_check: {res['edition']} has no entry in {cfg}; add its split pin first (see that file's entries)")

    def change(d: dict) -> dict:
        e = d[res["edition"]]
        e["chain"] = res["verdict"] == "split"
        e["split_check"] = {k: res[k] for k in ("checked", "verdict", "delta", "figures", "thresholds", "books", "runs")}
        return d
    update_json(cfg, change)
    print(f"{cfg}: {res['edition']} chain = {res['verdict'] == 'split'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--usj", type=Path, required=True, help="the edition's USJ folder")
    r.add_argument("--split", type=Path, required=True, help="its split file (words.parquet)")
    r.add_argument("--iso", required=True)
    r.add_argument("--edition", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--runs", type=int, default=2)
    r.add_argument("--books", default=None, help="only these books (comma-separated), e.g. a sample")
    r.add_argument("--data", type=Path, default=None, help="align-text's data folder (default: its own)")
    s = sub.add_parser("score")
    s.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--config", type=Path, default=REPO / "config" / "target_parts.json")
    args = ap.parse_args(argv)
    return {"run": run, "score": score, "apply": apply}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
