"""Re-ingest DBT editions in their canonical form (bibles' catalog/text-route.json) into a SCRATCH tree and measure what changes.

    .venv/bin/python pipeline/scripts/tools/dbt_reingest.py --out pipeline/work/dbt-reingest --tags aceibs,carbss
    .venv/bin/python pipeline/scripts/tools/dbt_reingest.py --out pipeline/work/dbt-reingest --all --workers 3

Per edition (a DBT pin in config/pins): `dbt_source.to_usj` into <out>/usj-<tag>/ (Sofria books as fetched, USX through bibles' converter,
plain verses as given), then a comparison with the current ingest cache: books new / only in the old / changed (text fingerprint), verses
whose tokens change. Nothing under pipeline/work/ingest-cache is touched; `--swap` (a separate, deliberate step) moves the changed
editions' new text into the cache and keeps the old text in <out>/old/. State is <out>/state.json (resumable per edition). The audio-sync
tree is never written (no write-back).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "pipeline")

CACHE = Path("pipeline/work/ingest-cache")
_lock = threading.Lock()


def dbt_pins() -> dict[str, dict]:
    pins = {}
    for f in sorted(Path("config/pins").glob("*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        if "dbt" in (p.get("provider") or "").lower() and p.get("bible_id"):
            pins[f.stem] = p
    return pins


def compare(new_dir: Path, old_dir: Path) -> dict:
    from lexeme_aligner import text_fingerprint as tf
    from lexeme_aligner.usj_source import tokenize
    t: collections.Counter = collections.Counter()
    examples = []
    old_names = {f.name for f in old_dir.glob("*.json")} if old_dir.is_dir() else set()
    new_names = {f.name for f in new_dir.glob("*.json")}
    t["books"] = len(new_names)
    t["books only in old"] = len(old_names - new_names)
    t["books new"] = len(new_names - old_names)
    for name in sorted(new_names & old_names):
        new, old = tf.usj_verses(new_dir / name), tf.usj_verses(old_dir / name)
        if tf.fingerprints(new)[0] == tf.fingerprints(old)[0]:
            continue
        t["books changed"] += 1
        for k in set(new) | set(old):
            a, b = tokenize(old.get(k, "")), tokenize(new.get(k, ""))
            if a != b:
                t["verses changed"] += 1
                if len(examples) < 3:
                    examples.append([name[3:6], list(k), " ".join(a)[:80], " ".join(b)[:80]])
    for name in sorted(new_names - old_names):
        t["verses in new books"] += len(tf.usj_verses(new_dir / name))
    return {**t, "examples": examples}


MIN_FREE_GB = 25.0


def run_one(tag: str, pin: dict, out: Path, state: dict, state_fp: Path, force: bool) -> None:
    if not force and state.get(tag, {}).get("status") == "done":
        return
    free = shutil.disk_usage(out).free / 1e9
    if free < MIN_FREE_GB:
        raise SystemExit(f"[dbt_reingest] only {free:.0f} GB free (< {MIN_FREE_GB:.0f}): stopping before {tag}; resume later")
    from lexeme_aligner import dbt_source as ds
    rec: dict = {"bible_id": pin["bible_id"], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    new_dir = out / f"usj-{tag}"
    try:
        if new_dir.exists():
            shutil.rmtree(new_dir)
        info = ds.bible_info(pin["bible_id"])
        picks = ds.text_filesets(info)
        rec["routes"] = {t: [r for r, _ in c] for t, c in picks.items()}
        result = ds.to_usj(pin["bible_id"], picks, new_dir, None, bare_iso=None)
        rec.update({"fetched": result["books"], "books_by_route": result["routes"], "missing": result["missing"]})
        rec["compare"] = compare(new_dir, CACHE / f"usj-{tag}")
        rec["status"] = "done"
    except SystemExit as e:
        rec.update(status="failed", error=str(e)[:300])
    except Exception as e:                                           # noqa: BLE001  one edition must not stop the sweep
        rec.update(status="failed", error=f"{type(e).__name__}: {str(e)[:300]}", trace=traceback.format_exc()[-600:])
    rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _lock:
        state[tag] = rec
        tmp = state_fp.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(state_fp)
    c = rec.get("compare", {})
    print(f"[dbt_reingest] {rec['status']:6s} {tag:12s} {rec.get('books_by_route', '')} missing={len(rec.get('missing', []))} "
          f"changed books={c.get('books changed', 0)} verses={c.get('verses changed', 0)} new books={c.get('books new', 0)}"
          + (f" ERROR {rec['error']}" if rec["status"] == "failed" else ""), file=sys.stderr, flush=True)


def swap(out: Path, state: dict, tags: list[str]) -> None:
    """Move the new text of every edition that CHANGED (or gained books) into the ingest cache; the old text goes to <out>/old/."""
    (out / "old").mkdir(exist_ok=True)
    moved = []
    for tag in tags:
        rec = state.get(tag, {})
        c = rec.get("compare") or {}
        if rec.get("status") != "done" or not (c.get("books changed") or c.get("books new")):
            continue
        cur, new = CACHE / f"usj-{tag}", out / f"usj-{tag}"
        if cur.exists():
            shutil.move(str(cur), str(out / "old" / cur.name))
        shutil.copytree(new, cur)
        moved.append(tag)
    print(f"[dbt_reingest] swapped {len(moved)} edition(s) into the ingest cache; old text in {out / 'old'}", file=sys.stderr)
    (out / "swapped.json").write_text(json.dumps(moved), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--tags")
    sel.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--swap", action="store_true", help="after the run, move changed editions' new text into the ingest cache")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    state_fp = a.out / "state.json"
    state = json.loads(state_fp.read_text(encoding="utf-8")) if state_fp.exists() else {}
    pins = dbt_pins()
    tags = [t.strip() for t in a.tags.split(",")] if a.tags else list(pins)
    unknown = [t for t in tags if t not in pins]
    if unknown:
        raise SystemExit(f"not DBT pins: {unknown}")
    todo = [t for t in tags if a.force or state.get(t, {}).get("status") != "done"]
    print(f"[dbt_reingest] {len(tags)} edition(s), {len(todo)} to run, {a.workers} worker(s)", file=sys.stderr)
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda t: run_one(t, pins[t], a.out, state, state_fp, a.force), todo))
    done = [t for t in tags if state.get(t, {}).get("status") == "done"]
    failed = [t for t in tags if state.get(t, {}).get("status") == "failed"]
    tot: collections.Counter = collections.Counter()
    for t in done:
        tot.update({k: v for k, v in state[t]["compare"].items() if isinstance(v, int)})
        tot.update({f"route {r}": n for r, n in state[t].get("books_by_route", {}).items()})
        tot["missing books"] += len(state[t].get("missing", []))
    print(f"[dbt_reingest] done {len(done)}, failed {len(failed)} {failed[:10]}; totals {dict(tot)}", file=sys.stderr)
    if a.swap:
        swap(a.out, state, tags)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
