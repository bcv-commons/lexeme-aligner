"""Resumable batch runner — the one tool for "run the chain over many languages" (2026-10-02).

Replaces `scripts/update_all.py` and the one-off batch scripts written during the fleet refresh (fleet_refresh, stem_555_batch, regen_post_batch,
repair_all_editions, run_catalog_sweeps): same ingredients every time — a queue of languages, N workers, `full_chain` per language, a state file so a
restart skips what is done, a free-space guard, a retry pass for failures, and the ledger entry written by the chain itself (full_chain step 9b).

    lexeme-aligner batch --all [--workers 3] [--nice 10] [--skip-ingest]              every onboarded language
    lexeme-aligner batch --stale-before 2026-09-28 --workers 3 --skip-ingest          languages whose partition is older than a date
    lexeme-aligner batch --list langs.json | --isos tgl,ind                           an explicit list
    ... --state FILE (default pipeline/work/logs/batch_state.json)  --fresh  --retry-failed  --min-free-gb 18  --no-ledger  --dry-run

Exit code 0 = every queued language done, 1 = some failed (listed in the state file), 2 = stopped because free space fell below the guard
(re-run the same command to resume). Nothing here publishes anything. Catalog onboarding (`batch --catalog`) stays `onboard_catalog`.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
DEFAULT_STATE = REPO / "pipeline/work/logs/batch_state.json"
LEX_MANIFEST = REPO / "publish/lexeme-alignments/manifest.json"


# ---- pure helpers (unit-tested) -------------------------------------------------------------------------------------------------
def select_languages(manifest_langs: dict, *, isos: list[str] | None = None, stale_before: float | None = None,
                     partition_mtime: Callable[[str], float | None] = lambda iso: None) -> list[str]:
    """The queue: explicit `isos`, else every language in the manifest; with `stale_before` only those whose partition is older. Smallest languages
    (fewest editions) first so the long ones do not block the early results; ties alphabetical."""
    base = list(isos) if isos is not None else sorted(manifest_langs)
    if stale_before is not None:
        base = [i for i in base if (partition_mtime(i) or 0.0) < stale_before]
    return sorted(base, key=lambda i: (len(manifest_langs.get(i, {}).get("base_texts", [])), i))


class State:
    """{started, done[], failed[]} written atomically after every language, so a killed run resumes where it stood."""

    def __init__(self, path: Path, fresh: bool = False):
        self.path = Path(path)
        self._lock = threading.Lock()
        doc = {} if fresh or not self.path.exists() else json.loads(self.path.read_text(encoding="utf-8"))
        self.started = doc.get("started") or time.strftime("%Y-%m-%dT%H:%M:%S")
        self.done: list[str] = list(doc.get("done", []))
        self.failed: list[str] = list(doc.get("failed", []))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"started": self.started, "done": self.done, "failed": self.failed}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def record(self, iso: str, ok: bool) -> None:
        with self._lock:
            (self.done if ok else self.failed).append(iso)
            if ok and iso in self.failed:
                self.failed.remove(iso)
            self._save()

    def reset_failed(self) -> None:
        with self._lock:
            self.failed = []
            self._save()


def free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / 1e9


def run_batch(isos: list[str], run_one: Callable[[str], bool], state: State, *, workers: int = 1, min_free_gb: float = 15.0,
              free: Callable[[], float] = free_gb, log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True)) -> int:
    """Run `run_one(iso)` for every queued language not already in the state. 0 = all ok, 1 = some failed, 2 = stopped on the free-space guard."""
    todo = [i for i in isos if i not in state.done and i not in state.failed]
    skipped = len(isos) - len(todo)
    log(f"[batch] {len(isos)} queued, {skipped} already in the state file, {len(todo)} to run, {workers} worker(s)")
    stop = threading.Event()
    queue_lock = threading.Lock()
    pending = list(todo)
    counter = {"n": 0}

    def worker() -> None:
        while not stop.is_set():
            with queue_lock:
                if not pending:
                    return
                if free() < min_free_gb:
                    log(f"[batch] free space {free():.0f} GB is below the guard ({min_free_gb:.0f} GB) — stopping; re-run to resume")
                    stop.set()
                    return
                iso = pending.pop(0)
                counter["n"] += 1
                n = counter["n"]
            t0 = time.time()
            try:
                ok = bool(run_one(iso))
            except Exception as e:                                       # noqa: BLE001 — one language must never take the batch down
                log(f"[batch] {iso}: {e!r}")
                ok = False
            state.record(iso, ok)
            log(f"[batch] {'OK  ' if ok else 'FAIL'} {n}/{len(todo)} {iso} ({time.time() - t0:.0f}s, free={free():.0f}G)")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for _ in range(max(1, workers)):
            ex.submit(worker)
    if stop.is_set():
        return 2
    return 1 if state.failed else 0


# ---- real runner ----------------------------------------------------------------------------------------------------------------
def load_env() -> None:
    """Load the repo's .env (BIBLE_API_KEY for DBT) like the Makefile does; a missing key makes a DBT ingest silently skip its edition."""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def make_runner(skip_ingest: bool, nice: int, ledger: bool, editions: dict[str, list[str]] | None = None) -> Callable[[str], bool]:
    """`editions` ({iso: [tag, ...]}): run only those editions' per-edition steps (full_chain --editions); the pooled exports still read all."""
    def run_one(iso: str) -> bool:
        cmd = [sys.executable, "-m", "lexeme_aligner.full_chain", "--iso", iso, "--clean-out",
               *(["--skip-ingest"] if skip_ingest else []), *([] if ledger else ["--no-ledger"]),     # the chain writes the ledger entry itself
               *(["--editions", ",".join(editions[iso])] if editions and editions.get(iso) else [])]
        rc = subprocess.run(cmd, cwd=str(REPO), preexec_fn=(lambda: os.nice(nice)) if nice else None).returncode
        return rc == 0
    return run_one


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all", action="store_true", help="every onboarded language")
    sel.add_argument("--list", type=Path, metavar="FILE", help="JSON list of languages")
    sel.add_argument("--isos", help="comma-separated languages")
    sel.add_argument("--stale-before", metavar="YYYY-MM-DD", help="onboarded languages whose lexeme-alignments partition is older than this date")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--nice", type=int, default=0, help="lower the priority of every chain (e.g. 10 beside other heavy jobs)")
    ap.add_argument("--skip-ingest", action="store_true", help="re-run on the cached text (no network)")
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--fresh", action="store_true", help="ignore the state file and start a new sweep")
    ap.add_argument("--retry-failed", action="store_true", help="run the languages the state file lists as failed again")
    ap.add_argument("--editions-file", type=Path, metavar="FILE",
                    help="JSON {iso: [edition tag, ...]}: for those languages run only the named editions' per-edition steps (a newly pooled edition)")
    ap.add_argument("--no-ledger", action="store_true", help="do not refresh the pipeline_decisions entry after each language")
    ap.add_argument("--dry-run", action="store_true", help="print the queue and exit")
    a = ap.parse_args(argv)

    manifest = json.loads(LEX_MANIFEST.read_text(encoding="utf-8")).get("languages", {})
    isos = None
    if a.list:
        isos = json.loads(a.list.read_text(encoding="utf-8"))
    elif a.isos:
        isos = [i.strip() for i in a.isos.split(",") if i.strip()]
    cut = calendar.timegm(time.strptime(a.stale_before, "%Y-%m-%d")) if a.stale_before else None

    def mtime(iso: str) -> float | None:
        f = REPO / f"publish/lexeme-alignments/iso={iso}/data.parquet"
        return f.stat().st_mtime if f.exists() else None

    queue = select_languages(manifest, isos=isos, stale_before=cut, partition_mtime=mtime)
    unknown = [i for i in queue if i not in manifest]
    if unknown:
        print(f"[batch] not onboarded yet (use `batch --catalog` or `run`): {unknown[:10]}", file=sys.stderr)
    queue = [i for i in queue if i in manifest]
    state = State(a.state, fresh=a.fresh)
    if a.retry_failed:
        state.reset_failed()
    if a.dry_run:
        todo = [i for i in queue if i not in state.done and i not in state.failed]
        print(f"[batch] dry run: {len(queue)} queued, {len(todo)} to run: {todo[:20]}{' ...' if len(todo) > 20 else ''}", file=sys.stderr)
        return 0
    load_env()
    ed_map = json.loads(a.editions_file.read_text(encoding="utf-8")) if a.editions_file else None
    return run_batch(queue, make_runner(a.skip_ingest, a.nice, not a.no_ledger, ed_map), state, workers=a.workers, min_free_gb=a.min_free_gb)


if __name__ == "__main__":
    raise SystemExit(main())
