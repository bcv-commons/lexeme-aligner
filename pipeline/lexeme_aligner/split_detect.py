"""Per language: should its editions be aligned by target word parts? Detect, generate, check, record (2026-11-28).

For one edition (or every edition of every language with a family template), in order:

  1. family     split_gen.family_of(iso): the config/split_families/<name>.json template whose `glottolog` names a node of the
                language's Glottolog 5.4 classification (bantu = narr1281 Narrow Bantu, tng = nucl1709 Nuclear Trans New Guinea).
                No template -> recorded as "no family template", nothing else runs.
  2. words arm  align-text on a sample of the edition's text (default: its NT books, else every book), whole target words, N runs
                (target_split_check.run_arm; whole source words, no pool, as the check needs).
  3. generate   split_gen.build_bundle from the words arm's own links (run 1) with the family template -> a bcv-segfacts bundle;
                split_export -> the edition's bcv-split file over its WHOLE text.
  4. parts arm  align-text on the same sample with that split, N runs.
  5. check      target_split_check.score: verdict from content_stable (parts - words): split / words / unclear.
  6. record     the split, the bundle and the check are kept in pipeline/generated-splits/<tag>/ (git-ignored: the split holds the
                edition's words, never published); config/target_parts.json gets an entry for the edition with `source:
                split_detect`, `local` + `sha256` of the split, `chain` = (verdict == split) and the check's figures. The chain
                (full_chain / target_parts.for_edition) then aligns that edition by parts at its next run; nothing is re-run here.

An entry not made by split_detect (a hand split: heb_scg, zin_wbt, kvg_wbt) is never overwritten or re-checked. An edition with a
split_detect entry is skipped unless --force (or --stale: its entry's template or generator changed).

    python -m lexeme_aligner.split_detect --edition TAG --iso ISO [--books MRK,ROM] [--runs 2] [--dry-run] [--force]
    python -m lexeme_aligner.split_detect --all [--isos zin,swh] [--list] [--workers 1] [--force | --stale]

State of a batch: pipeline/work/split-detect/state.json (resumable). Work folders pipeline/work/split-detect/<tag>/ are removed after a
finished edition unless --keep-work (the two arms of a whole NT are a few hundred MB).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "target_parts.json"
SPLITS = REPO / "pipeline" / "generated-splits"
WORK = REPO / "pipeline" / "work" / "split-detect"
USJ_CACHE = REPO / "pipeline" / "work" / "ingest-cache"
PA_MANIFEST = REPO / "publish" / "part-alignments" / "manifest.json"
SOURCE = "split_detect"
VERSION = "1"
NT_MIN = 20                     # an edition with at least this many NT books is sampled on its NT


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def template_info(family: str) -> dict:
    from lexeme_aligner import split_gen as SG
    f = SG.FAMILIES / f"{family}.json"
    return {"family": family, "template": str(f.relative_to(REPO)), "template_sha256": _sha(f), "split_gen": SG.VERSION,
            "split_detect": VERSION}


def sample_books(usj: Path, books: str | None) -> list[str]:
    from lexeme_aligner.run_pilot import NT_BOOKS
    have = sorted({p.stem.split("-", 1)[1] for p in usj.glob("*.json") if p.stem[:2].isdigit()})
    if books:
        want = [b.strip().upper() for b in books.split(",") if b.strip()]
        return [b for b in want if b in have]
    nt = [b for b in have if b in NT_BOOKS]
    return nt if len(nt) >= NT_MIN else have


def entry_of(tag: str) -> dict | None:
    return json.loads(CONFIG.read_text(encoding="utf-8")).get(tag) if CONFIG.exists() else None


def decide_skip(tag: str, iso: str, force: bool, stale: bool) -> str | None:
    """why this edition is not run, or None"""
    from lexeme_aligner import split_gen as SG
    e = entry_of(tag)
    if e and e.get("source") != SOURCE:
        return "has a hand-made or pinned split entry (never overwritten)"
    fam = SG.family_of(iso)
    if not fam:
        return "no family template"
    if not (USJ_CACHE / f"usj-{tag}").is_dir() or not any((USJ_CACHE / f"usj-{tag}").glob("*.json")):
        return "no ingested text"
    if e and not force:
        if stale and e.get("generator", {}).get("template_sha256") != template_info(fam)["template_sha256"]:
            return None
        return "already checked (use --force, or --stale after a template change)"
    return None


def write_entry(tag: str, iso: str, result: dict, split: Path, info: dict) -> None:
    from lexeme_aligner.manifest_io import update_json
    v = result["verdict"]

    def change(d: dict) -> dict:
        old = d.get(tag)
        if old and old.get("source") != SOURCE:          # a hand entry appeared meanwhile: leave it
            return d
        d[tag] = {
            "_doc": f"{dt.date.today().isoformat()}: generated by lexeme_aligner.split_detect (family {info['family']}); chain = the gold-free "
                    f"check's verdict ({v}). The split holds the edition's words: local only, never published.",
            "source": SOURCE, "chain": v == "split", "format": "bcv-split", "min_p_split": 0.9,
            "local": str(split.relative_to(REPO)), "sha256": _sha(split),
            "segmenter": f"lexeme_aligner.split_gen {info['split_gen']}, family template {info['template']} "
                         f"(sha256 {info['template_sha256'][:16]})",
            "numbering": "seg.S.split(written token, tones=True); part = list index (split_export)",
            "generator": info,
            "split_check": {k: result[k] for k in ("checked", "verdict", "delta", "figures", "thresholds", "books", "runs")},
        }
        return d
    update_json(CONFIG, change)


def detect(tag: str, iso: str, books: str | None = None, runs: int = 2, dry_run: bool = False, keep_work: bool = False,
           data: Path | None = None) -> dict:
    """One edition through steps 1-6; returns its result record."""
    from lexeme_aligner import split_export as SE
    from lexeme_aligner import split_gen as SG
    from lexeme_aligner import target_split_check as T
    fam = SG.family_of(iso)
    if not fam:
        return {"tag": tag, "iso": iso, "status": "no family template"}
    info = template_info(fam)
    usj_full = USJ_CACHE / f"usj-{tag}"
    sample = sample_books(usj_full, books)
    if not sample:
        return {"tag": tag, "iso": iso, "status": "no books to sample"}
    work = WORK / tag
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    usj = T._subset(usj_full, sample, work / "text")
    print(f"[split-detect] {tag} ({iso}, family {fam}): {len(sample)} books, {runs} runs", file=sys.stderr, flush=True)
    T.run_arm(usj, iso, tag, work / "words", runs, None, data)
    st = SG.build_bundle(iso, tag, usj, work / "words", work / "bundle", family=fam)
    out = SPLITS / tag
    tmp = SPLITS / f".{tag}.new"
    if tmp.exists():
        shutil.rmtree(tmp)
    ex = SE.export(tag, usj_full, work / "bundle", tmp / "split", f"lexeme_aligner.split_gen {SG.VERSION} (family {fam})",
                   "see the edition's licence pointer (local only)")
    if not ex.get("divided"):
        res = {"tag": tag, "iso": iso, "status": "generator cut nothing", "family": fam, "bundle": st}
        shutil.rmtree(tmp, ignore_errors=True)
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
        return res
    T.run_arm(usj, iso, tag, work / "parts", runs, tmp / "split" / "words.parquet", data)
    T.write_meta(work, tag, iso, usj_full, tmp / "split" / "words.parquet", ",".join(sample), runs)
    T.score(argparse.Namespace(out=work))
    result = json.loads((work / T.RESULT).read_text(encoding="utf-8"))
    shutil.copytree(work / "bundle", tmp / "bundle")
    shutil.copyfile(work / T.RESULT, tmp / T.RESULT)
    (tmp / "generator.json").write_text(json.dumps({**info, "bundle": st, "export": ex}, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    split = out / "split" / "words.parquet"
    # the check's record names the split where it now lives
    result["split"] = str(split)
    (out / T.RESULT).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not dry_run:
        write_entry(tag, iso, result, split, info)
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return {"tag": tag, "iso": iso, "status": "checked", "family": fam, "verdict": result["verdict"], "delta": result["delta"],
            "written": not dry_run, "split": str(split.relative_to(REPO))}


def editions(isos: list[str] | None = None) -> list[tuple[str, str]]:
    """[(tag, iso)] of every edition in the part-alignments manifest (optionally of these languages) whose language has a template"""
    from lexeme_aligner import split_gen as SG
    m = json.loads(PA_MANIFEST.read_text(encoding="utf-8"))["languages"]
    out = []
    for iso, l in sorted(m.items()):
        if isos and iso not in isos:
            continue
        if not SG.family_of(iso):
            continue
        for ed, e in sorted(l.get("editions", {}).items()):
            tag = e.get("tag") or ed
            if "+" not in ed and (tag, iso) not in out:
                out.append((tag, iso))
    return out


def _one(tag, iso, a) -> dict:
    try:
        return detect(tag, iso, a.books, a.runs, a.dry_run, a.keep_work, a.data)
    except BaseException as e:                       # SystemExit from align-text included: one edition must not stop the batch
        return {"tag": tag, "iso": iso, "status": "failed", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition", default=None, help="one edition tag (with --iso)")
    ap.add_argument("--iso", default=None)
    ap.add_argument("--all", action="store_true", help="every edition of every language with a family template")
    ap.add_argument("--isos", default=None, help="with --all: only these languages (comma-separated)")
    ap.add_argument("--list", action="store_true", help="with --all: print what would run and why others are skipped; run nothing")
    ap.add_argument("--books", default=None, help="sample books (comma-separated; default: the NT when it has >= 20 books, else all)")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="re-check editions that already have a split_detect entry")
    ap.add_argument("--stale", action="store_true", help="re-check split_detect entries made with another template version")
    ap.add_argument("--dry-run", action="store_true", help="keep the split and the check, but do not write config/target_parts.json")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--data", type=Path, default=None, help="align-text's data folder (default: its own)")
    a = ap.parse_args(argv)
    if a.edition:
        if not a.iso:
            ap.error("--edition needs --iso")
        why = decide_skip(a.edition, a.iso, a.force, a.stale)
        if why:
            print(f"[split-detect] {a.edition}: skipped: {why}", file=sys.stderr)
            return 0
        print(json.dumps(_one(a.edition, a.iso, a), ensure_ascii=False, indent=1))
        return 0
    if not a.all:
        ap.error("give --edition TAG --iso ISO, or --all")
    todo, skipped = [], {}
    for tag, iso in editions([x.strip() for x in a.isos.split(",")] if a.isos else None):
        why = decide_skip(tag, iso, a.force, a.stale)
        (skipped.setdefault(why, []).append(tag) if why else todo.append((tag, iso)))
    state_f = WORK / "state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {"done": {}}
    if not a.force and not a.stale:
        todo = [(t, i) for t, i in todo if state["done"].get(t, {}).get("status") not in ("checked", "no family template", "generator cut nothing")]
    print(f"[split-detect] to run: {len(todo)}; skipped: " + "; ".join(f"{k}: {len(v)}" for k, v in skipped.items()), file=sys.stderr)
    if a.list:
        for t, i in todo:
            print(f"{i}\t{t}")
        return 0
    WORK.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = {ex.submit(_one, t, i, a): t for t, i in todo}
        for f in as_completed(futs):
            r = f.result()
            state["done"][r["tag"]] = {k: v for k, v in r.items() if k != "trace"} | {"at": dt.datetime.now().isoformat(timespec="seconds")}
            tmp = state_f.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(state_f)
            print(f"[split-detect] {r['tag']}: {r['status']}" + (f" -> {r['verdict']} {r.get('delta')}" if "verdict" in r else "")
                  + (f" ({r['error']})" if "error" in r else ""), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
